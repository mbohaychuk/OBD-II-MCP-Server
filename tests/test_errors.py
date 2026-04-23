"""Unit tests for the structured error taxonomy.

These exercise only the enum + exception wrapper. The client-layer
mapping from python-OBD status strings to ObdError instances is
covered in `test_client_errors.py` (integration, uses a closed port).
"""

from __future__ import annotations

import pytest

from obd_mcp.errors import ObdError, ObdErrorCode


def test_all_five_codes_are_defined() -> None:
    assert {c.value for c in ObdErrorCode} == {
        "NO_DATA",
        "BUS_INIT_ERROR",
        "CAN_ERROR",
        "UNABLE_TO_CONNECT",
        "ADAPTER_TIMEOUT",
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
        raise ObdError(ObdErrorCode.NO_DATA, "empty")
    assert exc_info.value.code is ObdErrorCode.NO_DATA
