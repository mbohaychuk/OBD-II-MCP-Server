"""Unit tests for the NHTSA vPIC VIN-enrichment helper.

No live network calls — all traffic is routed through an httpx
`MockTransport` so the tests are deterministic and offline-safe.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from obd_mcp.vin import decode_vin

VALID_VIN = "1FATP8UH0F5274981"  # 2015 Mustang GT (shape, not looked up)


def _ok_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode())


def _make_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_decode_vin_extracts_year_make_model_displacement() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _ok_response(
            {
                "Results": [
                    {
                        "ModelYear": "2015",
                        "Make": "FORD",
                        "Model": "Mustang",
                        "Trim": "GT",
                        "DisplacementL": "5.0",
                        "EngineCylinders": "8",
                        "FuelTypePrimary": "Gasoline",
                        "VehicleType": "PASSENGER CAR",
                        "BodyClass": "Coupe",
                        "ErrorCode": "0",
                    }
                ]
            }
        )

    async with _make_client(handler) as c:
        result = await decode_vin(VALID_VIN, client=c)

    assert result is not None
    assert result["year"] == 2015
    assert result["make"] == "FORD"
    assert result["model"] == "Mustang"
    assert result["trim"] == "GT"
    assert result["displacement_liters"] == 5.0
    assert result["cylinders"] == 8
    assert result["fuel_type"] == "Gasoline"
    assert result["vehicle_type"] == "PASSENGER CAR"
    assert result["body_class"] == "Coupe"
    assert len(calls) == 1
    assert VALID_VIN in str(calls[0].url)


@pytest.mark.asyncio
async def test_decode_vin_returns_none_for_empty_vin() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make HTTP call with empty VIN")

    async with _make_client(handler) as c:
        assert await decode_vin("", client=c) is None
        assert await decode_vin("   ", client=c) is None


@pytest.mark.asyncio
async def test_decode_vin_returns_none_on_network_error() -> None:
    """VIN enrichment is best-effort. Offline → None, not a crash."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with _make_client(handler) as c:
        result = await decode_vin(VALID_VIN, client=c)
    assert result is None


@pytest.mark.asyncio
async def test_decode_vin_returns_none_on_http_500() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    async with _make_client(handler) as c:
        result = await decode_vin(VALID_VIN, client=c)
    assert result is None


@pytest.mark.asyncio
async def test_decode_vin_returns_none_when_vpic_reports_error_code() -> None:
    """vPIC returns 200 OK with ErrorCode != '0' for unparseable VINs."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok_response(
            {
                "Results": [
                    {
                        "ModelYear": "",
                        "Make": "",
                        "Model": "",
                        "ErrorCode": "1",
                        "ErrorText": "1 - Check Digit (9th position) does not calculate properly.",
                    }
                ]
            }
        )

    async with _make_client(handler) as c:
        result = await decode_vin(VALID_VIN, client=c)

    # We surface the enrichment anyway — the LLM can see ErrorCode — but
    # the essential fields are null.
    assert result is not None
    assert result["year"] is None
    assert result["make"] is None
    assert result["error_code"] == "1"


@pytest.mark.asyncio
async def test_decode_vin_treats_empty_string_fields_as_none() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok_response(
            {
                "Results": [
                    {
                        "ModelYear": "2019",
                        "Make": "FORD",
                        "Model": "",
                        "DisplacementL": "",
                        "EngineCylinders": "",
                        "ErrorCode": "0",
                    }
                ]
            }
        )

    async with _make_client(handler) as c:
        result = await decode_vin(VALID_VIN, client=c)

    assert result is not None
    assert result["year"] == 2019
    assert result["make"] == "FORD"
    assert result["model"] is None
    assert result["displacement_liters"] is None
    assert result["cylinders"] is None
