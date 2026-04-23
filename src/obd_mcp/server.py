"""FastMCP server wiring: lifespan, tool registration, stdio entrypoint.

Every tool body is a thin adapter: resolve the lifespan-scoped ObdClient
and DtcDatabase, then delegate to a pure async function in `tools`.
Destructive operations (`clear_dtcs`) route the elicitation through the
FastMCP Context; test suites bypass the server and exercise the `tools`
module directly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.elicitation import AcceptedElicitation
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from obd_mcp import tools as T
from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DtcDatabase

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


mcp = FastMCP("obd-mcp", lifespan=lifespan)


def _app(ctx: Context) -> AppContext:  # type: ignore[type-arg]
    app = ctx.request_context.lifespan_context
    assert isinstance(app, AppContext)
    return app


@mcp.tool()
def ping() -> str:
    """Health check. Returns 'pong' if the server is alive."""
    return "pong"


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def get_vehicle_info(ctx: Context) -> dict[str, Any]:  # type: ignore[type-arg]
    """VIN, calibration IDs, protocol, adapter voltage, and link status.

    Fields the ECU or adapter doesn't report come back as null.
    """
    return await T.get_vehicle_info(_app(ctx).client)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def list_supported_pids(ctx: Context) -> list[dict[str, str]]:  # type: ignore[type-arg]
    """List Mode 01 PIDs the connected ECU advertises support for."""
    return await T.list_supported_pids(_app(ctx).client)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def read_live_data(
    ctx: Context,  # type: ignore[type-arg]
    pids: list[str],
) -> list[dict[str, Any]]:
    """Snapshot-read one or more Mode 01 PIDs by name (e.g. ["RPM", "SPEED"]).

    Unknown or unsupported PIDs are surfaced as structured error entries,
    not exceptions. Each reading carries its own timestamp.
    """
    return await T.read_live_data(_app(ctx).client, pids)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def read_dtcs(
    ctx: Context,  # type: ignore[type-arg]
    scope: str = "all",
) -> dict[str, Any]:
    """Read stored and/or pending DTCs. scope ∈ {"stored", "pending", "all"}.

    Descriptions are joined from the bundled Wal33D DB (generic SAE
    definitions); if a code has no generic entry we fall back to the wire
    description from python-OBD.
    """
    app = _app(ctx)
    return await T.read_dtcs(app.client, scope=scope, dtc_db=app.dtc_db)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def read_freeze_frame(
    ctx: Context,  # type: ignore[type-arg]
    frame_index: int = 0,
) -> dict[str, Any]:
    """Mode 02 snapshot of sensor state at the moment a DTC was set.

    Pair with `read_dtcs` to explain what the engine was doing when a code
    triggered: RPM, speed, coolant temp, fuel trims, etc. Only `frame_index=0`
    (most recent frame) is supported in this release.
    """
    return await T.read_freeze_frame(_app(ctx).client, frame_index=frame_index)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def read_readiness_monitors(ctx: Context) -> dict[str, Any]:  # type: ignore[type-arg]
    """Emissions-readiness monitor completion status.

    Returns every applicable monitor with complete=true/false. An
    incomplete applicable monitor means clearing DTCs would leave the
    vehicle in an emissions-inspection-failing state until driven enough
    for that monitor to re-run.
    """
    return await T.read_readiness_monitors(_app(ctx).client)


class _ClearDtcsConfirmation(BaseModel):
    confirm: bool = Field(
        description=(
            "Set to true to proceed with clearing DTCs. This will reset "
            "emissions readiness monitors on the vehicle."
        ),
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def list_manufacturer_signals(
    ctx: Context,  # type: ignore[type-arg]  # noqa: ARG001
    make: str,
    model: str,
    year: int | None = None,
) -> dict[str, Any]:
    """Bundled manufacturer-specific Mode 22 signal catalogue.

    Returns the OBDb signal list for Ford Mustang and F-150 (the dev
    fleet subset this release vendors). Useful for an LLM to narrate
    what per-make data exists for the connected vehicle — even though
    obd-mcp does not issue Mode 22 reads itself yet. Unsupported
    vehicles return an in-band `{available: false, reason:
    "NO_SIGNAL_SET"}`; generic Mode 01 via `read_live_data` still works.
    """
    return await T.list_manufacturer_signals(year=year, make=make, model=model)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def lookup_recalls_and_complaints(
    ctx: Context,  # type: ignore[type-arg]  # noqa: ARG001
    year: int,
    make: str,
    model: str,
) -> dict[str, Any]:
    """NHTSA safety recalls and consumer complaints for a year/make/model.

    Recalls are binding safety campaigns; complaints are unverified owner
    reports that often precede a recall. Pair with `get_vehicle_info` to
    pick up year/make/model from the VIN automatically. TSBs and open
    investigations are not available — NHTSA's public API does not serve
    them.
    """
    return await T.lookup_recalls_and_complaints(year=year, make=make, model=model)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def clear_dtcs(ctx: Context) -> dict[str, Any]:  # type: ignore[type-arg]
    """Clear stored DTCs and freeze-frame data (Mode 04).

    Destructive: resets emissions readiness monitors. The client is asked
    to confirm via MCP elicitation, and the prompt surfaces the list of
    incomplete monitors that will be reset. If the user declines or the
    client does not support elicitation, no Mode 04 request is sent.
    """

    async def confirm(message: str, _incomplete: list[str]) -> bool:
        result = await ctx.elicit(message=message, schema=_ClearDtcsConfirmation)
        return isinstance(result, AcceptedElicitation) and result.data.confirm

    return await T.clear_dtcs(_app(ctx).client, confirm)


def register_sidekick_tool(server: FastMCP, sidekick_url: str) -> None:
    """Attach `lookup_repair_info` to `server`, bound to `sidekick_url`.

    Factored out so it can be invoked at import-time when the env var is
    set, and directly from tests with a stub URL.
    """

    @server.tool(
        name="lookup_repair_info",
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
    )
    async def _lookup_repair_info(
        ctx: Context,  # type: ignore[type-arg]  # noqa: ARG001
        dtc: str,
        year: int | None = None,
        make: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Look up repair information for a DTC via Mechanics Sidekick.

        Returns `{available, summary, sources, error, dtc, year, make,
        model, timestamp}`. When Sidekick is unreachable or returns a
        non-200, `available` is False and `error` carries a short reason
        — the tool never raises.
        """
        return await T.lookup_repair_info(
            sidekick_url=sidekick_url,
            dtc=dtc,
            year=year,
            make=make,
            model=model,
        )


_SIDEKICK_URL = os.environ.get("SIDEKICK_URL")
if _SIDEKICK_URL:
    register_sidekick_tool(mcp, _SIDEKICK_URL)


def main() -> None:
    mcp.run()
