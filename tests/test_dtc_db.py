"""Unit tests for the vendored Wal33D DTC database wrapper.

These are pure in-process SQLite lookups — no simulator, no async.
"""

from __future__ import annotations

import sqlite3

import pytest

from obd_mcp.dtc_db import DEFAULT_DB_PATH, DtcDatabase, DtcDefinition


@pytest.fixture()
def db() -> DtcDatabase:
    return DtcDatabase(DEFAULT_DB_PATH)


def test_default_db_path_exists() -> None:
    assert DEFAULT_DB_PATH.exists(), (
        f"Vendored DTC DB missing at {DEFAULT_DB_PATH}. Re-run the vendor step in Phase 1."
    )


def test_lookup_generic_code_returns_generic_row(db: DtcDatabase) -> None:
    rows = db.lookup("P0420")
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, DtcDefinition)
    assert row.code == "P0420"
    assert row.manufacturer == "GENERIC"
    assert row.is_generic is True
    assert row.type_ == "P"
    assert "Catalyst" in row.description


def test_lookup_manufacturer_only_code_has_no_generic_row(db: DtcDatabase) -> None:
    # P1690 is in the manufacturer-specific range; no GENERIC row should exist.
    assert db.lookup("P1690") == []


def test_lookup_with_manufacturer_returns_generic_plus_specific(db: DtcDatabase) -> None:
    rows = db.lookup("P0420", manufacturer="AUDI")
    assert [r.manufacturer for r in rows] == ["GENERIC", "AUDI"]
    assert rows[0].is_generic is True
    assert rows[1].is_generic is False


def test_lookup_manufacturer_only_code_with_matching_manufacturer(db: DtcDatabase) -> None:
    rows = db.lookup("P1690", manufacturer="FORD")
    assert len(rows) == 1
    assert rows[0].manufacturer == "FORD"
    assert rows[0].is_generic is False


def test_lookup_manufacturer_is_case_insensitive(db: DtcDatabase) -> None:
    upper = db.lookup("P1690", manufacturer="FORD")
    lower = db.lookup("P1690", manufacturer="ford")
    assert upper == lower


def test_lookup_unknown_code_returns_empty_list(db: DtcDatabase) -> None:
    assert db.lookup("ZZZZZ") == []


def test_lookup_code_is_case_insensitive(db: DtcDatabase) -> None:
    upper = db.lookup("P0420")
    lower = db.lookup("p0420")
    assert upper == lower


def test_context_manager_closes_connection() -> None:
    with DtcDatabase(DEFAULT_DB_PATH) as db:
        assert db.lookup("P0420")
    # After exiting, the underlying sqlite3 connection is closed; using it must error.
    with pytest.raises(sqlite3.ProgrammingError):
        db.lookup("P0420")
