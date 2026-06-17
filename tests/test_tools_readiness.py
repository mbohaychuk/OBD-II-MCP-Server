"""Unit tests for read_readiness_monitors monitor de-duplication.

EGR_VVT_SYSTEM_MONITORING is the one monitor python-OBD lists in BOTH the
spark and compression test sets, so a naive BASE+SPARK+COMPRESSION walk emits
it twice. These tests use a hand-built Status stub to keep the assertion
deterministic regardless of the simulator's ignition type.
"""

from __future__ import annotations

import pytest

from obd_mcp.tools import read_readiness_monitors


class _FakeTest:
    def __init__(self, *, available: bool, complete: bool) -> None:
        self.available = available
        self.complete = complete


class _FakeStatus:
    MIL = False
    DTC_count = 0
    ignition_type = "spark"
    EGR_VVT_SYSTEM_MONITORING = _FakeTest(available=True, complete=True)


class _FakeResponse:
    value = _FakeStatus()

    def is_null(self) -> bool:
        return False


class _FakeClient:
    async def query(self, _command: object) -> _FakeResponse:
        return _FakeResponse()


@pytest.mark.asyncio
async def test_readiness_monitors_emit_each_name_once() -> None:
    result = await read_readiness_monitors(_FakeClient())  # type: ignore[arg-type]
    names = [m["name"] for m in result["monitors"]]
    assert names.count("EGR_VVT_SYSTEM_MONITORING") == 1
    assert len(names) == len(set(names)), f"duplicate monitor names: {names}"
