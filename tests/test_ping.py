"""Smoke test for the ping health-check tool.

Also verifies that `ping` is registered with FastMCP so the host can discover it.
"""

from __future__ import annotations

import pytest

from obd_mcp.server import mcp, ping


def test_ping_returns_pong() -> None:
    assert ping() == "pong"


@pytest.mark.asyncio
async def test_ping_is_registered_with_fastmcp() -> None:
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "ping" in names
