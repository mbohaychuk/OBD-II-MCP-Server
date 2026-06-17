"""Structured error taxonomy surfaced to MCP clients.

FastMCP forwards the string form of any raised exception as the content
of a `CallToolResult` with `isError=true` (and does NOT forward a
traceback). Prefixing `__str__` with `[CODE]` gives the LLM a reliable
key to reason over without needing a bespoke MCP structured-error shape.
"""

from __future__ import annotations

from enum import StrEnum


class ObdErrorCode(StrEnum):
    """Connection-level failures `ObdClient` raises as an `ObdError`.

    Only the codes actually reachable today. python-OBD reports adapter
    timeouts and CAN faults as a null `OBDResponse` rather than an exception,
    so timeout / CAN-error codes are deferred until they can be detected from
    raw ELM327 reply tokens and validated on real hardware (see DECISIONS.md).
    Per-PID outcomes (`NO_DATA` / `NOT_SUPPORTED` / `UNKNOWN_PID`) are returned
    in-band by `read_live_data` as data — they are not raised here.
    """

    UNABLE_TO_CONNECT = "UNABLE_TO_CONNECT"
    BUS_INIT_ERROR = "BUS_INIT_ERROR"


class ObdError(Exception):
    """Tool-surface error from the OBD layer.

    Raised by `ObdClient` when the adapter or ECU conversation fails in a
    way that prevents the caller from getting meaningful data back. Per-PID
    "no data" cases inside `read_live_data` are returned in-band instead —
    those are data, not transport failures.
    """

    def __init__(self, code: ObdErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code.value}] {message}")
