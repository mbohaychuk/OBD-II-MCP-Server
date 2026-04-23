"""NHTSA vPIC VIN decoder.

Best-effort enrichment. The public `DecodeVinValues` endpoint requires no
key and is rate-limit-generous in practice. Any network failure, HTTP
error, or malformed payload collapses to `None` — the caller's response
is never blocked on this lookup.
"""

from __future__ import annotations

from typing import Any

import httpx

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}"
VPIC_TIMEOUT = 5.0


def _coerce_str(value: Any) -> str | None:
    """Treat empty string / None / whitespace as absent."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _coerce_int(value: Any) -> int | None:
    s = _coerce_str(value)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _coerce_float(value: Any) -> float | None:
    s = _coerce_str(value)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


async def decode_vin(
    vin: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Look up a VIN against NHTSA vPIC. Returns `None` on any failure.

    The `client` kwarg exists for test injection (MockTransport); in
    production the caller passes a long-lived `httpx.AsyncClient` or
    leaves it `None` to spin a short-lived one per call.
    """
    vin = vin.strip() if vin else ""
    if not vin:
        return None

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=VPIC_TIMEOUT)

    try:
        resp = await client.get(
            VPIC_URL.format(vin=vin),
            params={"format": "json"},
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()

    results = payload.get("Results") if isinstance(payload, dict) else None
    if not results:
        return None
    row = results[0]

    return {
        "year": _coerce_int(row.get("ModelYear")),
        "make": _coerce_str(row.get("Make")),
        "model": _coerce_str(row.get("Model")),
        "trim": _coerce_str(row.get("Trim")),
        "displacement_liters": _coerce_float(row.get("DisplacementL")),
        "cylinders": _coerce_int(row.get("EngineCylinders")),
        "fuel_type": _coerce_str(row.get("FuelTypePrimary")),
        "vehicle_type": _coerce_str(row.get("VehicleType")),
        "body_class": _coerce_str(row.get("BodyClass")),
        "error_code": _coerce_str(row.get("ErrorCode")),
    }
