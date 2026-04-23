"""Unit tests for the Mechanics Sidekick `repair-lookup` passthrough.

Sidekick is a separate project (user's RAG repair-manual chat); `obd-mcp`
only ships the HTTP bridge. All traffic here goes through
`httpx.MockTransport` so tests stay offline. The contract is narrow
enough that defining it in-repo is fine — Sidekick implements the
endpoint, obd-mcp calls it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from obd_mcp.sidekick import fetch_repair_info

SIDEKICK = "http://sidekick.test"


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode())


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_repair_info_posts_expected_body_and_shape() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _ok(
            {
                "summary": "P0300 points to a misfire. Common causes: plugs, coils, fuel delivery.",
                "sources": [
                    {
                        "title": "2025 Mustang EcoBoost Service Manual — §303",
                        "url": "https://manuals.local/mustang/2025/303",
                        "excerpt": "Replace coil-on-plug if secondary resistance is out of spec.",
                    }
                ],
            }
        )

    async with _client(handler) as http:
        result = await fetch_repair_info(
            SIDEKICK,
            dtc="P0300",
            year=2025,
            make="Ford",
            model="Mustang",
            client=http,
        )

    assert captured["method"] == "POST"
    assert captured["url"] == f"{SIDEKICK}/repair-lookup"
    assert captured["body"] == {
        "dtc": "P0300",
        "year": 2025,
        "make": "Ford",
        "model": "Mustang",
    }

    assert result["available"] is True
    assert result["error"] is None
    assert "misfire" in result["summary"]
    assert len(result["sources"]) == 1
    assert result["sources"][0]["title"].startswith("2025 Mustang")
    assert result["sources"][0]["url"].startswith("https://manuals.local")


@pytest.mark.asyncio
async def test_fetch_repair_info_trims_trailing_slash_on_base_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{SIDEKICK}/repair-lookup"
        return _ok({"summary": "ok", "sources": []})

    async with _client(handler) as http:
        result = await fetch_repair_info(
            SIDEKICK + "/",
            dtc="P0171",
            year=None,
            make=None,
            model=None,
            client=http,
        )
    assert result["available"] is True


@pytest.mark.asyncio
async def test_fetch_repair_info_network_failure_returns_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with _client(handler) as http:
        result = await fetch_repair_info(
            SIDEKICK, dtc="P0300", year=None, make=None, model=None, client=http
        )

    assert result["available"] is False
    assert result["summary"] is None
    assert result["sources"] == []
    assert result["error"] is not None
    assert "unreachable" in result["error"].lower() or "connect" in result["error"].lower()


@pytest.mark.asyncio
async def test_fetch_repair_info_http_500_returns_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    async with _client(handler) as http:
        result = await fetch_repair_info(
            SIDEKICK, dtc="P0300", year=None, make=None, model=None, client=http
        )

    assert result["available"] is False
    assert "500" in result["error"]


@pytest.mark.asyncio
async def test_fetch_repair_info_non_json_returns_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>hi</html>")

    async with _client(handler) as http:
        result = await fetch_repair_info(
            SIDEKICK, dtc="P0300", year=None, make=None, model=None, client=http
        )

    assert result["available"] is False
    assert "non-json" in result["error"].lower() or "json" in result["error"].lower()


@pytest.mark.asyncio
async def test_fetch_repair_info_tolerates_partial_payload() -> None:
    """Sidekick implementations may return only summary, or only sources."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok({"summary": "minimal response"})  # no sources

    async with _client(handler) as http:
        result = await fetch_repair_info(
            SIDEKICK, dtc="P0300", year=None, make=None, model=None, client=http
        )

    assert result["available"] is True
    assert result["summary"] == "minimal response"
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_fetch_repair_info_drops_malformed_source_entries() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "summary": "mixed",
                "sources": [
                    {"title": "ok", "url": "u", "excerpt": "e"},
                    "not-a-dict",
                    42,
                    {"title": "ok2"},  # partial is allowed
                ],
            }
        )

    async with _client(handler) as http:
        result = await fetch_repair_info(
            SIDEKICK, dtc="P0300", year=None, make=None, model=None, client=http
        )
    assert result["available"] is True
    titles = [s["title"] for s in result["sources"]]
    assert titles == ["ok", "ok2"]
