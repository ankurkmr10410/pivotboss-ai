"""
Yahoo Finance EOD provider.

Provides previous-day OHLC for CPR calculation WITHOUT any broker login.
This is the recommended source for `morning_setup()` (see ROADMAP.md).

Ticker conventions:
  - NSE stocks       → "<SYMBOL>.NS"        e.g. "RELIANCE.NS"
  - NIFTY 50 index   → "^NSEI"
  - BANK NIFTY index → "^NSEBANK"

Note: Yahoo does not reliably serve index *futures*. By design we compute CPR on
the spot index for NIFTY/BANKNIFTY (standard practice). Map futures in the
watchlist only if you specifically need future-based levels.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional

from data_provider import Candle, MarketDataProvider

logger = logging.getLogger(__name__)


class YahooProvider(MarketDataProvider):
    """EOD OHLC via yfinance. No login required."""

    def __init__(
        self,
        symbol_map: Optional[Dict[str, str]] = None,
        fetcher: Optional[Callable[[str, int], "object"]] = None,
    ):
        """
        Args:
            symbol_map: internal symbol name → Yahoo ticker (e.g. {"RELIANCE": "RELIANCE.NS"}).
            fetcher:    injectable fetch fn (ticker, days) -> DataFrame; defaults to yfinance.
                        Used for offline testing.
        """
        self.symbol_map = symbol_map or {}
        self._fetcher = fetcher or self._default_fetch

    # ── CONSTRUCTION HELPERS ────────────────────────────────────────────────

    @classmethod
    def from_watchlist(cls, watchlist) -> "YahooProvider":
        """Build a provider from a config_loader.Watchlist instance."""
        return cls(symbol_map=watchlist.yahoo_map())

    # ── INTERFACE ───────────────────────────────────────────────────────────

    def get_eod_ohlc(self, symbol: str, days: int = 5) -> List[Candle]:
        ticker = self.symbol_map.get(symbol)
        if not ticker:
            logger.error("No Yahoo ticker mapped for symbol '%s'. Add it to config/watchlist.yaml.", symbol)
            return []

        try:
            hist = self._fetcher(ticker, days)
        except Exception as e:
            logger.error("Yahoo fetch failed for %s (%s): %s", symbol, ticker, e)
            return []

        return self._parse_history(hist, days)

    # ── INTERNALS ───────────────────────────────────────────────────────────

    @staticmethod
    def _default_fetch(ticker: str, days: int):
        """Default fetcher: live yfinance call. Import kept lazy so the rest of
        the module (and tests) don't require yfinance to import."""
        import yfinance as yf
        # Fetch a buffer beyond `days` to survive weekends/holidays and the
        # in-progress "today" drop in _parse_history.
        return yf.Ticker(ticker).history(period=f"{days + 5}d", auto_adjust=False)

    @staticmethod
    def _parse_history(hist, days: int) -> List[Candle]:
        if hist is None:
            return []
        try:
            if len(hist) == 0:
                return []
        except TypeError:
            return []

        candles: List[Candle] = []
        for idx, row in hist.iterrows():
            try:
                date_str = idx.strftime("%Y-%m-%d")
            except AttributeError:
                date_str = str(idx)[:10]
            try:
                candles.append(Candle(
                    date=date_str,
                    open=round(float(row["Open"]), 2),
                    high=round(float(row["High"]), 2),
                    low=round(float(row["Low"]), 2),
                    close=round(float(row["Close"]), 2),
                    volume=int(row.get("Volume", 0) or 0),
                ))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Skipping malformed Yahoo row %s: %s", date_str, e)
                continue

        # Drop today's in-progress session — CPR must use a *completed* day.
        today = datetime.now().strftime("%Y-%m-%d")
        while candles and candles[-1].date == today:
            candles.pop()

        if not candles:
            return []

        return candles[-days:]
