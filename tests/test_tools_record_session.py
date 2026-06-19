"""Tests for `record_session` — time-bounded PID sampling.

Covers input validation, output shape, progress callbacks, and an
integration pass against the Ircama simulator to verify readings
materialize. Uses short durations (≤ 0.5s) to keep the suite fast.
"""

from __future__ import annotations

from typing import Any

import pytest

from obd_mcp.client import ObdClient
from obd_mcp.errors import ObdError, ObdErrorCode
from obd_mcp.tools import record_session


@pytest.mark.asyncio
async def test_record_session_rejects_nonpositive_duration() -> None:
    with pytest.raises(ValueError, match="duration_s"):
        await record_session(None, duration_s=0, pids=["RPM"], hz_target=1.0)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_record_session_rejects_excessive_duration() -> None:
    with pytest.raises(ValueError, match="duration_s"):
        await record_session(None, duration_s=601, pids=["RPM"], hz_target=1.0)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_record_session_rejects_empty_pids() -> None:
    with pytest.raises(ValueError, match="pids"):
        await record_session(None, duration_s=1.0, pids=[], hz_target=1.0)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_record_session_rejects_invalid_hz() -> None:
    with pytest.raises(ValueError, match="hz_target"):
        await record_session(
            None,  # type: ignore[arg-type]
            duration_s=1.0,
            pids=["RPM"],
            hz_target=0,
        )
    with pytest.raises(ValueError, match="hz_target"):
        await record_session(
            None,  # type: ignore[arg-type]
            duration_s=1.0,
            pids=["RPM"],
            hz_target=100,
        )


@pytest.mark.asyncio
async def test_record_session_captures_samples_against_simulator(
    elm_simulator: str,
) -> None:
    client = ObdClient(portstr=elm_simulator)
    progress_events: list[tuple[int, int]] = []

    async def progress(current: int, total: int) -> None:
        progress_events.append((current, total))

    try:
        result = await record_session(
            client,
            duration_s=0.5,
            pids=["RPM", "SPEED"],
            hz_target=4.0,
            progress=progress,
        )
    finally:
        await client.close()

    assert result["duration_s"] == 0.5
    assert result["hz_target"] == 4.0
    assert result["pids"] == ["RPM", "SPEED"]
    assert result["session_id"]
    assert result["resource_uri"] == f"obd://sessions/{result['session_id']}.json"
    # 0.5s @ 4Hz target → ~2 samples expected; accept [1, 4] for scheduler jitter.
    assert 1 <= result["samples_count"] <= 4
    assert len(result["samples"]) == result["samples_count"]
    assert progress_events  # callback was fired at least once

    sample = result["samples"][0]
    assert "t" in sample
    assert "readings" in sample
    names = {r["name"] for r in sample["readings"]}
    assert names == {"RPM", "SPEED"}


@pytest.mark.asyncio
async def test_record_session_unknown_pid_raises() -> None:
    """Unknown PID names are caught up-front, before any sampling starts."""
    with pytest.raises(ValueError, match="unknown PID"):
        await record_session(
            None,  # type: ignore[arg-type]
            duration_s=0.1,
            pids=["RPM", "NOT_A_REAL_PID"],
            hz_target=1.0,
        )


@pytest.mark.asyncio
async def test_record_session_rejects_non_readable_pid_up_front() -> None:
    """A destructive/non-Mode-01-09 command (e.g. CLEAR_DTC) is refused before
    sampling — it must never be dispatched per-tick around the elicitation."""
    with pytest.raises(ValueError, match="readable Mode 01/09"):
        await record_session(
            None,  # type: ignore[arg-type]
            duration_s=0.1,
            pids=["RPM", "CLEAR_DTC"],
            hz_target=1.0,
        )


class _FastStubClient:
    """Returns canned readings immediately — exercises scheduling without a bus."""

    async def supports(self, _cmd: Any) -> bool:
        return True

    async def query(self, cmd: Any) -> Any:
        import obd

        class R:
            def __init__(self, v: Any) -> None:
                self.value = v

            def is_null(self) -> bool:
                return self.value is None

        if cmd.name == "RPM":
            return R(obd.Unit.Quantity(1200, obd.Unit.rpm))
        return R(None)


@pytest.mark.asyncio
async def test_record_session_samples_count_roughly_matches_rate() -> None:
    """Stub client ~= zero query latency → samples ~= duration × hz_target."""
    client = _FastStubClient()
    result = await record_session(
        client,  # type: ignore[arg-type]
        duration_s=0.4,
        pids=["RPM"],
        hz_target=10.0,
    )
    # Target = 4 samples; accept a wide range so slow CI machines don't flake.
    assert 2 <= result["samples_count"] <= 8
    assert result["ended_early"] is None


class _DropMidSessionClient:
    """Succeeds for the first sample, then raises a transport ObdError —
    simulates an adapter dropping out mid-recording."""

    def __init__(self, fail_on_query: int) -> None:
        self.query_calls = 0
        self._fail_on = fail_on_query

    async def supports(self, _cmd: Any) -> bool:
        return True

    async def query(self, _cmd: Any) -> Any:
        import obd

        self.query_calls += 1
        if self.query_calls >= self._fail_on:
            raise ObdError(ObdErrorCode.UNABLE_TO_CONNECT, "adapter unplugged")

        class R:
            value = obd.Unit.Quantity(1200, obd.Unit.rpm)

            def is_null(self) -> bool:
                return False

        return R()


@pytest.mark.asyncio
async def test_record_session_transport_drop_keeps_partial_samples() -> None:
    """A transport failure mid-recording ends early but preserves samples."""
    client = _DropMidSessionClient(fail_on_query=2)
    result = await record_session(
        client,  # type: ignore[arg-type]
        duration_s=0.5,
        pids=["RPM"],
        hz_target=20.0,
    )
    assert result["samples_count"] == 1
    assert len(result["samples"]) == 1
    assert result["ended_early"] is not None
    assert "UNABLE_TO_CONNECT" in result["ended_early"]["reason"]
