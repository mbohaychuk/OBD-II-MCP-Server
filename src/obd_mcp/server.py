"""FastMCP server wiring: lifespan, tool registration, stdio entrypoint.

Every tool body is a thin adapter: resolve the lifespan-scoped ObdClient
and DtcDatabase, then delegate to a pure async function in `tools`.
Destructive operations (`clear_dtcs`) route the elicitation through the
FastMCP Context; test suites bypass the server and exercise the `tools`
module directly.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from mcp.server.elicitation import AcceptedElicitation
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ClientCapabilities, ElicitationCapability, ToolAnnotations
from pydantic import BaseModel, Field

from obd_mcp import tools as T
from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DtcDatabase
from obd_mcp.schemas import (
    ClearDtcsResult,
    DtcReport,
    FreezeFrame,
    LiveReading,
    ManufacturerSignals,
    ReadinessReport,
    RecallsAndComplaints,
    SessionRecording,
    SupportedPid,
    VehicleInfo,
)

DEFAULT_PORT_URL = "socket://localhost:35000"


@dataclass
class AppContext:
    client: ObdClient
    dtc_db: DtcDatabase


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    port = os.environ.get("OBD_PORT", DEFAULT_PORT_URL)
    client = ObdClient(portstr=port)
    dtc_db = DtcDatabase()
    try:
        yield AppContext(client=client, dtc_db=dtc_db)
    finally:
        await client.close()
        dtc_db.close()


SERVER_INSTRUCTIONS = """\
obd-mcp bridges this host to a vehicle's OBD-II port via an ELM327 adapter.

Suggested flow: call get_vehicle_info first — it returns the VIN and decodes
year/make/model, which feed lookup_recalls_and_complaints and the `make`
argument of read_dtcs (needed to resolve manufacturer-specific codes). Before
clear_dtcs, run read_readiness_monitors / read_dtcs so the user sees what will
be erased; clear_dtcs is destructive and requires the user to confirm via
elicitation.

Error convention: only connection-level failures (UNABLE_TO_CONNECT,
BUS_INIT_ERROR) surface as tool errors. Per-PID outcomes (NOT_SUPPORTED,
NO_DATA, UNKNOWN_PID, NOT_A_READABLE_PID) are returned in-band as data, so a
partial result is normal — inspect each row's `error` field rather than
treating the whole call as failed."""

mcp = FastMCP("obd-mcp", lifespan=lifespan, instructions=SERVER_INSTRUCTIONS)

# Session store for `record_session`. Module-level because the MCP
# resource handler for `obd://sessions/{id}.json` can't receive the
# lifespan context — and since the FastMCP instance is a singleton per
# server process, the lifetimes match. Sessions die with the process,
# matching DECISIONS.md: "in-memory only, no disk persistence".
_SESSIONS: dict[str, dict[str, Any]] = {}


def _app(ctx: Context) -> AppContext:  # type: ignore[type-arg]
    app = ctx.request_context.lifespan_context
    assert isinstance(app, AppContext)
    return app


@mcp.resource(
    "obd://sessions/{session_id}.json",
    name="obd_session",
    title="Recorded OBD session",
    description=(
        "The PID timeseries captured by a record_session call, as JSON. "
        "In-memory for the server process lifetime; the session_id comes from "
        "record_session's resource_uri."
    ),
    mime_type="application/json",
)
async def _session_resource(session_id: str) -> str:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise ValueError(f"session {session_id!r} not found")
    return json.dumps(session)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def ping() -> str:
    """Health check. Returns 'pong' if the server is alive."""
    return "pong"


@mcp.tool(
    # openWorldHint: reaches NHTSA vPIC to decode the VIN.
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True),
)
async def get_vehicle_info(ctx: Context) -> VehicleInfo:  # type: ignore[type-arg]
    """VIN, calibration IDs, protocol, adapter voltage, and link status.

    Fields the ECU or adapter doesn't report come back as null.
    """
    return cast(VehicleInfo, await T.get_vehicle_info(_app(ctx).client))


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
async def list_supported_pids(ctx: Context) -> list[SupportedPid]:  # type: ignore[type-arg]
    """List Mode 01 PIDs the connected ECU advertises support for."""
    return cast(list[SupportedPid], await T.list_supported_pids(_app(ctx).client))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def read_live_data(
    ctx: Context,  # type: ignore[type-arg]
    pids: list[str],
) -> list[LiveReading]:
    """Snapshot-read one or more Mode 01/09 PIDs by name (e.g. ["RPM", "SPEED"]).

    Only live-data (Mode 01) and vehicle-info (Mode 09) reads are accepted;
    anything else (a DTC-clear or other command) returns NOT_A_READABLE_PID.
    Unknown or unsupported PIDs are surfaced as structured error entries, not
    exceptions. Each reading carries its own timestamp.
    """
    return cast(list[LiveReading], await T.read_live_data(_app(ctx).client, pids))


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
async def read_dtcs(
    ctx: Context,  # type: ignore[type-arg]
    scope: str = "all",
    make: str | None = None,
) -> DtcReport:
    """Read stored and/or pending DTCs. scope ∈ {"stored", "pending", "all"}.

    Descriptions are joined from the bundled Wal33D DB. Each code carries a
    `source` of "generic", "manufacturer", or "wire". Pass `make` (e.g. the
    one `get_vehicle_info` decodes from the VIN) to resolve manufacturer-range
    codes — many P1xxx codes have only a generic "Manufacturer Controlled DTC"
    placeholder unless the make's own definition is consulted.
    """
    app = _app(ctx)
    return cast(
        DtcReport, await T.read_dtcs(app.client, scope=scope, dtc_db=app.dtc_db, manufacturer=make)
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
async def read_freeze_frame(
    ctx: Context,  # type: ignore[type-arg]
    frame_index: int = 0,
) -> FreezeFrame:
    """Mode 02 snapshot of sensor state at the moment a DTC was set.

    Pair with `read_dtcs` to explain what the engine was doing when a code
    triggered: RPM, speed, coolant temp, fuel trims, etc. Only `frame_index=0`
    (most recent frame) is supported in this release.
    """
    return cast(FreezeFrame, await T.read_freeze_frame(_app(ctx).client, frame_index=frame_index))


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
async def read_readiness_monitors(ctx: Context) -> ReadinessReport:  # type: ignore[type-arg]
    """Emissions-readiness monitor completion status.

    Returns every applicable monitor with complete=true/false. An
    incomplete applicable monitor means clearing DTCs would leave the
    vehicle in an emissions-inspection-failing state until driven enough
    for that monitor to re-run.
    """
    return cast(ReadinessReport, await T.read_readiness_monitors(_app(ctx).client))


class _ClearDtcsConfirmation(BaseModel):
    confirm: bool = Field(
        description=(
            "Set to true to proceed with clearing DTCs. This will reset "
            "emissions readiness monitors on the vehicle."
        ),
    )


@mcp.tool(
    # Not read-only: it mints a session_id and persists the timeseries under an
    # obd:// resource, modifying server state. Non-destructive and not
    # idempotent (each call yields a new session).
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def record_session(
    ctx: Context,  # type: ignore[type-arg]
    duration_s: float,
    pids: list[str],
    hz_target: float = 1.0,
) -> SessionRecording:
    """Record a time-bounded sample of one or more PIDs.

    Streams progress via MCP progress notifications. The full timeseries
    is returned inline *and* saved to the server's session store; the
    response's `resource_uri` can be fetched later via `resources/read`
    to re-obtain the samples without re-recording.

    Sessions live in memory only and are lost on server restart.
    Bounds: `duration_s ∈ (0, 600]`, `hz_target ∈ (0, 20]`.
    """

    async def _emit(current: int, total: int) -> None:
        await ctx.report_progress(current, total)

    result = await T.record_session(
        _app(ctx).client,
        duration_s=duration_s,
        pids=pids,
        hz_target=hz_target,
        progress=_emit,
    )
    _SESSIONS[result["session_id"]] = result
    return cast(SessionRecording, result)


@mcp.tool(
    # Bundled OBDb catalogue only — no network.
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
async def list_manufacturer_signals(
    ctx: Context,  # type: ignore[type-arg]  # noqa: ARG001
    make: str,
    model: str,
    year: int | None = None,
) -> ManufacturerSignals:
    """Bundled manufacturer-specific Mode 22 signal catalogue.

    Returns the OBDb signal list for Ford Mustang and F-150 (the dev
    fleet subset this release vendors). Useful for an LLM to narrate
    what per-make data exists for the connected vehicle — even though
    obd-mcp does not issue Mode 22 reads itself yet. Unsupported
    vehicles return an in-band `{available: false, reason:
    "NO_SIGNAL_SET"}`; generic Mode 01 via `read_live_data` still works.

    Pass `year` to filter to signals valid for that model year. Omitting
    it returns the full catalogue across all model years (a superset that
    may include signals not applicable to a specific vehicle).
    """
    return cast(
        ManufacturerSignals, await T.list_manufacturer_signals(year=year, make=make, model=model)
    )


@mcp.tool(
    # openWorldHint: reaches NHTSA recalls/complaints APIs.
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True),
)
async def lookup_recalls_and_complaints(
    ctx: Context,  # type: ignore[type-arg]  # noqa: ARG001
    year: int,
    make: str,
    model: str,
) -> RecallsAndComplaints:
    """NHTSA safety recalls and consumer complaints for a year/make/model.

    Recalls are binding safety campaigns; complaints are unverified owner
    reports that often precede a recall. Pair with `get_vehicle_info` to
    pick up year/make/model from the VIN automatically. TSBs and open
    investigations are not available — NHTSA's public API does not serve
    them.
    """
    return cast(
        RecallsAndComplaints,
        await T.lookup_recalls_and_complaints(year=year, make=make, model=model),
    )


def _elicitation_approved(result: object) -> bool:
    """True only when the user explicitly accepted the elicitation AND set
    confirm=True. Any other outcome — declined or cancelled — is a refusal,
    so a destructive Mode 04 never runs without affirmative consent."""
    return isinstance(result, AcceptedElicitation) and bool(result.data.confirm)


def _make_clear_confirm(ctx: Context) -> T.ConfirmFn:  # type: ignore[type-arg]
    """Build the clear_dtcs confirmer for one request.

    A host that can't present an elicitation prompt can't give consent, so this
    detects the missing capability up front and raises ElicitationUnsupported
    (→ a clean refusal with an actionable reason) rather than letting
    ctx.elicit() raise a generic tool error. Extracted to a module-level factory
    so this consent-gate logic is unit-testable without a live MCP session.
    """

    async def confirm(message: str, _incomplete: list[str]) -> bool:
        if not ctx.session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability())
        ):
            raise T.ElicitationUnsupported
        result = await ctx.elicit(message=message, schema=_ClearDtcsConfirmation)
        return _elicitation_approved(result)

    return confirm


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def clear_dtcs(ctx: Context) -> ClearDtcsResult:  # type: ignore[type-arg]
    """Clear stored DTCs and freeze-frame data (Mode 04).

    Destructive: resets emissions readiness monitors. The client is asked
    to confirm via MCP elicitation, and the prompt surfaces the list of
    incomplete monitors that will be reset. If the user declines, no Mode 04
    request is sent (`reason="user_declined"`). If the host does not support
    elicitation there is no way to obtain consent, so the clear is refused
    with `reason="elicitation_unsupported"` — switch to a host that supports
    confirmation (e.g. Claude Desktop) to clear codes.
    """
    return cast(ClearDtcsResult, await T.clear_dtcs(_app(ctx).client, _make_clear_confirm(ctx)))


def main() -> None:
    mcp.run()
