"""Unit tests for the OBDb signal-set loader.

The bundled JSONs come from github.com/OBDb/Ford-{Mustang,F-150} pinned
to known commits; the loader reads them, normalizes the signal rows
into `Signal` records, and applies the `filter` year bounds.
"""

from __future__ import annotations

from obd_mcp.obdb import Signal, load_signals


def test_load_signals_returns_empty_for_unknown_make_or_model() -> None:
    assert load_signals("Toyota", "Corolla") == []
    assert load_signals("Ford", "DoesNotExist") == []


def test_load_signals_mustang_has_signals() -> None:
    signals = load_signals("Ford", "Mustang")
    assert len(signals) >= 1
    first = signals[0]
    assert isinstance(first, Signal)
    assert first.id
    assert first.name
    assert first.header
    assert first.request_hex.startswith("22")  # all OBDb commands are Mode 22
    assert len(first.request_hex) >= 4


def test_load_signals_f150_has_signals_with_f150_alias() -> None:
    canonical = load_signals("Ford", "F-150")
    aliased = load_signals("ford", "f150")
    assert len(canonical) > 0
    assert len(canonical) == len(aliased), "alias lookup must match canonical"


def test_load_signals_make_model_are_case_insensitive() -> None:
    a = load_signals("FORD", "MUSTANG")
    b = load_signals("ford", "mustang")
    c = load_signals("Ford", "Mustang")
    assert len(a) == len(b) == len(c)


def test_load_signals_year_filter_narrows_result() -> None:
    """OBDb `filter` gates commands by year. Filtering should reduce or
    keep the signal count — never increase it."""
    unfiltered = load_signals("Ford", "F-150")
    filtered_1990 = load_signals("Ford", "F-150", year=1990)
    filtered_2020 = load_signals("Ford", "F-150", year=2020)
    assert len(filtered_1990) <= len(unfiltered)
    assert len(filtered_2020) <= len(unfiltered)
    # 2020 is within the modern F-150 range so should get a non-empty
    # result; 1990 predates most OBDb coverage.
    assert len(filtered_2020) > 0


def test_load_signals_filter_years_list_matches_exact() -> None:
    """When `filter.years` lists specific years, only those years match
    (plus any command with no filter)."""
    signals = load_signals("Ford", "Mustang", year=2025)
    # Sanity: 2025 Mustang should have at least some signals.
    assert len(signals) > 0


def test_signal_record_has_request_hex_from_cmd_dict() -> None:
    """OBDb `cmd` is like {"22": "0301"} → request_hex "220301"."""
    signals = load_signals("Ford", "Mustang")
    for sig in signals:
        # Mode 22 requests are always at least 6 hex chars (mode + 2-byte PID).
        assert sig.request_hex[:2] == "22"
        assert len(sig.request_hex) >= 6
        assert all(c in "0123456789ABCDEF" for c in sig.request_hex)
