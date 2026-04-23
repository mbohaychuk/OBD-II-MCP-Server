"""Unit tests for the NHTSA recalls + complaints helper.

All HTTP traffic is routed through `httpx.MockTransport` so the tests
stay offline-safe and deterministic. Real NHTSA endpoint shapes were
captured from:

  GET https://api.nhtsa.gov/recalls/recallsByVehicle?make=ford&model=mustang&modelYear=2025
  GET https://api.nhtsa.gov/complaints/complaintsByVehicle?make=ford&model=mustang&modelYear=2025
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from obd_mcp.nhtsa import lookup_complaints, lookup_recalls


def _make_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode())


_RECALL_PAYLOAD: dict[str, Any] = {
    "Count": 1,
    "Message": "Results returned successfully",
    "results": [
        {
            "Manufacturer": "Ford Motor Company",
            "NHTSACampaignNumber": "25V164000",
            "parkIt": False,
            "parkOutSide": False,
            "overTheAirUpdate": False,
            "ReportReceivedDate": "14/03/2025",
            "Component": "POWER TRAIN:AUTOMATIC TRANSMISSION:FLUID/LUBRICANT",
            "Summary": "Ford is recalling certain 2024-2025 F-150, Mustang…",
            "Consequence": "Unexpected vehicle movement increases crash risk.",
            "Remedy": "Dealers will replace the transmission valve body.",
            "ModelYear": "2025",
            "Make": "FORD",
            "Model": "MUSTANG",
        }
    ],
}


_COMPLAINT_PAYLOAD: dict[str, Any] = {
    "count": 1,
    "message": "Results returned successfully",
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
            "summary": "Right taillights intermittently fail…",
            "products": [
                {
                    "type": "Vehicle",
                    "productYear": "2025",
                    "productMake": "FORD",
                    "productModel": "MUSTANG",
                    "manufacturer": "Ford Motor Company",
                }
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_lookup_recalls_extracts_campaign_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok(_RECALL_PAYLOAD)

    async with _make_client(handler) as client:
        recalls = await lookup_recalls("Ford", "Mustang", 2025, client=client)

    assert len(recalls) == 1
    r = recalls[0]
    assert r["campaign_number"] == "25V164000"
    assert r["component"].startswith("POWER TRAIN")
    assert "Ford Motor Company" in r["manufacturer"]
    assert r["park_it"] is False
    assert r["park_outside"] is False
    assert r["ota_update"] is False
    assert r["report_received_date"] == "14/03/2025"
    assert "transmission" in r["remedy"].lower()

    assert len(requests) == 1
    url = requests[0].url
    assert url.params["make"] == "Ford"
    assert url.params["model"] == "Mustang"
    assert url.params["modelYear"] == "2025"
    assert "recalls/recallsByVehicle" in str(url)


@pytest.mark.asyncio
async def test_lookup_recalls_network_failure_returns_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with _make_client(handler) as client:
        assert await lookup_recalls("Ford", "Mustang", 2025, client=client) == []


@pytest.mark.asyncio
async def test_lookup_recalls_http_500_returns_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    async with _make_client(handler) as client:
        assert await lookup_recalls("Ford", "Mustang", 2025, client=client) == []


@pytest.mark.asyncio
async def test_lookup_recalls_empty_results_is_empty_list() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok({"Count": 0, "Message": "No results found", "results": []})

    async with _make_client(handler) as client:
        assert await lookup_recalls("Nissan", "Leaf", 1998, client=client) == []


@pytest.mark.asyncio
async def test_lookup_complaints_extracts_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok(_COMPLAINT_PAYLOAD)

    async with _make_client(handler) as client:
        complaints = await lookup_complaints("Ford", "Mustang", 2025, client=client)

    assert len(complaints) == 1
    c = complaints[0]
    assert c["odi_number"] == 11732601
    assert c["component"] == "EXTERIOR LIGHTING"
    assert c["crash"] is False
    assert c["fire"] is False
    assert c["injuries"] == 0
    assert c["deaths"] == 0
    assert c["incident_date"] == "04/05/2026"
    assert c["filed_date"] == "04/21/2026"
    assert "taillights" in c["summary"].lower()
    # VIN / products / manufacturer fields are intentionally dropped —
    # they're noise for the LLM consumer.
    assert "vin" not in c
    assert "products" not in c

    assert len(requests) == 1
    assert "complaints/complaintsByVehicle" in str(requests[0].url)


@pytest.mark.asyncio
async def test_lookup_complaints_network_failure_returns_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    async with _make_client(handler) as client:
        assert await lookup_complaints("Ford", "Mustang", 2025, client=client) == []


@pytest.mark.asyncio
async def test_lookup_recalls_missing_results_key_returns_empty() -> None:
    """Defensive: if NHTSA changes shape, don't KeyError — return []."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok({"Count": 0, "Message": "unexpected shape"})

    async with _make_client(handler) as client:
        assert await lookup_recalls("Ford", "Mustang", 2025, client=client) == []
