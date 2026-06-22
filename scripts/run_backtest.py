"""
Run a CPR strategy backtest over historical Yahoo data.

Fetches multi-year daily candles, runs the CPR backtester, prints a report,
and optionally notifies via WhatsApp/console.

Examples:
  # 2-year NIFTY backtest (console report)
  python scripts/run_backtest.py --symbol NIFTY --years 2

  # All watchlist symbols, 1 year, also send a WhatsApp summary
  python scripts/run_backtest.py --all --years 1 --notify whatsapp

  # Custom window + strength gate + optimistic exit model
  python scripts/run_backtest.py --symbol RELIANCE --years 3 \\
      --min-strength 7 --model optimistic
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure stdout handles Unicode on Windows (cp1252 lacks → etc.)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make backend/ importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from config_loader import Watchlist
from providers.yahoo_provider import YahooProvider
from backtester import CPRBacktester
from alerts import get_alert_provider
from data_provider import Candle
from db import MarketStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _fmt_block(label: str, d: dict) -> list[str]:
    lines = [f"  {label}:"]
    for k, v in d.items():
        lines.append(f"    {k:24}: {v}")
    return lines


def build_report(symbol: str, result, pess_m, opt_m) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append(f"  CPR BACKTEST — {symbol}")
    lines.append(f"  {result.start_date}  →  {result.end_date}  ({len(result.trades)} trades)")
    lines.append("=" * 60)

    for label, m in (("Pessimistic (SL-first)", pess_m), ("Optimistic (target-first)", opt_m)):
        lines.append(f"\n  {label}:")
        for k, v in m.items():
            lines.append(f"    {k:24}: {v}")

    lines.append("\n  Breakdown by CPR type (pessimistic):")
    for k, v in result.breakdown_by_cpr_type().items():
        lines.append(f"    {k:24}: {v}")

    lines.append("\n  Breakdown by signal (pessimistic):")
    for k, v in result.breakdown_by_signal().items():
        lines.append(f"    {k:24}: {v}")

    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="CPR strategy backtester over Yahoo history")
    p.add_argument("--symbol", help="Single watchlist symbol (e.g. NIFTY)")
    p.add_argument("--all", action="store_true", help="Backtest every symbol in the watchlist")
    p.add_argument("--years", type=float, default=2.0, help="Years of history (default 2)")
    p.add_argument("--min-strength", type=int, default=6, help="Min signal strength to take a trade (1-10)")
    p.add_argument("--model", choices=["pessimistic", "optimistic"], default="pessimistic",
                   help="Exit model. Backtester reports BOTH; this picks the summary's headline.")
    p.add_argument("--notify", choices=["console", "whatsapp"], default="console",
                   help="Alert channel for the summary")
    p.add_argument("--cache", help="Path to a JSON cache of candles {symbol:[{date,open,high,low,close},...]}. "
                                   "Bypasses Yahoo (useful when Yahoo rate-limits). Keys are symbol names.")
    p.add_argument("--db", nargs="?", const="data/pivotboss.db", default=None,
                   help="Cache candles in SQLite (default DB: data/pivotboss.db). "
                        "Reuses cached candles on repeat runs — avoids Yahoo rate-limiting. "
                        "Pass a path to use a custom DB location.")
    p.add_argument("--refresh", action="store_true",
                   help="With --db: ignore cached candles and re-fetch from Yahoo, "
                        "then overwrite the DB cache.")
    args = p.parse_args()

    if not args.symbol and not args.all:
        p.error("Provide --symbol <NAME> or --all")

    wl = Watchlist()
    symbols = wl.names() if args.all else [args.symbol.upper()]
    unknown = [s for s in symbols if s not in wl.names()]
    if unknown:
        p.error(f"Unknown symbol(s): {unknown}. Available: {wl.names()}")

    end = date.today()
    start = end - timedelta(days=int(args.years * 365))
    alert = get_alert_provider(args.notify)

    # Load a local cache if provided (Yahoo often rate-limits bursts).
    cache = {}
    if args.cache:
        try:
            with open(args.cache, encoding="utf-8") as f:
                cache = json.load(f)
            logger.info("Loaded cache from %s (%d symbols).", args.cache, len(cache))
        except Exception as e:
            logger.warning("Could not load cache %s: %s — will fetch live.", args.cache, e)

    provider = YahooProvider.from_watchlist(wl)
    store = None
    if args.db:
        store = MarketStore(db_path=Path(args.db))
        asyncio.run(store.init())
        logger.info("SQLite candle cache: %s", store.db_path)

    all_lines = []
    for sym in symbols:
        candle_dicts = []
        if sym in cache:
            candle_dicts = cache[sym]
            logger.info("Using JSON-cached candles for %s (%d).", sym, len(candle_dicts))
        elif store is not None and not args.refresh:
            cached = asyncio.run(store.get_candles(sym, start=str(start), end=str(end)))
            if cached:
                candle_dicts = [c.to_dict() for c in cached]
                logger.info("Using DB-cached candles for %s (%d).", sym, len(candle_dicts))
        if not candle_dicts:
            logger.info("Fetching %s history (%s → %s)...", sym, start, end)
            candles = provider.get_history_range(sym, start, end)
            candle_dicts = [c.to_dict() for c in candles]
            logger.info("  got %d candles.", len(candle_dicts))
            # Persist fetched candles to the DB for next time.
            if store is not None and candles:
                asyncio.run(store.upsert_symbol_candles(
                    sym, [Candle(**c) for c in candle_dicts], source="yahoo"
                ))
                logger.info("  cached %d candles to DB.", len(candles))

        if len(candle_dicts) < 3:
            logger.warning("Not enough data for %s — skipping.", sym)
            continue

        # Run BOTH exit models so we bracket the true expectancy.
        pess = CPRBacktester(min_strength=args.min_strength, exit_model="pessimistic").run(sym, candle_dicts)
        opt = CPRBacktester(min_strength=args.min_strength, exit_model="optimistic").run(sym, candle_dicts)

        # `result` for breakdowns follows the chosen headline model.
        result = pess if args.model == "pessimistic" else opt
        report = build_report(sym, result, pess.metrics(), opt.metrics())
        print("\n" + report)
        all_lines.append(report)

    # Notify a combined summary.
    if all_lines:
        summary = "\n\n".join(all_lines)
        alert.send(summary)


if __name__ == "__main__":
    main()
