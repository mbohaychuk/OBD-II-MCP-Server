"""Tests for `read_dtcs`.

Two paths are exercised:

- Integration against the Ircama simulator ('car' scenario) — validates the
  wire round-trip and empty-result structure.
- Unit tests with a stub client — validates DB joining, scope gating, and
  the manufacturer-only-code fallback to the wire description.
"""

from __future__ import annotations

from typing import Any

import pytest

from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DEFAULT_DB_PATH, DtcDatabase
from obd_mcp.tools import read_dtcs


class _StubResponse:
    def __init__(self, value: Any) -> None:
        self.value = value

    def is_null(self) -> bool:
        return self.value is None


class _StubClient:
    """Minimal ObdClient stand-in for DTC decoder tests.

    Only the `.query()` method is exercised by `read_dtcs`.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.queries: list[str] = []

    async def query(self, command: Any) -> _StubResponse:
        name = command.name
        self.queries.append(name)
        return _StubResponse(self._responses.get(name))


@pytest.mark.asyncio
async def test_read_dtcs_against_clean_simulator(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        result = await read_dtcs(client, scope="all")
        assert result["scope"] == "all"
        assert result["count"] == 0
        assert result["codes"] == []
        assert isinstance(result["timestamp"], float)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_read_dtcs_scope_stored_only_hits_mode_03() -> None:
    stub = _StubClient({"GET_DTC": [("P0420", "Catalyst eff")]})
    result = await read_dtcs(stub, scope="stored")  # type: ignore[arg-type]
    assert stub.queries == ["GET_DTC"]
    assert result["count"] == 1
    assert result["codes"][0]["scope"] == "stored"


@pytest.mark.asyncio
async def test_read_dtcs_scope_pending_only_hits_mode_07() -> None:
    stub = _StubClient({"GET_CURRENT_DTC": [("P0300", "")]})
    result = await read_dtcs(stub, scope="pending")  # type: ignore[arg-type]
    assert stub.queries == ["GET_CURRENT_DTC"]
    assert result["codes"][0]["scope"] == "pending"


@pytest.mark.asyncio
async def test_read_dtcs_scope_all_hits_both() -> None:
    stub = _StubClient(
        {
            "GET_DTC": [("P0420", "")],
            "GET_CURRENT_DTC": [("P0300", "")],
        }
    )
    result = await read_dtcs(stub, scope="all")  # type: ignore[arg-type]
    assert stub.queries == ["GET_DTC", "GET_CURRENT_DTC"]
    assert {c["code"] for c in result["codes"]} == {"P0420", "P0300"}
    assert {c["scope"] for c in result["codes"]} == {"stored", "pending"}


@pytest.mark.asyncio
async def test_read_dtcs_joins_with_database() -> None:
    stub = _StubClient({"GET_DTC": [("P0420", "wire fallback")]})
    with DtcDatabase(DEFAULT_DB_PATH) as db:
        result = await read_dtcs(stub, scope="stored", dtc_db=db)  # type: ignore[arg-type]
    assert result["codes"][0]["description"] == (
        "Catalyst System Efficiency Below Threshold Bank 1"
    )


@pytest.mark.asyncio
async def test_read_dtcs_falls_back_to_wire_description_for_manufacturer_code() -> None:
    # P1690 has no GENERIC row in Wal33D and no manufacturer passed here.
    stub = _StubClient({"GET_DTC": [("P1690", "MIL Electrical Fault")]})
    with DtcDatabase(DEFAULT_DB_PATH) as db:
        result = await read_dtcs(stub, scope="stored", dtc_db=db)  # type: ignore[arg-type]
    assert result["codes"][0]["description"] == "MIL Electrical Fault"


@pytest.mark.asyncio
async def test_read_dtcs_unknown_code_has_null_description() -> None:
    stub = _StubClient({"GET_DTC": [("P9999", "")]})
    with DtcDatabase(DEFAULT_DB_PATH) as db:
        result = await read_dtcs(stub, scope="stored", dtc_db=db)  # type: ignore[arg-type]
    assert result["codes"][0]["description"] is None


@pytest.mark.asyncio
async def test_read_dtcs_rejects_invalid_scope() -> None:
    stub = _StubClient({})
    with pytest.raises(ValueError, match="scope must be"):
        await read_dtcs(stub, scope="permanent")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_read_dtcs_rejects_other_garbage_scope() -> None:
    stub = _StubClient({})
    with pytest.raises(ValueError):
        await read_dtcs(stub, scope="yolo")  # type: ignore[arg-type]
