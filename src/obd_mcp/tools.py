"""Pure-logic async tool handlers.

Each function takes an `ObdClient` (and optionally a `DtcDatabase`) and
returns a JSON-serializable dict / list. FastMCP tool wrappers in
`server.py` thread these through the server's lifespan context. Keeping
the logic parameter-driven makes integration tests trivial: the fixture
spins a simulator, tests call these functions directly.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import obd
from obd.codes import BASE_TESTS, COMPRESSION_TESTS, SPARK_TESTS

from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DtcDatabase
from obd_mcp.nhtsa import lookup_complaints, lookup_recalls
from obd_mcp.sidekick import fetch_repair_info
from obd_mcp.vin import decode_vin

DTC_SCOPES: frozenset[str] = frozenset({"stored", "pending", "all"})

# confirmer for destructive tools: receives the human-readable warning and the
# list of incomplete monitor names; returns True iff the user/agent approves.
ConfirmFn = Callable[[str, list[str]], Awaitable[bool]]


def _serialize_value(value: Any) -> Any:
    """Coerce a python-OBD response value to something JSON-safe.

    - pint Quantity → {"magnitude": float, "unit": str}
    - Status / list / scalar → passed through
    """
    if value is None:
        return None
    # pint Quantity (dimensionful numeric)
    if hasattr(value, "magnitude") and hasattr(value, "units"):
        return {"magnitude": float(value.magnitude), "unit": str(value.units)}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, tuple):
        return [_serialize_value(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


async def get_vehicle_info(
    client: ObdClient,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Identity + link info for the attached vehicle.

    Fields that the ECU or adapter doesn't provide come back as `None`.
    When a VIN is available, it is enriched via NHTSA vPIC into
    `vin_decoded` (year / make / model / displacement / ...). Enrichment
    failures are silent — `vin_decoded` is `None` and the rest of the
    payload is unchanged.

    `http_client` is test-injection; production passes `None` and we
    spin an ephemeral client for the one lookup.
    """
    vin_resp = await client.query(obd.commands.VIN)
    calib_resp = await client.query(obd.commands.CALIBRATION_ID)
    cvn_resp = await client.query(obd.commands.CVN)
    voltage_resp = await client.query(obd.commands.ELM_VOLTAGE)

    voltage: float | None = None
    if not voltage_resp.is_null() and voltage_resp.value is not None:
        voltage = float(voltage_resp.value.magnitude)

    vin_value = None if vin_resp.is_null() else _serialize_value(vin_resp.value)
    vin_decoded: dict[str, Any] | None = None
    if isinstance(vin_value, str) and vin_value.strip():
        vin_decoded = await decode_vin(vin_value, client=http_client)

    return {
        "vin": vin_value,
        "vin_decoded": vin_decoded,
        "calibration_ids": None if calib_resp.is_null() else _serialize_value(calib_resp.value),
        "cvn": None if cvn_resp.is_null() else _serialize_value(cvn_resp.value),
        "voltage_volts": voltage,
        "protocol": await client.protocol_name(),
        "port": await client.port_name(),
        "status": await client.status(),
        "timestamp": time.time(),
    }


async def list_supported_pids(client: ObdClient) -> list[dict[str, str]]:
    """Mode 01 PIDs the ECU advertises support for."""
    cmds = await client.supported_commands()
    return [
        {
            "pid": c.command.decode("ascii"),
            "name": c.name,
            "description": c.desc,
        }
        for c in cmds
    ]


async def read_live_data(client: ObdClient, pids: list[str]) -> list[dict[str, Any]]:
    """Snapshot reading of one or more Mode 01 PIDs, decoded.

    For each name in `pids`:
      - unknown name → {error: "UNKNOWN_PID"}
      - known but not advertised by ECU → {error: "NOT_SUPPORTED"}
      - query returned nothing → {error: "NO_DATA"}
      - success → {value, unit}  (unit may be null for non-dimensional values)
    """
    results: list[dict[str, Any]] = []
    for name in pids:
        now = time.time()
        if not obd.commands.has_name(name):
            results.append({"pid": None, "name": name, "error": "UNKNOWN_PID", "timestamp": now})
            continue
        cmd = obd.commands[name]
        pid_hex = cmd.command.decode("ascii")
        if not await client.supports(cmd):
            results.append(
                {"pid": pid_hex, "name": name, "error": "NOT_SUPPORTED", "timestamp": now}
            )
            continue
        resp = await client.query(cmd)
        if resp.is_null():
            results.append(
                {"pid": pid_hex, "name": name, "error": "NO_DATA", "timestamp": time.time()}
            )
            continue
        serialized = _serialize_value(resp.value)
        if isinstance(serialized, dict) and "magnitude" in serialized and "unit" in serialized:
            value: Any = serialized["magnitude"]
            unit: str | None = serialized["unit"]
        else:
            value = serialized
            unit = None
        results.append(
            {
                "pid": pid_hex,
                "name": name,
                "value": value,
                "unit": unit,
                "timestamp": time.time(),
            }
        )
    return results


def _enrich_dtc(
    code: str,
    scope: str,
    wire_description: str,
    dtc_db: DtcDatabase | None,
) -> dict[str, Any]:
    description: str | None = None
    if dtc_db is not None:
        rows = dtc_db.lookup(code)
        if rows:
            description = rows[0].description
    if description is None and wire_description:
        description = wire_description
    return {"code": code, "scope": scope, "description": description}


async def read_dtcs(
    client: ObdClient,
    scope: str = "all",
    dtc_db: DtcDatabase | None = None,
) -> dict[str, Any]:
    """Read stored / pending DTCs and join with the Wal33D description DB.

    `scope` ∈ {"stored", "pending", "all"}. Permanent DTCs (Mode 0A) are
    not implemented in Phase 1 — python-OBD has no built-in command for
    them and the simulator does not emit them either.
    """
    if scope not in DTC_SCOPES:
        raise ValueError(f"scope must be one of {sorted(DTC_SCOPES)}, got {scope!r}")

    codes: list[dict[str, Any]] = []

    if scope in {"stored", "all"}:
        resp = await client.query(obd.commands.GET_DTC)
        if not resp.is_null() and resp.value:
            for code, wire_desc in resp.value:
                codes.append(_enrich_dtc(code, "stored", wire_desc, dtc_db))

    if scope in {"pending", "all"}:
        resp = await client.query(obd.commands.GET_CURRENT_DTC)
        if not resp.is_null() and resp.value:
            for code, wire_desc in resp.value:
                codes.append(_enrich_dtc(code, "pending", wire_desc, dtc_db))

    return {
        "scope": scope,
        "count": len(codes),
        "codes": codes,
        "timestamp": time.time(),
    }


async def read_freeze_frame(client: ObdClient, frame_index: int = 0) -> dict[str, Any]:
    """Mode 02 snapshot of the sensor state when a DTC was captured.

    Returns `{available, reason, dtc, frame, timestamp}`. `dtc` is `{code,
    description}` identifying which trouble code triggered the capture;
    `frame` is a dict of `{pid_name: {value, unit}}` for every freeze-
    frame PID the ECU reports. When no DTC is stored the ECU returns a
    null DTC and we surface `available=False`, `reason="NO_FREEZE_FRAME"`.

    python-OBD does not append a frame-index byte to Mode 02 requests, so
    only `frame_index=0` (the most recent frame) is reachable via the
    standard command set. Non-zero indices are rejected in-band rather
    than silently falling back — ECUs that store multiple freeze frames
    are uncommon and need raw-command support (future work).
    """
    if frame_index != 0:
        return {
            "available": False,
            "reason": "FRAME_INDEX_NOT_SUPPORTED",
            "dtc": None,
            "frame": {},
            "frame_index": frame_index,
            "timestamp": time.time(),
        }

    dtc_resp = await client.query(obd.commands.DTC_FREEZE_DTC)
    if dtc_resp.is_null() or dtc_resp.value is None:
        return {
            "available": False,
            "reason": "NO_FREEZE_FRAME",
            "dtc": None,
            "frame": {},
            "frame_index": 0,
            "timestamp": time.time(),
        }

    code, wire_desc = dtc_resp.value

    readings: dict[str, dict[str, Any]] = {}
    for cmd in obd.commands.modes[2]:
        if cmd is None:
            continue
        if cmd.name == "DTC_FREEZE_DTC" or cmd.name.startswith("DTC_PIDS_"):
            continue
        if not await client.supports(cmd):
            continue
        resp = await client.query(cmd)
        if resp.is_null():
            continue
        serialized = _serialize_value(resp.value)
        if isinstance(serialized, dict) and "magnitude" in serialized and "unit" in serialized:
            readings[cmd.name] = {
                "value": serialized["magnitude"],
                "unit": serialized["unit"],
            }
        else:
            readings[cmd.name] = {"value": serialized, "unit": None}

    return {
        "available": True,
        "reason": None,
        "dtc": {"code": code, "description": wire_desc},
        "frame": readings,
        "frame_index": 0,
        "timestamp": time.time(),
    }


async def read_readiness_monitors(client: ObdClient) -> dict[str, Any]:
    """Emissions-readiness monitors from Mode 01 PID 01 (STATUS).

    Returns every ECU-advertised monitor with `applicable=True`. A monitor
    with `applicable=True` and `complete=False` is "incomplete" — clearing
    DTCs will reset it and may cause emissions-inspection failures.
    """
    resp = await client.query(obd.commands.STATUS)
    if resp.is_null() or resp.value is None:
        return {
            "available": False,
            "mil": None,
            "dtc_count": None,
            "ignition_type": None,
            "monitors": [],
            "timestamp": time.time(),
        }
    status = resp.value
    monitors: list[dict[str, Any]] = []
    for test_name in BASE_TESTS + SPARK_TESTS + COMPRESSION_TESTS:
        if test_name is None:
            continue
        test = getattr(status, test_name, None)
        if test is None:
            continue
        if not test.available:
            # Not applicable to this vehicle — skip to reduce noise.
            continue
        monitors.append(
            {
                "name": test_name,
                "applicable": True,
                "complete": bool(test.complete),
            }
        )
    return {
        "available": True,
        "mil": bool(status.MIL),
        "dtc_count": int(status.DTC_count),
        "ignition_type": status.ignition_type or None,
        "monitors": monitors,
        "timestamp": time.time(),
    }


def _build_clear_dtcs_prompt(readiness: dict[str, Any]) -> tuple[str, list[str]]:
    incomplete = [m["name"] for m in readiness["monitors"] if not m["complete"]]
    parts = [
        "Clearing DTCs will erase stored trouble codes and reset emissions "
        "readiness monitors. This is a destructive, non-reversible action on "
        "the vehicle.",
        "If your jurisdiction requires an emissions inspection, clearing may "
        "cause the next inspection to fail until the vehicle has been driven "
        "long enough for the monitors to re-run and report complete.",
    ]
    if incomplete:
        parts.append(f"Incomplete monitors that will be reset: {', '.join(incomplete)}.")
    else:
        parts.append("All applicable monitors are currently COMPLETE.")
    parts.append("Proceed with clearing DTCs?")
    return "\n\n".join(parts), incomplete


async def clear_dtcs(client: ObdClient, confirm: ConfirmFn) -> dict[str, Any]:
    """Mode 04: clear stored DTCs and freeze-frame data.

    Gated by a runtime confirmation via the `confirm` callback. The callback
    is expected to surface the readiness-monitor warning to the user (via
    MCP elicitation in production; a test shim in the unit suite).
    """
    readiness = await read_readiness_monitors(client)
    prompt, incomplete = _build_clear_dtcs_prompt(readiness)

    approved = await confirm(prompt, incomplete)
    if not approved:
        return {
            "cleared": False,
            "reason": "user_declined",
            "readiness_before": readiness,
            "timestamp": time.time(),
        }

    resp = await client.query(obd.commands.CLEAR_DTC)
    # CLEAR_DTC has a `drop` decoder: successful ack yields value=None but
    # `messages` is non-empty. Treat non-empty messages as success.
    wire_ok = bool(resp.messages)
    return {
        "cleared": wire_ok,
        "reason": None if wire_ok else "adapter_no_response",
        "readiness_before": readiness,
        "timestamp": time.time(),
    }


async def lookup_recalls_and_complaints(
    *,
    year: int,
    make: str,
    model: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """NHTSA safety recalls and consumer complaints for a year/make/model.

    Fires both endpoints in parallel; each is best-effort. If one is down
    and the other answers, the response still carries what we got. If
    both fail, identity fields are still populated so the LLM can surface
    a useful "lookup unavailable" message without re-asking for inputs.

    TSBs (technical service bulletins) and investigations are out of
    scope: NHTSA does not expose either via its public API. See
    `docs/DECISIONS.md` for the rationale behind the tool's scope.
    """
    owns_client = http_client is None
    if http_client is None:
        from obd_mcp.nhtsa import NHTSA_TIMEOUT

        http_client = httpx.AsyncClient(timeout=NHTSA_TIMEOUT)

    try:
        recalls, complaints = await asyncio.gather(
            lookup_recalls(make, model, year, client=http_client),
            lookup_complaints(make, model, year, client=http_client),
        )
    finally:
        if owns_client:
            await http_client.aclose()

    return {
        "year": year,
        "make": make,
        "model": model,
        "recalls": recalls,
        "complaints": complaints,
        "timestamp": time.time(),
    }


async def lookup_repair_info(
    *,
    sidekick_url: str,
    dtc: str,
    year: int | None = None,
    make: str | None = None,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Forward a DTC + vehicle context to a Mechanics Sidekick endpoint.

    Sidekick (a separate RAG project) owns the repair corpus; `obd-mcp`
    just proxies. On any Sidekick outage the response envelope collapses
    to `available=False` with a human-readable `error` — the caller's
    context (dtc/year/make/model) is echoed back so the LLM can narrate
    the outage without re-asking the user.
    """
    sidekick_response = await fetch_repair_info(
        sidekick_url,
        dtc=dtc,
        year=year,
        make=make,
        model=model,
        client=http_client,
    )
    return {
        **sidekick_response,
        "dtc": dtc,
        "year": year,
        "make": make,
        "model": model,
        "timestamp": time.time(),
    }
