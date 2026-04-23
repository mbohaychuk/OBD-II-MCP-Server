"""Tool-layer test for `lookup_recalls_and_complaints`.

Verifies the tool stitches recalls + complaints from NHTSA into the
shape the MCP surface returns, and that a partial outage (e.g. the
complaints API down but recalls up) still returns something useful.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from obd_mcp.tools import lookup_recalls_and_complaints


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode())


_RECALL_PAYLOAD: dict[str, Any] = {
    "Count": 1,
    "results": [
        {
            "Manufacturer": "Ford Motor Company",
            "NHTSACampaignNumber": "25V164000",
            "parkIt": False,
            "parkOutSide": False,
            "overTheAirUpdate": False,
            "ReportReceivedDate": "14/03/2025",
            "Component": "POWER TRAIN",
            "Summary": "transmission valve body",
            "Consequence": "crash risk",
            "Remedy": "replace the valve body",
            "ModelYear": "2025",
            "Make": "FORD",
            "Model": "MUSTANG",
        }
    ],
}

_COMPLAINT_PAYLOAD: dict[str, Any] = {
    "count": 1,
    "results": [
        {
            "odiNumber": 11732601,
            "manufacturer": "Ford Motor Company",
            "crash": False,
            "fire": False,
            "numberOfInjuries": 0,
            "numberOfDeaths": 0,
            "dateOfIncident": "04/05/2026",
            "dateComplaintFiled": "04/21/2026",
            "vin": "1FA6P8TH2S5",
            "components": "EXTERIOR LIGHTING",
            "summary": "taillights failing",
            "products": [],
        }
    ],
}


def _both_ok(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "recalls/recallsByVehicle" in url:
        return _ok(_RECALL_PAYLOAD)
    if "complaints/complaintsByVehicle" in url:
        return _ok(_COMPLAINT_PAYLOAD)
    raise AssertionError(f"unexpected URL: {url}")


@pytest.mark.asyncio
async def test_lookup_recalls_and_complaints_happy_path() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_both_ok)) as http:
        result = await lookup_recalls_and_complaints(
            year=2025,
            make="Ford",
            model="Mustang",
            http_client=http,
        )

    assert result["year"] == 2025
    assert result["make"] == "Ford"
    assert result["model"] == "Mustang"
    assert len(result["recalls"]) == 1
    assert len(result["complaints"]) == 1
    assert result["recalls"][0]["campaign_number"] == "25V164000"
    assert result["complaints"][0]["odi_number"] == 11732601
    assert isinstance(result["timestamp"], float)


@pytest.mark.asyncio
async def test_lookup_recalls_and_complaints_partial_outage() -> None:
    """Recalls up, complaints down → recalls returned, complaints=[]."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "recalls/recallsByVehicle" in url:
            return _ok(_RECALL_PAYLOAD)
        return httpx.Response(503, content=b"service unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await lookup_recalls_and_complaints(
            year=2025, make="Ford", model="Mustang", http_client=http
        )

    assert len(result["recalls"]) == 1
    assert result["complaints"] == []


@pytest.mark.asyncio
async def test_lookup_recalls_and_complaints_full_outage() -> None:
    """Both endpoints down → both empty, no exception."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await lookup_recalls_and_complaints(
            year=2025, make="Ford", model="Mustang", http_client=http
        )

    assert result["recalls"] == []
    assert result["complaints"] == []
    # Identity fields still populated so the LLM can retry.
    assert result["make"] == "Ford"
