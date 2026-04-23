"""Tests for `read_freeze_frame`.

Simulator integration covers the "no freeze frame stored" path (the 'car'
scenario never sets a DTC). Stub-client unit tests cover the populated-
frame path since the simulator cannot be coerced into capturing one.
"""

from __future__ import annotations

from typing import Any

import obd
import pytest

from obd_mcp.client import ObdClient
from obd_mcp.tools import read_freeze_frame


@pytest.mark.asyncio
async def test_read_freeze_frame_against_simulator_no_dtc_set(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        report = await read_freeze_frame(client, frame_index=0)
        assert report["available"] is False
        assert report["reason"] == "NO_FREEZE_FRAME"
        assert report["dtc"] is None
        assert report["frame"] == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_read_freeze_frame_index_other_than_zero_returns_structured_error() -> None:
    """python-OBD only sends frame 0. Non-zero indices are rejected in-band."""

    class _Stub:
        async def query(self, _cmd: Any) -> Any:
            raise AssertionError("should not query the wire for frame_index != 0")

        async def supports(self, _cmd: Any) -> bool:
            raise AssertionError("should not check support")

    report = await read_freeze_frame(_Stub(), frame_index=1)  # type: ignore[arg-type]
    assert report["available"] is False
    assert report["reason"] == "FRAME_INDEX_NOT_SUPPORTED"
    assert report["dtc"] is None
    assert report["frame"] == {}


class _StubResponse:
    def __init__(self, value: Any) -> None:
        self.value = value

    def is_null(self) -> bool:
        return self.value is None


class _StubFrameClient:
    """Mimics just enough of ObdClient for read_freeze_frame's happy path."""

    def __init__(
        self,
        dtc: tuple[str, str] | None,
        supported: dict[str, Any],
    ) -> None:
        self._dtc = dtc
        self._supported = supported
        self.queries: list[str] = []

    async def query(self, command: Any) -> _StubResponse:
        self.queries.append(command.name)
        if command.name == "DTC_FREEZE_DTC":
            return _StubResponse(self._dtc)
        if command.name in self._supported:
            return _StubResponse(self._supported[command.name])
        return _StubResponse(None)

    async def supports(self, command: Any) -> bool:
        return command.name in self._supported


@pytest.mark.asyncio
async def test_read_freeze_frame_with_populated_dtc_returns_structured_readings() -> None:
    # Use obd.Unit to build pint-compatible quantities as python-OBD would.
    rpm_value = obd.Unit.Quantity(1200, obd.Unit.rpm)
    coolant = obd.Unit.Quantity(90, obd.Unit.celsius)

    stub = _StubFrameClient(
        dtc=("P0420", "Catalyst System Efficiency Below Threshold (Bank 1)"),
        supported={"DTC_RPM": rpm_value, "DTC_COOLANT_TEMP": coolant},
    )

    report = await read_freeze_frame(stub, frame_index=0)  # type: ignore[arg-type]

    assert report["available"] is True
    assert report["reason"] is None
    assert report["dtc"] == {
        "code": "P0420",
        "description": "Catalyst System Efficiency Below Threshold (Bank 1)",
    }
    assert set(report["frame"].keys()) == {"DTC_RPM", "DTC_COOLANT_TEMP"}
    assert report["frame"]["DTC_RPM"]["value"] == 1200.0
    assert report["frame"]["DTC_RPM"]["unit"] == "revolutions_per_minute"
    assert report["frame"]["DTC_COOLANT_TEMP"]["value"] == 90.0
    assert report["frame"]["DTC_COOLANT_TEMP"]["unit"] == "degree_Celsius"


@pytest.mark.asyncio
async def test_read_freeze_frame_skips_metadata_pids() -> None:
    """DTC_PIDS_A/B/C are the Mode 02 "supported PIDs" bitmaps; they're not frame data."""
    stub = _StubFrameClient(
        dtc=("P0171", "System Too Lean (Bank 1)"),
        supported={"DTC_PIDS_A": 0xFFFFFFFF, "DTC_RPM": obd.Unit.Quantity(800, obd.Unit.rpm)},
    )
    report = await read_freeze_frame(stub, frame_index=0)  # type: ignore[arg-type]
    assert "DTC_PIDS_A" not in report["frame"]
    assert "DTC_RPM" in report["frame"]
