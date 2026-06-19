"""Tests for config_loader.Watchlist (uses the real config/watchlist.yaml)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from config_loader import Watchlist


def test_watchlist_loads_symbols():
    wl = Watchlist()
    names = wl.names()
    assert "NIFTY" in names
    assert "BANKNIFTY" in names
    assert "RELIANCE" in names


def test_yahoo_map_has_indices_and_stocks():
    wl = Watchlist()
    ymap = wl.yahoo_map()
    assert ymap["NIFTY"] == "^NSEI"
    assert ymap["BANKNIFTY"] == "^NSEBANK"
    assert ymap["RELIANCE"] == "RELIANCE.NS"


def test_kotak_map_structure():
    wl = Watchlist()
    kmap = wl.kotak_map()
    nifty = kmap["NIFTY"]
    assert set(nifty.keys()) == {"exchange", "token", "lot_size"}
    assert nifty["exchange"] == "nse_fo"


def test_get_case_insensitive():
    wl = Watchlist()
    assert wl.get("nifty") is not None
    assert wl.get("nifty").name == "NIFTY"


def test_unknown_symbol_returns_none():
    wl = Watchlist()
    assert wl.get("DOES_NOT_EXIST") is None


def test_lot_size_present_and_positive():
    wl = Watchlist()
    for name in wl.names():
        assert wl.get(name).lot_size >= 1
