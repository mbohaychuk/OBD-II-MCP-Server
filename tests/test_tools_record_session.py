"""Tests for `record_session` — time-bounded PID sampling.

Covers input validation, output shape, progress callbacks, and an
integration pass against the Ircama simulator to verify readings
materialize. Uses short durations (≤ 0.5s) to keep the suite fast.
"""

from __future__ import annotations

from typing import Any

import pytest

from obd_mcp.client import ObdClient
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
