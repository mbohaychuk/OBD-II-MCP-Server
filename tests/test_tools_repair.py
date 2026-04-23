"""Tool-layer test for `lookup_repair_info`.

Verifies that the tool echoes the caller's context (dtc/year/make/model)
into the response alongside Sidekick's payload, and carries a timestamp.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from obd_mcp.tools import lookup_repair_info


@pytest.mark.asyncio
async def test_lookup_repair_info_echoes_context_and_timestamps() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(
                {"summary": "bank 1 lean", "sources": [{"title": "FSM", "url": "u"}]}
            ).encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await lookup_repair_info(
            sidekick_url="http://sidekick.test",
            dtc="P0171",
            year=2015,
            make="Ford",
            model="F-150",
            http_client=http,
        )

    assert result["available"] is True
    assert result["dtc"] == "P0171"
    assert result["year"] == 2015
    assert result["make"] == "Ford"
    assert result["model"] == "F-150"
    assert result["summary"] == "bank 1 lean"
    assert len(result["sources"]) == 1
    assert isinstance(result["timestamp"], float)


@pytest.mark.asyncio
async def test_lookup_repair_info_preserves_context_on_sidekick_outage() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await lookup_repair_info(
            sidekick_url="http://sidekick.test",
            dtc="P0420",
            year=2019,
            make="Ford",
            model="Edge",
            http_client=http,
        )

    # Context echoed so the LLM can narrate the outage without re-asking.
    assert result["dtc"] == "P0420"
    assert result["year"] == 2019
    assert result["available"] is False
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_lookup_repair_info_accepts_null_vehicle_context() -> None:
    """DTC-only lookup is valid — caller may not know year/make/model."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=json.dumps({"summary": "generic", "sources": []}).encode()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await lookup_repair_info(
            sidekick_url="http://sidekick.test",
            dtc="P0300",
            http_client=http,
        )

    assert captured["body"] == {
        "dtc": "P0300",
        "year": None,
        "make": None,
        "model": None,
    }
    assert result["available"] is True
    assert result["year"] is None
