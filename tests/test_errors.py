"""Unit tests for the structured error taxonomy.

These exercise only the enum + exception wrapper. The client-layer
mapping from python-OBD status strings to ObdError instances is
covered in `test_client_errors.py` (integration, uses a closed port).
"""

from __future__ import annotations

import pytest

from obd_mcp.errors import ObdError, ObdErrorCode


def test_only_reachable_codes_are_defined() -> None:
    # The enum carries exactly the connection-level codes ObdClient can
    # actually raise. Timeout / CAN-error codes are deferred (see
    # DECISIONS.md); the per-PID NO_DATA / NOT_SUPPORTED / UNKNOWN_PID
    # markers are in-band strings in read_live_data, not raised here.
    assert {c.value for c in ObdErrorCode} == {
        "UNABLE_TO_CONNECT",
        "BUS_INIT_ERROR",
    }


def test_obderror_carries_code_and_message() -> None:
    err = ObdError(ObdErrorCode.UNABLE_TO_CONNECT, "port refused: socket://x:1")
    assert err.code is ObdErrorCode.UNABLE_TO_CONNECT
    assert err.message == "port refused: socket://x:1"


def test_obderror_str_has_prefix_for_llm_parsing() -> None:
    """MCP surfaces `str(exception)` to the client; LLMs key off the prefix."""
    err = ObdError(ObdErrorCode.BUS_INIT_ERROR, "ELM alive, no ECU response")
    assert str(err) == "[BUS_INIT_ERROR] ELM alive, no ECU response"


def test_obderror_is_raiseable_and_catchable() -> None:
    with pytest.raises(ObdError) as exc_info:
        raise ObdError(ObdErrorCode.UNABLE_TO_CONNECT, "port refused")
    assert exc_info.value.code is ObdErrorCode.UNABLE_TO_CONNECT
