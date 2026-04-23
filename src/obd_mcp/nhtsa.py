"""NHTSA recalls + complaints helpers.

Best-effort enrichment mirroring `vin.decode_vin`: any network failure,
HTTP error, or malformed payload collapses to `[]` — the caller never
blocks on these lookups. Keys are normalized to snake_case so the MCP
payload is consistent across both endpoints (NHTSA uses both camelCase
and PascalCase on the wire).

Endpoints (public, no authentication required):
  - GET https://api.nhtsa.gov/recalls/recallsByVehicle
  - GET https://api.nhtsa.gov/complaints/complaintsByVehicle

NHTSA does not publish TSB content via a public API — technical service
bulletins are copyrighted manufacturer material. Investigations are
likewise not exposed on the public endpoint (the
`investigationsByVehicle` path returns "Missing Authentication Token").
Scope here is what the API actually serves.
"""

from __future__ import annotations

from typing import Any

import httpx

RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
COMPLAINTS_URL = "https://api.nhtsa.gov/complaints/complaintsByVehicle"
NHTSA_TIMEOUT = 5.0


async def _fetch_results(
    url: str,
    params: dict[str, str],
    client: httpx.AsyncClient | None,
) -> list[dict[str, Any]]:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=NHTSA_TIMEOUT)
    try:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    return [row for row in results if isinstance(row, dict)]


def _recall_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "campaign_number": row.get("NHTSACampaignNumber"),
        "manufacturer": row.get("Manufacturer"),
        "component": row.get("Component"),
        "summary": row.get("Summary"),
        "consequence": row.get("Consequence"),
        "remedy": row.get("Remedy"),
        "report_received_date": row.get("ReportReceivedDate"),
        "park_it": bool(row.get("parkIt", False)),
        "park_outside": bool(row.get("parkOutSide", False)),
        "ota_update": bool(row.get("overTheAirUpdate", False)),
    }


def _complaint_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "odi_number": row.get("odiNumber"),
        "component": row.get("components"),
        "summary": row.get("summary"),
        "crash": bool(row.get("crash", False)),
        "fire": bool(row.get("fire", False)),
        "injuries": int(row.get("numberOfInjuries") or 0),
        "deaths": int(row.get("numberOfDeaths") or 0),
        "incident_date": row.get("dateOfIncident"),
        "filed_date": row.get("dateComplaintFiled"),
    }


async def lookup_recalls(
    make: str,
    model: str,
    year: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """NHTSA safety-recall campaigns for a specific year/make/model."""
    rows = await _fetch_results(
        RECALLS_URL,
        {"make": make, "model": model, "modelYear": str(year)},
        client,
    )
    return [_recall_row(row) for row in rows]


async def lookup_complaints(
    make: str,
    model: str,
    year: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """NHTSA consumer complaint reports for a specific year/make/model."""
    rows = await _fetch_results(
        COMPLAINTS_URL,
        {"make": make, "model": model, "modelYear": str(year)},
        client,
    )
    return [_complaint_row(row) for row in rows]
