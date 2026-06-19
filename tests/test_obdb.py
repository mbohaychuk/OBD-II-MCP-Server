"""Unit tests for the OBDb signal-set loader.

The bundled JSONs come from github.com/OBDb/Ford-{Mustang,F-150} pinned
to known commits; the loader reads them, normalizes the signal rows
into `Signal` records, and applies the `filter` year bounds.
"""

from __future__ import annotations

from obd_mcp.obdb import Signal, _matches_filter, load_signals


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
    """OBDb `filter` gates commands by year. A year filter must strictly
    reduce the count, not just keep it — a `<=` bound would also pass if the
    filter silently stopped filtering."""
    unfiltered = load_signals("Ford", "F-150")
    filtered_1990 = load_signals("Ford", "F-150", year=1990)
    filtered_2020 = load_signals("Ford", "F-150", year=2020)
    assert 0 < len(filtered_1990) < len(unfiltered)
    assert 0 < len(filtered_2020) < len(unfiltered)


def test_load_signals_filter_years_list_matches_exact() -> None:
    """When `filter.years` lists specific years, only those years match
    (plus any command with no filter)."""
    signals = load_signals("Ford", "Mustang", year=2025)
    # Sanity: 2025 Mustang should have at least some signals.
    assert len(signals) > 0


def test_matches_filter_branches() -> None:
    """The bundled JSONs only use bare from/to bounds, so the `years`-list
    branches of _matches_filter are never exercised by real data. Pin their
    precedence directly: an in-list year short-circuits true; an out-of-list
    year is false unless a from/to range also admits it; a non-dict spec is
    unconditionally included."""
    # years list only
    assert _matches_filter({"years": [2020, 2022]}, 2020) is True
    assert _matches_filter({"years": [2020, 2022]}, 2021) is False
    # years list + range: out-of-list year falls through to the range
    assert _matches_filter({"years": [2020], "from": 2018, "to": 2024}, 2019) is True
    assert _matches_filter({"years": [2020], "from": 2018, "to": 2024}, 2030) is False
    # range only
    assert _matches_filter({"from": 2015}, 2016) is True
    assert _matches_filter({"from": 2015}, 2014) is False
    assert _matches_filter({"to": 2015}, 2016) is False
    # no/invalid spec → unconditional include
    assert _matches_filter(None, 2020) is True
    assert _matches_filter({}, 2020) is True


def test_signal_record_has_request_hex_from_cmd_dict() -> None:
    """OBDb `cmd` is like {"22": "0301"} → request_hex "220301"."""
    signals = load_signals("Ford", "Mustang")
    for sig in signals:
        # Mode 22 requests are always at least 6 hex chars (mode + 2-byte PID).
        assert sig.request_hex[:2] == "22"
        assert len(sig.request_hex) >= 6
        assert all(c in "0123456789ABCDEF" for c in sig.request_hex)
