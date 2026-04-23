"""Tool-layer test for `list_manufacturer_signals`."""

from __future__ import annotations

import pytest

from obd_mcp.tools import list_manufacturer_signals


@pytest.mark.asyncio
async def test_list_manufacturer_signals_ford_mustang_returns_signals() -> None:
    result = await list_manufacturer_signals(year=2025, make="Ford", model="Mustang")
    assert result["available"] is True
    assert result["make"] == "Ford"
    assert result["model"] == "Mustang"
    assert result["year"] == 2025
    assert len(result["signals"]) > 0
    sig = result["signals"][0]
    assert set(sig.keys()) >= {"id", "name", "header", "request_hex"}


@pytest.mark.asyncio
async def test_list_manufacturer_signals_unsupported_make_is_in_band_error() -> None:
    result = await list_manufacturer_signals(year=2010, make="Toyota", model="Corolla")
    assert result["available"] is False
    assert result["reason"] == "NO_SIGNAL_SET"
    assert result["signals"] == []


@pytest.mark.asyncio
async def test_list_manufacturer_signals_no_year_returns_all() -> None:
    with_year = await list_manufacturer_signals(year=2015, make="Ford", model="F-150")
    without_year = await list_manufacturer_signals(year=None, make="Ford", model="F-150")
    # No year = everything; year=2015 = subset (or equal if no filter trims).
    assert len(without_year["signals"]) >= len(with_year["signals"])
