"""Verify every Phase 1 tool is registered with FastMCP with the right
annotations. Catches wiring regressions (e.g. forgetting destructiveHint
on clear_dtcs) before they ship.
"""

from __future__ import annotations

import pytest

from obd_mcp.server import mcp

EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "ping",
        "get_vehicle_info",
        "list_supported_pids",
        "read_live_data",
        "read_dtcs",
        "read_freeze_frame",
        "read_readiness_monitors",
        "record_session",
        "list_manufacturer_signals",
        "lookup_recalls_and_complaints",
        "clear_dtcs",
    }
)


@pytest.mark.asyncio
async def test_all_expected_tools_are_registered() -> None:
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"tools missing from FastMCP registry: {missing}"


@pytest.mark.asyncio
async def test_clear_dtcs_is_marked_destructive() -> None:
    tools = await mcp.list_tools()
    clear = next(t for t in tools if t.name == "clear_dtcs")
    assert clear.annotations is not None
    assert clear.annotations.destructiveHint is True
    assert clear.annotations.idempotentHint is False


@pytest.mark.asyncio
async def test_read_tools_are_marked_read_only() -> None:
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}
    for name in (
        "get_vehicle_info",
        "list_supported_pids",
        "read_live_data",
        "read_dtcs",
        "read_freeze_frame",
        "read_readiness_monitors",
        "list_manufacturer_signals",
        "lookup_recalls_and_complaints",
    ):
        ann = by_name[name].annotations
        assert ann is not None, name
        assert ann.readOnlyHint is True, name


@pytest.mark.asyncio
async def test_record_session_is_not_read_only() -> None:
    """record_session persists a session and mints an obd:// resource, so it
    modifies server state — readOnlyHint must be false (but not destructive)."""
    tools = await mcp.list_tools()
    rec = next(t for t in tools if t.name == "record_session")
    assert rec.annotations is not None
    assert rec.annotations.readOnlyHint is False
    assert rec.annotations.destructiveHint is False
    assert rec.annotations.idempotentHint is False
