"""Integration tests for `list_supported_pids` and `read_live_data`."""

from __future__ import annotations

import pytest

from obd_mcp.client import ObdClient
from obd_mcp.tools import list_supported_pids, read_live_data


@pytest.mark.asyncio
async def test_list_supported_pids_includes_core_mode01(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        pids = await list_supported_pids(client)
        assert pids
        names = {p["name"] for p in pids}
        # The 'car' scenario must advertise these — if it doesn't, the scenario
        # regressed and tests below will break in interesting ways.
        assert {"RPM", "SPEED", "COOLANT_TEMP"}.issubset(names)
        # Every PID is a Mode 01 hex string.
        for entry in pids:
            assert entry["pid"].startswith("01"), entry
            assert entry["description"], entry
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_read_live_data_returns_decoded_values(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        readings = await read_live_data(client, ["RPM", "SPEED", "COOLANT_TEMP"])
        assert len(readings) == 3
        by_name = {r["name"]: r for r in readings}

        assert by_name["RPM"]["error"] is None if "error" in by_name["RPM"] else True
        assert by_name["RPM"]["value"] > 0
        assert by_name["RPM"]["unit"] == "revolutions_per_minute"
        assert by_name["RPM"]["pid"] == "010C"

        assert by_name["SPEED"]["unit"] in {"kilometer_per_hour", "kph"}
        assert by_name["COOLANT_TEMP"]["unit"] in {"degree_Celsius", "celsius"}

        for r in readings:
            assert isinstance(r["timestamp"], float)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_read_live_data_unknown_pid_reports_error(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        readings = await read_live_data(client, ["NOT_A_REAL_PID"])
        assert len(readings) == 1
        assert readings[0]["name"] == "NOT_A_REAL_PID"
        assert readings[0]["error"] == "UNKNOWN_PID"
        assert readings[0]["pid"] is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_read_live_data_unsupported_pid_reports_error(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        # VIN (Mode 09) is known to python-OBD but the 'car' scenario doesn't advertise it.
        readings = await read_live_data(client, ["VIN"])
        assert len(readings) == 1
        assert readings[0]["name"] == "VIN"
        assert readings[0]["error"] == "NOT_SUPPORTED"
        assert readings[0]["pid"] == "0902"
    finally:
        await client.close()
