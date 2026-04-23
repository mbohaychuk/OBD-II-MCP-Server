"""End-to-end coverage across every Mode 01 PID the simulator advertises.

This is the Phase 2 acceptance test: `list_supported_pids` + one
`read_live_data` per PID should cover the full Mode 01 surface without
any uncaught exceptions. Each reading must have either a value or an
in-band error code (`UNKNOWN_PID`, `NOT_SUPPORTED`, `NO_DATA`) — nothing
bubbles up as a traceback.
"""

from __future__ import annotations

import pytest

from obd_mcp.client import ObdClient
from obd_mcp.tools import list_supported_pids, read_live_data

_ALLOWED_ERROR_CODES = {"UNKNOWN_PID", "NOT_SUPPORTED", "NO_DATA"}


@pytest.mark.asyncio
async def test_every_supported_pid_decodes_or_errors_cleanly(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        supported = await list_supported_pids(client)
        assert supported, "simulator should advertise at least one Mode 01 PID"

        names = [entry["name"] for entry in supported]
        readings = await read_live_data(client, names)

        assert len(readings) == len(names)
        for reading, name in zip(readings, names, strict=True):
            assert reading["name"] == name
            if "error" in reading:
                assert reading["error"] in _ALLOWED_ERROR_CODES, reading
                continue
            # Success shape: value present (may be None for enum-like PIDs),
            # unit either string or None, pid populated, timestamp set.
            assert "value" in reading, reading
            assert "unit" in reading
            assert reading["pid"] is not None
            assert isinstance(reading["timestamp"], float)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_supported_pids_set_is_non_trivial(elm_simulator: str) -> None:
    """The 'car' scenario supports a recognizable subset of the core Mode 01 PIDs."""
    client = ObdClient(portstr=elm_simulator)
    try:
        supported = await list_supported_pids(client)
        names = {entry["name"] for entry in supported}
        # These are the sanity-check set the simulator always answers for.
        assert {"RPM", "SPEED", "COOLANT_TEMP"}.issubset(names), names
    finally:
        await client.close()
