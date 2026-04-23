"""Integration tests for `get_vehicle_info` against the Ircama simulator.

The `car` scenario does not implement Mode 09 (VIN / calibration / CVN), so
we verify the tool gracefully returns nulls for those fields and still
populates protocol / voltage / status / port. VIN enrichment is covered
separately in `test_vin.py` (offline, mocked); this suite only checks
that the enrichment key is present and null when no VIN is available.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from obd_mcp.client import ObdClient
from obd_mcp.tools import get_vehicle_info


@pytest.mark.asyncio
async def test_get_vehicle_info_against_simulator(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        info = await get_vehicle_info(client)

        # Simulator's 'car' scenario doesn't implement Mode 09.
        assert info["vin"] is None
        assert info["calibration_ids"] is None
        assert info["cvn"] is None
        # No VIN → no enrichment attempted.
        assert info["vin_decoded"] is None

        # ELM_VOLTAGE and protocol/status/port are always populated.
        assert info["voltage_volts"] is not None
        assert 10.0 < info["voltage_volts"] < 16.0
        assert "CAN" in info["protocol"]
        assert info["port"] == elm_simulator
        assert info["status"] == "Car Connected"
        assert isinstance(info["timestamp"], float)
    finally:
        await client.close()


class _StubVinInfoClient:
    """Just enough for get_vehicle_info without a real simulator."""

    def __init__(self, vin: str) -> None:
        self._vin = vin

    async def query(self, command: Any) -> Any:
        class R:
            def __init__(self, v: Any) -> None:
                self.value = v

            def is_null(self) -> bool:
                return self.value is None

        if command.name == "VIN":
            return R(self._vin)
        if command.name == "ELM_VOLTAGE":
            import obd

            return R(obd.Unit.Quantity(12.4, obd.Unit.volt))
        return R(None)

    async def status(self) -> str:
        return "Car Connected"

    async def protocol_name(self) -> str:
        return "ISO 15765-4 (CAN 11/500)"

    async def port_name(self) -> str:
        return "stub://vin-enrichment"


@pytest.mark.asyncio
async def test_get_vehicle_info_calls_vpic_when_vin_present() -> None:
    """If the ECU reports a VIN, enrichment runs and attaches vin_decoded."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "Results": [
                        {
                            "ModelYear": "2015",
                            "Make": "FORD",
                            "Model": "F-150",
                            "ErrorCode": "0",
                        }
                    ]
                }
            ).encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        info = await get_vehicle_info(
            _StubVinInfoClient("1FTEW1EF2FKE12345"),  # type: ignore[arg-type]
            http_client=http_client,
        )

    assert info["vin"] == "1FTEW1EF2FKE12345"
    assert info["vin_decoded"] is not None
    assert info["vin_decoded"]["year"] == 2015
    assert info["vin_decoded"]["make"] == "FORD"
    assert info["vin_decoded"]["model"] == "F-150"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_vehicle_info_survives_vpic_outage() -> None:
    """vPIC down → vin_decoded is null, rest of the payload still fine."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        info = await get_vehicle_info(
            _StubVinInfoClient("1FTEW1EF2FKE12345"),  # type: ignore[arg-type]
            http_client=http_client,
        )

    assert info["vin"] == "1FTEW1EF2FKE12345"
    assert info["vin_decoded"] is None
    assert info["status"] == "Car Connected"
