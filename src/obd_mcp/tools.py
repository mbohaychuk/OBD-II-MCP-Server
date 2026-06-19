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
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import obd
from obd.codes import BASE_TESTS, COMPRESSION_TESTS, SPARK_TESTS

from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DtcDatabase
from obd_mcp.errors import ObdError
from obd_mcp.nhtsa import lookup_complaints, lookup_recalls
from obd_mcp.obdb import load_signals
from obd_mcp.vin import decode_vin

DTC_SCOPES: frozenset[str] = frozenset({"stored", "pending", "all"})

# Bounds for record_session. Capped generously to keep per-call response
# payloads tractable; the LLM can chain calls if a longer recording is
# desired.
RECORD_MAX_DURATION_S: float = 600.0
RECORD_MAX_HZ: float = 20.0


class ElicitationUnsupported(Exception):
    """Raised by a `ConfirmFn` when the host cannot ask the user at all
    (it does not support MCP elicitation). Distinct from the user actively
    declining — both refuse the destructive op, but only this one means
    "no prompt was ever shown," so the caller can tell the user to switch
    to a host that supports confirmation rather than "you declined.\""""


# confirmer for destructive tools: receives the human-readable warning and the
# list of incomplete monitor names; returns True iff the user/agent approves,
# False if the user declines, or raises ElicitationUnsupported if the host
# cannot present the prompt.
ConfirmFn = Callable[[str, list[str]], Awaitable[bool]]

# Progress callback for long-running tools. Current and total are sample
# counts (not bytes or percentage) so the host can compute its own % label.
ProgressFn = Callable[[int, int], Awaitable[None]]


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
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _decode_vin(resp: Any) -> str | None:
    """Decode the VIN from a Mode 09 response.

    python-OBD's encoded-string decoder has a strip bug: its strip set
    accidentally includes the ASCII bytes '0', '1', '2', so it trims those off
    the ends of the VIN — and most North-American VINs start with '1' or '2'.
    The raw response frames are the only place the untruncated VIN survives, so
    decode from there when present, falling back to the decoded value otherwise
    (e.g. a test stub that supplies a value but no frames).
    """
    if resp.is_null():
        return None
    messages = getattr(resp, "messages", None)
    if messages:
        data = getattr(messages[0], "data", None)
        if data:
            # Frame: [mode-echo, pid, NODI, <ascii VIN...>]. Drop the 2-byte
            # mode/pid echo, then strip only NODI / null padding — not ASCII.
            vin = bytes(data)[2:].strip().strip(b"\x00\x01\x02").decode("ascii", errors="replace")
            if vin:
                return vin
    value = resp.value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("ascii", errors="replace")
    if isinstance(value, str) and value.strip():
        return value
    return None


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

    vin_value = _decode_vin(vin_resp)
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


# Modes read_live_data is allowed to dispatch: 01 (live sensor PIDs) and 09
# (vehicle info — VIN, calibration IDs). Every other mode is either destructive
# (Mode 04 CLEAR_DTC) or already has a dedicated tool (Mode 03/07 DTC reads,
# Mode 02 freeze frame). Gating here keeps the one destructive command behind
# clear_dtcs's elicitation instead of reachable as a "PID".
_READABLE_MODES: frozenset[int] = frozenset({1, 9})


def _live_entry(
    pid: str | None,
    name: str,
    *,
    value: Any = None,
    unit: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """A read_live_data row. Every row carries the same keys so callers never
    have to probe whether `value` or `error` is present: on success `error` is
    null; on failure `value`/`unit` are null."""
    return {
        "pid": pid,
        "name": name,
        "value": value,
        "unit": unit,
        "error": error,
        "timestamp": time.time(),
    }


async def read_live_data(client: ObdClient, pids: list[str]) -> list[dict[str, Any]]:
    """Snapshot reading of one or more Mode 01/09 PIDs, decoded.

    Returns one uniform row per requested name (keys: pid, name, value, unit,
    error, timestamp). Exactly one of `value`/`error` is meaningful:
      - unknown name → error="UNKNOWN_PID"
      - known but not a Mode 01/09 read → error="NOT_A_READABLE_PID"
      - known but not advertised by ECU → error="NOT_SUPPORTED"
      - query returned nothing → error="NO_DATA"
      - success → value (+ unit, null for non-dimensional values), error=null
    """
    results: list[dict[str, Any]] = []
    for name in pids:
        if not obd.commands.has_name(name):
            results.append(_live_entry(None, name, error="UNKNOWN_PID"))
            continue
        cmd = obd.commands[name]
        pid_hex = cmd.command.decode("ascii")
        if cmd.mode not in _READABLE_MODES:
            results.append(_live_entry(pid_hex, name, error="NOT_A_READABLE_PID"))
            continue
        if not await client.supports(cmd):
            results.append(_live_entry(pid_hex, name, error="NOT_SUPPORTED"))
            continue
        resp = await client.query(cmd)
        if resp.is_null():
            results.append(_live_entry(pid_hex, name, error="NO_DATA"))
            continue
        serialized = _serialize_value(resp.value)
        if isinstance(serialized, dict) and "magnitude" in serialized and "unit" in serialized:
            results.append(
                _live_entry(pid_hex, name, value=serialized["magnitude"], unit=serialized["unit"])
            )
        else:
            results.append(_live_entry(pid_hex, name, value=serialized))
    return results


def _enrich_dtc(
    code: str,
    scope: str,
    wire_description: str,
    dtc_db: DtcDatabase | None,
    manufacturer: str | None = None,
) -> dict[str, Any]:
    """Join a raw DTC with a human description, recording its provenance.

    With a `manufacturer`, a make-specific row wins over the generic one —
    manufacturer-range codes (e.g. Ford P1xxx) often have only a useless
    "Manufacturer Controlled DTC" generic placeholder, so preferring the
    make's own definition is what makes the bundled per-brand data useful.
    Falls back generic → wire. `source` is one of manufacturer/generic/wire,
    or null when nothing resolved.
    """
    description: str | None = None
    source: str | None = None
    if dtc_db is not None:
        rows = dtc_db.lookup(code, manufacturer=manufacturer)
        mfr_row = next((r for r in rows if r.manufacturer != "GENERIC"), None)
        generic_row = next((r for r in rows if r.manufacturer == "GENERIC"), None)
        if mfr_row is not None:
            description = mfr_row.description
            source = "manufacturer"
        elif generic_row is not None:
            description = generic_row.description
            source = "generic"
    if description is None and wire_description:
        description = wire_description
        source = "wire"
    return {"code": code, "scope": scope, "description": description, "source": source}


async def read_dtcs(
    client: ObdClient,
    scope: str = "all",
    dtc_db: DtcDatabase | None = None,
    manufacturer: str | None = None,
) -> dict[str, Any]:
    """Read stored / pending DTCs and join with the Wal33D description DB.

    `scope` ∈ {"stored", "pending", "all"}. Permanent DTCs (Mode 0A) are
    not implemented in Phase 1 — python-OBD has no built-in command for
    them and the simulator does not emit them either.

    `manufacturer` (e.g. "Ford") opts the join into the make-specific rows so
    manufacturer-range codes resolve to a real description instead of the
    generic placeholder; omit it for generic-only decoding.
    """
    if scope not in DTC_SCOPES:
        raise ValueError(f"scope must be one of {sorted(DTC_SCOPES)}, got {scope!r}")

    codes: list[dict[str, Any]] = []

    if scope in {"stored", "all"}:
        resp = await client.query(obd.commands.GET_DTC)
        if not resp.is_null() and resp.value:
            for code, wire_desc in resp.value:
                codes.append(_enrich_dtc(code, "stored", wire_desc, dtc_db, manufacturer))

    if scope in {"pending", "all"}:
        resp = await client.query(obd.commands.GET_CURRENT_DTC)
        if not resp.is_null() and resp.value:
            for code, wire_desc in resp.value:
                codes.append(_enrich_dtc(code, "pending", wire_desc, dtc_db, manufacturer))

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
    # EGR_VVT_SYSTEM_MONITORING appears in both SPARK_TESTS and
    # COMPRESSION_TESTS, so de-dupe by name to emit each monitor once.
    seen: set[str] = set()
    for test_name in BASE_TESTS + SPARK_TESTS + COMPRESSION_TESTS:
        if test_name is None or test_name in seen:
            continue
        seen.add(test_name)
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

    Fail-closed: Mode 04 is sent only on an affirmative confirm. A declined
    confirm returns `reason="user_declined"`; a host that cannot present the
    prompt at all (no elicitation support) returns
    `reason="elicitation_unsupported"` so the caller can redirect the user to
    a supported host rather than mislabel it a decline.
    """
    readiness = await read_readiness_monitors(client)
    prompt, incomplete = _build_clear_dtcs_prompt(readiness)

    try:
        approved = await confirm(prompt, incomplete)
    except ElicitationUnsupported:
        return {
            "cleared": False,
            "reason": "elicitation_unsupported",
            "readiness_before": readiness,
            "timestamp": time.time(),
        }
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


async def record_session(
    client: ObdClient,
    duration_s: float,
    pids: list[str],
    hz_target: float,
    *,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Time-bounded PID sampling, returning a dense timeseries.

    Samples each PID in `pids` at `hz_target` samples/second for
    `duration_s` seconds. If the underlying read loop is slower than the
    target rate (slow adapter, many PIDs), the actual sample count will
    be lower — we never stack delays to catch up. Per-sample readings use
    the same shape as `read_live_data`, so in-band errors (NOT_SUPPORTED,
    NO_DATA) flow through unchanged.

    `progress`, when supplied, is invoked after each sample with
    `(current, total)` — `total` is the target sample count based on
    hz × duration, not a guarantee.

    Returns `{session_id, resource_uri, duration_s, hz_target, pids,
    samples_count, samples, started_at, ended_early}`. `ended_early` is
    `None` on a clean run, or `{"reason": "[CODE] ..."}` when a transport
    failure cut the recording short — the samples collected before the
    drop are still returned. The caller is expected to stash this under
    `server._SESSIONS[session_id]` so the MCP resource at `resource_uri`
    can serve it back.
    """
    if duration_s <= 0 or duration_s > RECORD_MAX_DURATION_S:
        raise ValueError(f"duration_s must be in (0, {RECORD_MAX_DURATION_S}], got {duration_s}")
    if hz_target <= 0 or hz_target > RECORD_MAX_HZ:
        raise ValueError(f"hz_target must be in (0, {RECORD_MAX_HZ}], got {hz_target}")
    if not pids:
        raise ValueError("pids must contain at least one PID name")

    unknown = [p for p in pids if not obd.commands.has_name(p)]
    if unknown:
        raise ValueError(f"unknown PID name(s): {', '.join(unknown)}")

    not_readable = [p for p in pids if obd.commands[p].mode not in _READABLE_MODES]
    if not_readable:
        raise ValueError(f"not a readable Mode 01/09 PID: {', '.join(not_readable)}")

    session_id = uuid.uuid4().hex[:12]
    interval = 1.0 / hz_target
    started_at = time.time()
    deadline = started_at + duration_s
    target_count = max(1, int(round(duration_s * hz_target)))
    samples: list[dict[str, Any]] = []

    ended_early: dict[str, str] | None = None
    next_t = started_at
    while time.time() < deadline:
        wait = next_t - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            readings = await read_live_data(client, pids)
        except ObdError as err:
            # A transport failure mid-recording (adapter unplugged, bus drop)
            # ends the session early but keeps the samples collected so far,
            # rather than discarding the whole timeseries.
            ended_early = {"reason": str(err)}
            break
        now = time.time()
        samples.append({"t": now - started_at, "readings": readings})
        if progress is not None:
            # target_count is hz×duration; a fast adapter or scheduler jitter can
            # squeeze in an extra sample past it, so clamp to avoid >100% in hosts
            # that render current/total as a percentage.
            await progress(min(len(samples), target_count), target_count)
        next_t = max(next_t + interval, now)

    return {
        "session_id": session_id,
        "resource_uri": f"obd://sessions/{session_id}.json",
        "duration_s": duration_s,
        "hz_target": hz_target,
        "pids": pids,
        "samples_count": len(samples),
        "samples": samples,
        "started_at": started_at,
        "ended_early": ended_early,
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


async def list_manufacturer_signals(
    *,
    year: int | None,
    make: str,
    model: str,
) -> dict[str, Any]:
    """Manufacturer-specific Mode 22 signals bundled for supported vehicles.

    Returns the metadata (signal id, name, request PID, CAN header, unit)
    for signals the OBDb project has catalogued for this make/model.
    Useful context for an LLM to narrate what manufacturer-specific data
    exists for the connected vehicle — even though obd-mcp does not
    issue Mode 22 reads itself yet.

    Unsupported make/model returns `{available: False, reason:
    "NO_SIGNAL_SET", signals: []}` — generic Mode 01 via `read_live_data`
    still works for those.
    """
    signals = load_signals(make, model, year=year)
    if not signals:
        return {
            "available": False,
            "reason": "NO_SIGNAL_SET",
            "year": year,
            "make": make,
            "model": model,
            "signals": [],
            "timestamp": time.time(),
        }
    return {
        "available": True,
        "reason": None,
        "year": year,
        "make": make,
        "model": model,
        "signals": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "unit": s.unit,
                "header": s.header,
                "response_address": s.response_address,
                "request_hex": s.request_hex,
            }
            for s in signals
        ],
        "timestamp": time.time(),
    }
