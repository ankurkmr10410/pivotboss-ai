"""
Market Data Provider abstraction.

Strategy and orchestration code talks to `MarketDataProvider`, never to a
specific broker or data source. This keeps CPR setup independent of the broker
session (see ROADMAP.md, Phase 1).

Two responsibilities, intentionally split:
  - EOD / previous-day OHLC  → YahooProvider (no login, bulletproof)
  - Live LTP / intraday       → broker provider (Kotak)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    """A single OHLCV daily candle."""
    date: str          # ISO YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class MarketDataProvider(ABC):
    """
    Common interface for all market-data sources.

    Subclasses MUST implement `get_eod_ohlc`. Live-LTP support is optional
    (broker providers implement it; EOD-only sources raise NotImplementedError).
    """

    @abstractmethod
    def get_eod_ohlc(self, symbol: str, days: int = 5) -> List[Candle]:
        """Return the last `days` complete daily candles, most-recent last.

        The final candle must be the most recent *completed* trading day
        (today's in-progress session is excluded).
        """
        ...

    def get_history_range(
        self, symbol: str, start: date, end: date
    ) -> List[Candle]:
        """Return complete daily candles between [start, end] inclusive, oldest first.

        Default implementation slices from `get_eod_ohlc`. Providers with bulk
        fetch (e.g. Yahoo) override this for efficient multi-year backtests.
        """
        from datetime import timedelta
        days = max(1, (end - start).days + 1)
        candles = self.get_eod_ohlc(symbol, days=days + 60)  # buffer for weekends/hols
        start_s, end_s = start.isoformat(), end.isoformat()
        return [c for c in candles if start_s <= c.date <= end_s]

    def get_ltp(self, symbol: str) -> Optional[float]:
        """Last traded price. EOD-only providers do not implement this."""
        raise NotImplementedError(
            f"{type(self).__name__} does not provide live LTP. "
            "Use a broker-backed provider for live prices."
        )

    def get_historical(self, symbol: str, days: int = 5) -> List[Candle]:
        """Alias for EOD OHLC. Override for finer timeframes if needed."""
        return self.get_eod_ohlc(symbol, days)
