"""Tests for `read_readiness_monitors` and `clear_dtcs`.

`read_readiness_monitors` is exercised against the simulator. `clear_dtcs`
is exercised with a stub client + stub confirmer, since the simulator's
'car' scenario is non-interactive: we can't simulate a user accepting or
declining the elicitation prompt without a real MCP client.
"""

from __future__ import annotations

from typing import Any

import pytest

from obd_mcp.client import ObdClient
from obd_mcp.tools import clear_dtcs, read_readiness_monitors


@pytest.mark.asyncio
async def test_read_readiness_monitors_against_simulator(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        report = await read_readiness_monitors(client)
        assert report["available"] is True
        assert isinstance(report["mil"], bool)
        assert isinstance(report["dtc_count"], int)
        assert report["ignition_type"] in {"spark", "compression", None}
        # car scenario always has at least the 3 BASE_TESTS applicable.
        assert report["monitors"], report
        names = {m["name"] for m in report["monitors"]}
        assert "MISFIRE_MONITORING" in names
        for m in report["monitors"]:
            assert m["applicable"] is True
            assert isinstance(m["complete"], bool)
    finally:
        await client.close()


class _StubMessage:
    pass


class _StubResponse:
    def __init__(self, value: Any, has_messages: bool = True) -> None:
        self.value = value
        self.messages = [_StubMessage()] if has_messages else []

    def is_null(self) -> bool:
        return self.value is None


class _StubStatusTest:
    def __init__(self, available: bool, complete: bool) -> None:
        self.available = available
        self.complete = complete


class _StubStatus:
    def __init__(
        self,
        mil: bool = False,
        dtc_count: int = 0,
        ignition_type: str = "spark",
        incomplete_names: tuple[str, ...] = (),
    ) -> None:
        self.MIL = mil
        self.DTC_count = dtc_count
        self.ignition_type = ignition_type
        # Set every BASE/SPARK monitor as available; incomplete per the arg.
        from obd.codes import BASE_TESTS, SPARK_TESTS

        for name in BASE_TESTS + SPARK_TESTS:
            if name is None:
                continue
            complete = name not in incomplete_names
            setattr(self, name, _StubStatusTest(available=True, complete=complete))


class _StubClient:
    def __init__(
        self, status: _StubStatus | None, clear_response: _StubResponse | None = None
    ) -> None:
        self._status_resp = _StubResponse(status) if status is not None else _StubResponse(None)
        self._clear_resp = clear_response or _StubResponse(None)
        self.queries: list[str] = []

    async def query(self, command: Any) -> _StubResponse:
        self.queries.append(command.name)
        if command.name == "STATUS":
            return self._status_resp
        if command.name == "CLEAR_DTC":
            return self._clear_resp
        raise AssertionError(f"unexpected query: {command.name}")


@pytest.mark.asyncio
async def test_clear_dtcs_refused_by_user_does_not_send_mode_04() -> None:
    stub = _StubClient(_StubStatus(incomplete_names=("CATALYST_MONITORING",)))

    async def never(_msg: str, _incomplete: list[str]) -> bool:
        return False

    result = await clear_dtcs(stub, never)  # type: ignore[arg-type]
    assert result["cleared"] is False
    assert result["reason"] == "user_declined"
    assert "CLEAR_DTC" not in stub.queries
    assert result["readiness_before"]["available"] is True


@pytest.mark.asyncio
async def test_clear_dtcs_approved_sends_mode_04_and_reports_success() -> None:
    stub = _StubClient(
        _StubStatus(),
        clear_response=_StubResponse(None, has_messages=True),
    )

    async def approve(_msg: str, _incomplete: list[str]) -> bool:
        return True

    result = await clear_dtcs(stub, approve)  # type: ignore[arg-type]
    assert result["cleared"] is True
    assert result["reason"] is None
    assert "CLEAR_DTC" in stub.queries


@pytest.mark.asyncio
async def test_clear_dtcs_approved_but_adapter_silent_reports_failure() -> None:
    stub = _StubClient(
        _StubStatus(),
        clear_response=_StubResponse(None, has_messages=False),
    )

    async def approve(_msg: str, _incomplete: list[str]) -> bool:
        return True

    result = await clear_dtcs(stub, approve)  # type: ignore[arg-type]
    assert result["cleared"] is False
    assert result["reason"] == "adapter_no_response"


@pytest.mark.asyncio
async def test_clear_dtcs_prompt_includes_incomplete_monitors() -> None:
    incomplete = ("CATALYST_MONITORING", "OXYGEN_SENSOR_MONITORING")
    stub = _StubClient(_StubStatus(incomplete_names=incomplete))

    seen_prompt: dict[str, Any] = {}

    async def capture(msg: str, incomplete_monitors: list[str]) -> bool:
        seen_prompt["msg"] = msg
        seen_prompt["incomplete"] = incomplete_monitors
        return False

    await clear_dtcs(stub, capture)  # type: ignore[arg-type]
    for name in incomplete:
        assert name in seen_prompt["msg"]
    assert set(seen_prompt["incomplete"]) == set(incomplete)


@pytest.mark.asyncio
async def test_clear_dtcs_prompt_when_all_monitors_complete() -> None:
    stub = _StubClient(_StubStatus(incomplete_names=()))

    seen_prompt: dict[str, Any] = {}

    async def capture(msg: str, incomplete_monitors: list[str]) -> bool:
        seen_prompt["msg"] = msg
        seen_prompt["incomplete"] = incomplete_monitors
        return False

    await clear_dtcs(stub, capture)  # type: ignore[arg-type]
    assert seen_prompt["incomplete"] == []
    assert "COMPLETE" in seen_prompt["msg"]
