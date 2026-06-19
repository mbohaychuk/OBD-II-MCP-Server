"""Integration tests for `list_supported_pids` and `read_live_data`."""

from __future__ import annotations

from typing import Any

import obd
import pytest

from obd_mcp.client import ObdClient
from obd_mcp.tools import list_supported_pids, read_live_data


class _NullResponse:
    value = None

    def is_null(self) -> bool:
        return True


class _RecordingClient:
    """Stub client that records every command it is asked about, so a test
    can prove a PID was rejected *before* any wire I/O was attempted."""

    def __init__(self) -> None:
        self.supports_checked: list[str] = []
        self.queried: list[str] = []

    async def supports(self, command: Any) -> bool:
        self.supports_checked.append(command.name)
        return True

    async def query(self, command: Any) -> _NullResponse:
        self.queried.append(command.name)
        return _NullResponse()


@pytest.mark.asyncio
async def test_read_live_data_rejects_destructive_command_without_querying() -> None:
    """CLEAR_DTC (Mode 04) is a registered command and python-OBD reports it
    as supported on every connection, so without a mode gate read_live_data
    would dispatch a destructive erase straight to the bus — around the
    elicitation that clear_dtcs enforces. It must be refused in-band instead."""
    client = _RecordingClient()
    readings = await read_live_data(client, ["CLEAR_DTC"])  # type: ignore[arg-type]
    assert readings[0]["name"] == "CLEAR_DTC"
    assert readings[0]["error"] == "NOT_A_READABLE_PID"
    assert client.queried == []
    assert client.supports_checked == []


@pytest.mark.asyncio
async def test_read_live_data_rejects_non_readable_modes() -> None:
    """Mode 03 (GET_DTC) and AT commands (ELM_VOLTAGE) have dedicated tools or
    no business in a live-data read; only Mode 01/09 reads are admitted."""
    client = _RecordingClient()
    readings = await read_live_data(client, ["GET_DTC", "ELM_VOLTAGE"])  # type: ignore[arg-type]
    assert {r["error"] for r in readings} == {"NOT_A_READABLE_PID"}
    assert client.queried == []


@pytest.mark.asyncio
async def test_read_live_data_admits_mode_09() -> None:
    """VIN (Mode 09) passes the mode gate — it is a read, just an unusual one;
    the supports() check (not the gate) decides whether the ECU answers."""
    assert obd.commands["VIN"].mode == 9
    client = _RecordingClient()
    await read_live_data(client, ["VIN"])  # type: ignore[arg-type]
    assert client.supports_checked == ["VIN"]


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
