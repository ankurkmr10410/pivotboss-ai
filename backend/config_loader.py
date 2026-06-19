"""
Watchlist / symbol configuration.

`config/watchlist.yaml` is the single source of truth for every symbol:
  - internal name
  - segment (index / stock / futures)
  - Yahoo ticker (for EOD OHLC)
  - Kotak token + exchange (for live data & orders)
  - lot size (verify on nseindia.com — NSE revises these periodically)

Loading is fail-safe: a missing/malformed file logs a warning and falls back to
a small built-in default so the bot still runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is in requirements.txt
    yaml = None

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "watchlist.yaml"


@dataclass
class SymbolMeta:
    name: str
    segment: str            # "index" | "stock" | "futures"
    yahoo_ticker: str       # e.g. "^NSEI", "RELIANCE.NS"
    kotak_exchange: str     # e.g. "nse_cm", "nse_fo"
    kotak_token: str        # Kotak instrument token (string)
    lot_size: int = 1


# Built-in fallback so the bot runs even without the yaml file present.
_FALLBACK = [
    SymbolMeta("NIFTY",     "index", "^NSEI",        "nse_fo", "26000", 75),
    SymbolMeta("BANKNIFTY", "index", "^NSEBANK",     "nse_fo", "26009", 35),
    SymbolMeta("RELIANCE",  "stock", "RELIANCE.NS",  "nse_cm", "2885",  1),
    SymbolMeta("HDFCBANK",  "stock", "HDFCBANK.NS",  "nse_cm", "1333",  1),
    SymbolMeta("TCS",       "stock", "TCS.NS",       "nse_cm", "11536", 1),
]


class Watchlist:
    """Loaded view of config/watchlist.yaml."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.symbols: Dict[str, SymbolMeta] = {}
        self._load()

    def _load(self) -> None:
        if yaml is None:
            logger.warning("PyYAML not installed; using built-in fallback watchlist.")
            self._use_fallback()
            return

        if not self.path.exists():
            logger.warning("Watchlist not found at %s; using built-in fallback.", self.path)
            self._use_fallback()
            return

        try:
            with open(self.path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("Failed to parse %s: %s — using fallback.", self.path, e)
            self._use_fallback()
            return

        entries = data.get("symbols", [])
        if not entries:
            logger.warning("No symbols in %s; using built-in fallback.", self.path)
            self._use_fallback()
            return

        for entry in entries:
            try:
                kotak = entry.get("kotak", {}) or {}
                meta = SymbolMeta(
                    name=str(entry["name"]).strip().upper(),
                    segment=str(entry.get("segment", "stock")),
                    yahoo_ticker=str(entry.get("yahoo_ticker", "")).strip(),
                    kotak_exchange=str(kotak.get("exchange", "")).strip(),
                    kotak_token=str(kotak.get("token", "")).strip(),
                    lot_size=int(entry.get("lot_size", 1) or 1),
                )
                self.symbols[meta.name] = meta
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Skipping malformed watchlist entry %r: %s", entry, e)

        if not self.symbols:
            logger.warning("Watchlist parsed but empty; using built-in fallback.")
            self._use_fallback()

    def _use_fallback(self) -> None:
        self.symbols = {m.name: m for m in _FALLBACK}

    # ── ACCESSORS ───────────────────────────────────────────────────────────

    def names(self) -> List[str]:
        return list(self.symbols.keys())

    def get(self, name: str) -> Optional[SymbolMeta]:
        return self.symbols.get(name.upper() if isinstance(name, str) else name)

    def yahoo_map(self) -> Dict[str, str]:
        """internal symbol → Yahoo ticker (only symbols that have one)."""
        return {n: m.yahoo_ticker for n, m in self.symbols.items() if m.yahoo_ticker}

    def kotak_map(self) -> Dict[str, Dict]:
        """internal symbol → {exchange, token, lot_size} for the Kotak connector."""
        return {
            n: {"exchange": m.kotak_exchange, "token": m.kotak_token, "lot_size": m.lot_size}
            for n, m in self.symbols.items()
            if m.kotak_token
        }
