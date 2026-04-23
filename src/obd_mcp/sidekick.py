"""Mechanics Sidekick `repair-lookup` passthrough.

Sidekick is a separate project (the user's RAG repair-manual chat). This
module is just the HTTP bridge so `obd-mcp` can forward DTC lookups to
it. The contract is intentionally narrow:

    POST {SIDEKICK_URL}/repair-lookup
    {
      "dtc": "P0300",
      "year": 2025 | null,
      "make": "Ford" | null,
      "model": "Mustang" | null
    }

    200 OK
    {
      "summary": "<human-readable synthesis>",
      "sources": [{"title": "...", "url": "...", "excerpt": "..."}, ...]
    }

Any failure (network, non-200, bad JSON) collapses to an `available=False`
envelope with a human-readable `error`. Sidekick-less deployments should
omit `SIDEKICK_URL` entirely — the tool won't register at all, rather
than silently returning empty results.
"""

from __future__ import annotations

from typing import Any

import httpx

SIDEKICK_TIMEOUT = 15.0


def _error(message: str) -> dict[str, Any]:
    return {"available": False, "error": message, "summary": None, "sources": []}


def _normalize(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"available": True, "error": None, "summary": None, "sources": []}
    summary = payload.get("summary")
    summary = summary if isinstance(summary, str) else None
    sources_raw = payload.get("sources")
    sources: list[dict[str, Any]] = []
    if isinstance(sources_raw, list):
        for src in sources_raw:
            if not isinstance(src, dict):
                continue
            sources.append(
                {
                    "title": src.get("title"),
                    "url": src.get("url"),
                    "excerpt": src.get("excerpt"),
                }
            )
    return {"available": True, "error": None, "summary": summary, "sources": sources}


async def fetch_repair_info(
    sidekick_url: str,
    *,
    dtc: str,
    year: int | None,
    make: str | None,
    model: str | None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST to `{sidekick_url}/repair-lookup` and normalize the response.

    Returns an envelope of `{available, error, summary, sources}`. Never
    raises: Sidekick outage is an expected condition the caller (and LLM)
    should be able to narrate.
    """
    endpoint = sidekick_url.rstrip("/") + "/repair-lookup"
    body = {"dtc": dtc, "year": year, "make": make, "model": model}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=SIDEKICK_TIMEOUT)

    try:
        resp = await client.post(endpoint, json=body)
        if resp.status_code != 200:
            return _error(f"sidekick returned HTTP {resp.status_code}")
        payload = resp.json()
    except httpx.HTTPError as e:
        return _error(f"sidekick unreachable ({type(e).__name__})")
    except ValueError:
        return _error("sidekick returned non-JSON body")
    finally:
        if owns_client:
            await client.aclose()

    return _normalize(payload)
