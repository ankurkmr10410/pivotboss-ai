"""Offline tests for the Yahoo provider using an injected fake fetcher (no network)."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from providers.yahoo_provider import YahooProvider


def _fake_history(rows: int, include_today: bool = True) -> pd.DataFrame:
    """Build a DataFrame shaped like yfinance.history() output."""
    dates = []
    today = datetime.now()
    # business-day-style backfill (weekends skipped for realism)
    d = today
    while len(dates) < rows:
        if d.weekday() < 5:        # Mon-Fri
            dates.append(d)
        d -= timedelta(days=1)
    dates = list(reversed(dates))

    df = pd.DataFrame(
        {
            "Open":   [100 + i for i in range(rows)],
            "High":   [105 + i for i in range(rows)],
            "Low":    [95 + i for i in range(rows)],
            "Close":  [102 + i for i in range(rows)],
            "Volume": [1_000_000 + i * 1000 for i in range(rows)],
        },
        index=pd.DatetimeIndex(dates),
    )
    return df


def test_get_eod_ohlc_parses_and_limits_to_days():
    rows = 8
    df = _fake_history(rows, include_today=True)
    fetcher = lambda ticker, days: df
    provider = YahooProvider(symbol_map={"RELIANCE": "RELIANCE.NS"}, fetcher=fetcher)

    candles = provider.get_eod_ohlc("RELIANCE", days=5)
    # Today's in-progress session must be dropped, then trimmed to `days`.
    assert len(candles) == 5
    assert all(hasattr(c, "open") and hasattr(c, "close") for c in candles)


def test_get_eod_drops_today_in_progress():
    df = _fake_history(6)
    fetcher = lambda ticker, days: df
    provider = YahooProvider(symbol_map={"X": "X.NS"}, fetcher=fetcher)

    candles = provider.get_eod_ohlc("X", days=10)
    today = datetime.now().strftime("%Y-%m-%d")
    assert all(c.date != today for c in candles), "today's session must not be used for CPR"


def test_missing_symbol_map_returns_empty():
    provider = YahooProvider(symbol_map={}, fetcher=lambda *a, **k: _fake_history(5))
    assert provider.get_eod_ohlc("UNKNOWN") == []


def test_unknown_symbol_without_mapping_logs_and_returns_empty():
    provider = YahooProvider(symbol_map={"X": "X.NS"}, fetcher=lambda *a, **k: _fake_history(5))
    assert provider.get_eod_ohlc("NOT_MAPPED") == []


def test_empty_history_returns_empty():
    provider = YahooProvider(
        symbol_map={"X": "X.NS"},
        fetcher=lambda *a, **k: pd.DataFrame(),
    )
    assert provider.get_eod_ohlc("X") == []


def test_from_watchlist_builds_symbol_map():
    from config_loader import Watchlist
    provider = YahooProvider.from_watchlist(Watchlist())
    # NIFTY maps to the spot index
    assert provider.symbol_map.get("NIFTY") == "^NSEI"
    # a stock maps to .NS
    assert provider.symbol_map.get("RELIANCE") == "RELIANCE.NS"


def test_candle_values_parsed_correctly():
    df = _fake_history(3)
    fetcher = lambda ticker, days: df
    provider = YahooProvider(symbol_map={"X": "X.NS"}, fetcher=fetcher)
    candles = provider.get_eod_ohlc("X", days=3)

    # First kept row should match the DataFrame's earliest (sorted) row.
    assert candles[0].open == 100.0
    assert candles[0].high == 105.0
    assert candles[0].low == 95.0
    assert candles[0].close == 102.0
