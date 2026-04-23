"""Session resource wiring: `obd://sessions/{id}.json` serves what
`record_session` stored. Uses the module-level session store to avoid
spinning up a full MCP host for an end-to-end round trip.
"""

from __future__ import annotations

import json
import time

import pytest

from obd_mcp.server import _SESSIONS, mcp


@pytest.mark.asyncio
async def test_session_resource_template_registered() -> None:
    templates = await mcp.list_resource_templates()
    uris = [t.uriTemplate for t in templates]
    assert "obd://sessions/{session_id}.json" in uris


@pytest.mark.asyncio
async def test_session_resource_reads_back_stored_payload() -> None:
    session_id = "test-abc123"
    payload = {
        "session_id": session_id,
        "duration_s": 0.5,
        "hz_target": 2.0,
        "pids": ["RPM"],
        "samples_count": 1,
        "samples": [{"t": 0.0, "readings": []}],
        "started_at": time.time(),
        "resource_uri": f"obd://sessions/{session_id}.json",
    }
    _SESSIONS[session_id] = payload
    try:
        contents = await mcp.read_resource(f"obd://sessions/{session_id}.json")
        contents_list = list(contents)
        assert contents_list
        data = json.loads(contents_list[0].content)
        assert data["session_id"] == session_id
        assert data["pids"] == ["RPM"]
        assert data["samples_count"] == 1
    finally:
        _SESSIONS.pop(session_id, None)


@pytest.mark.asyncio
async def test_session_resource_read_unknown_id_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        await mcp.read_resource("obd://sessions/does-not-exist-xyz.json")


@pytest.mark.asyncio
async def test_record_session_tool_registered_with_readonly_annotation() -> None:
    tools = await mcp.list_tools()
    record = next((t for t in tools if t.name == "record_session"), None)
    assert record is not None
    assert record.annotations is not None
    assert record.annotations.readOnlyHint is True
