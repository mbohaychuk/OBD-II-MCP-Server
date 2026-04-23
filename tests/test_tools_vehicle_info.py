"""Integration tests for `get_vehicle_info` against the Ircama simulator.

The `car` scenario does not implement Mode 09 (VIN / calibration / CVN), so
we verify the tool gracefully returns nulls for those fields and still
populates protocol / voltage / status / port.
"""

from __future__ import annotations

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

        # ELM_VOLTAGE and protocol/status/port are always populated.
        assert info["voltage_volts"] is not None
        assert 10.0 < info["voltage_volts"] < 16.0
        assert "CAN" in info["protocol"]
        assert info["port"] == elm_simulator
        assert info["status"] == "Car Connected"
        assert isinstance(info["timestamp"], float)
    finally:
        await client.close()
