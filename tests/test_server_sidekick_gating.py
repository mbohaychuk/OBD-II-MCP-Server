"""Sidekick tool is gated by the `SIDEKICK_URL` env var.

When absent, `lookup_repair_info` must not appear in `mcp.list_tools()`
— configuration-by-opt-in, not a silent no-op tool. When present, the
registrar helper attaches it to the server.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from obd_mcp.server import mcp, register_sidekick_tool


@pytest.mark.asyncio
async def test_sidekick_tool_not_registered_when_env_unset() -> None:
    """The default server process in tests has no SIDEKICK_URL — tool absent."""
    names = {t.name for t in await mcp.list_tools()}
    assert "lookup_repair_info" not in names


@pytest.mark.asyncio
async def test_register_sidekick_tool_attaches_to_server() -> None:
    scratch = FastMCP("scratch")
    assert not any(t.name == "lookup_repair_info" for t in await scratch.list_tools())

    register_sidekick_tool(scratch, "http://sidekick.test")

    tools = await scratch.list_tools()
    repair = next((t for t in tools if t.name == "lookup_repair_info"), None)
    assert repair is not None
    assert repair.annotations is not None
    assert repair.annotations.readOnlyHint is True
    assert repair.annotations.idempotentHint is True
