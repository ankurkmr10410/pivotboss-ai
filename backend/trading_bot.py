"""
PivotBoss AI Trading Bot
Ties together: Kotak Neo API → CPR Engine → Signal Generator → Paper Trader
Run this every morning at 9:00 AM IST before market opens.
"""

import os
import json
import time
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from cpr_engine    import calculate_cpr, generate_signal, run_analysis
from paper_trader  import PaperTrader, TradeStatus
from kotak_connector import get_connector
from config_loader import Watchlist
from providers.yahoo_provider import YahooProvider
from db import MarketStore

# Ensure logging directory exists
Path("logs").mkdir(exist_ok=True)

# Try to ensure console uses UTF-8 so emoji/logging don't raise on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import io

# Wrap console stream to ensure UTF-8 output (replace unencodable chars)
try:
    console_stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    console_stream = sys.stdout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(console_stream),
        logging.FileHandler("logs/trading_bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# Watchlist is now the single source of truth (config/watchlist.yaml).
WATCHLIST = Watchlist().names()
DATA_FILE  = "data/daily_analysis.json"
TRADE_FILE = "data/paper_trades.json"


class PivotBossBot:

    def __init__(self, mock: Optional[bool] = None):
        # Default to live mode when Kotak creds are configured; mock otherwise.
        # Override explicitly via the `mock` arg or PIVOTBOSS_MOCK=true env var.
        if mock is None:
            mock = os.getenv("PIVOTBOSS_MOCK", "").strip().lower() in ("1", "true", "yes")
        self.mock = mock
        # Live path auto-logs-in via get_connector(auto_login=True).
        self.connector = get_connector(mock=mock, auto_login=not mock)
        self.connector_type = "MOCK" if mock else ("LIVE" if self.connector.is_logged_in else "LIVE-OFFLINE")
        self.store = MarketStore()
        # Init schema + one-time JSON→SQLite migration, then load trader from DB.
        self._init_store()
        self.trader = self._load_trader()
        self.analysis = {}   # symbol → {cpr, signal}
        # EOD provider (Yahoo) for previous-day OHLC. Broker login is NOT required.
        # See ROADMAP.md — CPR setup must not depend on a live session.
        self.eod_provider = YahooProvider.from_watchlist(Watchlist())
        logger.info(f"PivotBoss Bot initialized | Connector={self.connector_type}")

    def _init_store(self) -> None:
        """Create tables and migrate any legacy JSON portfolio into SQLite."""
        asyncio.run(self.store.init())
        if asyncio.run(self.store.needs_migration(Path(TRADE_FILE))):
            logger.info("Migrating legacy %s into SQLite...", TRADE_FILE)
            asyncio.run(self.store.migrate_from_json(Path(TRADE_FILE)))

    async def _init_store_async(self) -> None:
        """Async version of _init_store -- used when called from an async context (FastAPI)."""
        await self.store.init()
        if await self.store.needs_migration(Path(TRADE_FILE)):
            logger.info("Migrating legacy %s into SQLite...", TRADE_FILE)
            await self.store.migrate_from_json(Path(TRADE_FILE))

    def _load_trader(self) -> PaperTrader:
        try:
            return asyncio.run(PaperTrader.load_from_store(self.store))
        except Exception as e:
            logger.warning(f"Store load failed ({e}); starting fresh trader.")
            return PaperTrader(starting_capital=500000)

    async def _load_trader_async(self) -> PaperTrader:
        """Async version of _load_trader -- used when called from an async context (FastAPI)."""
        try:
            return await PaperTrader.load_from_store(self.store)
        except Exception as e:
            logger.warning(f"Store load failed ({e}); starting fresh trader.")
            return PaperTrader(starting_capital=500000)

    @classmethod
    async def create_async(cls, mock=None) -> "PivotBossBot":
        """Async factory -- use this from FastAPI startup instead of PivotBossBot()."""
        self = cls.__new__(cls)
        if mock is None:
            mock = os.getenv("PIVOTBOSS_MOCK", "").strip().lower() in ("1", "true", "yes")
        self.mock = mock
        self.connector = get_connector(mock=mock, auto_login=not mock)
        self.connector_type = "MOCK" if mock else ("LIVE" if self.connector.is_logged_in else "LIVE-OFFLINE")
        self.store = MarketStore()
        await self._init_store_async()
        self.trader = await self._load_trader_async()
        self.analysis = {}
        self.eod_provider = YahooProvider.from_watchlist(Watchlist())
        logger.info(f"PivotBoss Bot initialized | Connector={self.connector_type}")
        return self

    # ── STEP 1: MORNING SETUP (run at 9:00 AM) ─────────────────────────────

    def _fetch_eod_candles(self, symbol: str, days: int = 3):
        """
        Previous-day OHLC for CPR.

        Source priority:
          1. Yahoo (EOD) — no login, bulletproof. Always try first.
          2. Broker historical — fallback if Yahoo is unavailable.

        Returns a list of candle dicts (most-recent last), or [] on failure.
        """
        # 1) Yahoo EOD
        try:
            candles = self.eod_provider.get_eod_ohlc(symbol, days=days)
            if candles:
                logger.info(f"  {symbol}: EOD via Yahoo ({len(candles)} candles)")
                return [c.to_dict() for c in candles]
        except NotImplementedError:
            pass
        except Exception as e:
            logger.warning(f"  {symbol}: Yahoo EOD unavailable ({e}); falling back to broker.")

        # 2) Broker fallback
        try:
            candles = self.connector.get_historical_ohlc(symbol, days=days)
            if candles:
                logger.info(f"  {symbol}: EOD via broker fallback ({len(candles)} candles)")
            return candles or []
        except Exception as e:
            logger.error(f"  {symbol}: broker EOD fallback also failed: {e}")
            return []

    def morning_setup(self):
        """
        Fetch yesterday's OHLC and calculate CPR for all symbols.
        Run this at 9:00 AM before market opens.

        EOD comes from Yahoo (no broker login needed); broker historical is used
        only as a fallback.
        """
        logger.info("=" * 60)
        logger.info("  MORNING SETUP — Calculating CPR for all symbols")
        logger.info("=" * 60)

        today = datetime.now().strftime("%Y-%m-%d")

        for symbol in WATCHLIST:
            try:
                candles = self._fetch_eod_candles(symbol, days=3)
                if len(candles) < 2:
                    logger.warning(f"Not enough data for {symbol}")
                    continue

                # Yesterday = last candle, day before = second-last (virgin check)
                prev   = candles[-1]   # yesterday
                prev2  = candles[-2]   # day before yesterday

                # Look up yesterday's stored CPR for Virgin CPR detection.
                # Virgin CPR = price never entered yesterday's CPR zone.
                prev_cpr_tc = None
                prev_cpr_bc = None
                try:
                    yesterday = prev["date"]
                    prev_cpr = asyncio.run(self.store.get_cpr(symbol, yesterday))
                    if prev_cpr:
                        prev_cpr_tc = prev_cpr.tc
                        prev_cpr_bc = prev_cpr.bc
                except Exception as e:
                    logger.debug(f"  {symbol}: no stored CPR for virgin check ({e})")

                # Build CPR from yesterday's data
                cpr = calculate_cpr(
                    symbol=symbol,
                    date=today,
                    high=prev["high"],
                    low=prev["low"],
                    close=prev["close"],
                    prev_high=prev2["high"] if prev2 else None,
                    prev_low=prev2["low"]   if prev2 else None,
                    prev_cpr_high=prev_cpr_tc,
                    prev_cpr_low=prev_cpr_bc,
                )

                self.analysis[symbol] = {
                    "cpr": cpr.to_dict(),
                    "prev_candle": prev,
                }

                logger.info(
                    f"  {symbol:12} | Pivot: {cpr.pivot:>10.2f} | "
                    f"TC: {cpr.tc:>10.2f} | BC: {cpr.bc:>10.2f} | "
                    f"Type: {cpr.cpr_type:7} | Virgin: {cpr.is_virgin}"
                )

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")

        self._save_analysis()
        logger.info("\nMorning setup complete. Waiting for market open...")

    # ── STEP 2: MARKET OPEN SCAN (run at 9:16 AM) ──────────────────────────

    def market_open_scan(self):
        """
        At 9:16 AM, fetch opening price and generate signals.
        """
        logger.info("\n" + "=" * 60)
        logger.info("  MARKET OPEN SCAN — Generating signals")
        logger.info("=" * 60)

        signals_generated = []

        for symbol in WATCHLIST:
            if symbol not in self.analysis:
                logger.warning(f"No morning CPR data for {symbol}, skipping")
                continue

            try:
                quote = self.connector.get_quotes(symbol)
                if not quote:
                    continue

                cpr_data = self.analysis[symbol]["cpr"]

                # Rebuild CPR object from saved dict
                from cpr_engine import CPRLevels, generate_signal as gen_sig
                cpr = CPRLevels(**{k: v for k, v in cpr_data.items()})

                signal = gen_sig(
                    cpr=cpr,
                    current_price=quote["ltp"],
                    opening_price=quote["open"],
                )

                self.analysis[symbol]["signal"] = signal.to_dict()
                self.analysis[symbol]["quote"]  = quote

                sig_val = signal.signal.value if hasattr(signal.signal, 'value') else str(signal.signal)
                emoji = "🟢" if "BUY" in sig_val else "🔴" if "SELL" in sig_val else "⚪"
                logger.info(
                    f"  {emoji} {symbol:12} | LTP: ₹{quote['ltp']:>10.2f} | "
                    f"Signal: {sig_val:12} | Strength: {signal.strength}/10"
                )
                for r in signal.reason:
                    logger.info(f"         → {r}")

                signals_generated.append(signal)

                # Auto paper trade if strength >= MIN_SIGNAL_STRENGTH (default 6)
                # Optionally confirm with 15-min breakout before entering
                _min_str = int(os.getenv("MIN_SIGNAL_STRENGTH", "6"))
                if signal.strength >= _min_str and sig_val != "NEUTRAL":
                    direction = "BUY" if "BUY" in sig_val else "SELL"
                    cpr_gate = signal.cpr_levels.get("tc") if direction == "BUY" else signal.cpr_levels.get("bc")
                    use_breakout_confirm = os.getenv("USE_BREAKOUT_CONFIRM", "false").lower() == "true"

                    if use_breakout_confirm and cpr_gate and hasattr(self.connector, "confirm_breakout_5min"):
                        confirmed = self.connector.confirm_breakout_5min(symbol, float(cpr_gate), direction)
                        if not confirmed:
                            logger.info(f"  {symbol}: 15-min breakout not confirmed yet — skipping entry")
                            continue
                        logger.info(f"  {symbol}: 15-min breakout CONFIRMED — entering trade")

                    self._auto_paper_trade(signal, quote)

            except Exception as e:
                logger.error(f"Signal error for {symbol}: {e}")

        self._save_analysis()
        return signals_generated

    # ── STEP 3: AUTO PAPER TRADE ────────────────────────────────────────────

    def _auto_paper_trade(self, signal, quote):
        trade_dict = {
            "symbol":          signal.symbol,
            "date":            signal.date,
            "signal":          signal.signal.value if hasattr(signal.signal, "value") else str(signal.signal),
            "signal_strength": signal.strength,
            "entry_price":     signal.entry_price,
            "stop_loss":       signal.stop_loss,
            "target1":         signal.target1,
            "target2":         signal.target2,
            "target3":         signal.target3,
            "cpr_type":        signal.cpr_levels.get("cpr_type", "NORMAL"),
            "is_virgin_cpr":   signal.cpr_levels.get("is_virgin", False),
        }
        trade = self.trader.open_position(trade_dict)
        if trade:
            logger.info(f"  📋 Paper trade opened: {trade.symbol} {trade.direction} "
                        f"| Entry: ₹{trade.entry_price} | SL: ₹{trade.stop_loss} | Qty: {trade.quantity}")
            self._save_trader()

    # ── STEP 4: INTRADAY MONITOR ────────────────────────────────────────────

    def monitor_positions(self, interval_seconds: int = 60):
        """
        Monitor open paper trades continuously.
        Checks if SL or targets are hit every `interval_seconds`.
        """
        logger.info(f"\n⏱  Monitoring positions every {interval_seconds}s...")

        while True:
            open_trades = [t for t in self.trader.portfolio.trades
                           if t.status == TradeStatus.OPEN.value]

            if not open_trades:
                logger.info("No open positions to monitor.")
                time.sleep(interval_seconds)
                continue

            for trade in open_trades:
                quote = self.connector.get_quotes(trade.symbol)
                if not quote:
                    continue

                ltp = quote["ltp"]
                closed = self.trader.update_position(trade.symbol, ltp)
                if closed:
                    pnl_color = "✅" if closed.pnl > 0 else "❌"
                    logger.info(
                        f"  {pnl_color} {trade.symbol} closed | {closed.status} | "
                        f"Exit: ₹{closed.exit_price} | P&L: ₹{closed.pnl:+.2f}"
                    )
                    self._save_trader()

            stats = self.trader.get_stats()
            logger.info(
                f"  Portfolio → Capital: ₹{self.trader.portfolio.current_capital:,.0f} | "
                f"Open: {stats['open_trades']} | Win Rate: {stats['win_rate']}% | "
                f"Total P&L: ₹{stats['total_pnl']:+,.0f}"
            )

            time.sleep(interval_seconds)

    # ── UTILITIES ────────────────────────────────────────────────────────────

    def print_summary(self):
        stats = self.trader.get_stats()
        print("\n" + "=" * 60)
        print("  PAPER TRADING SUMMARY")
        print("=" * 60)
        print(f"  Starting Capital : ₹{self.trader.portfolio.starting_capital:>12,.0f}")
        print(f"  Current Capital  : ₹{self.trader.portfolio.current_capital:>12,.0f}")
        cap_start = self.trader.portfolio.starting_capital
        cap_now = self.trader.portfolio.current_capital
        cap_pct = (cap_now - cap_start) / cap_start * 100
        print(f"  Total P&L        : ₹{stats['total_pnl']:>+12,.0f}  ({cap_pct:+.2f}%)")
        print(f"  Total Trades     : {stats['total_trades']}")
        print(f"  Win Rate         : {stats['win_rate']}%")
        print(f"  Profit Factor    : {stats['profit_factor']}")
        print(f"  Best Trade       : ₹{stats['best_trade']:>+,.0f}")
        print(f"  Worst Trade      : ₹{stats['worst_trade']:>+,.0f}")
        print(f"  Max Drawdown     : {stats['max_drawdown']}%")

    def _save_trader(self) -> None:
        """Persist the paper trader to SQLite (primary) + JSON (backup)."""
        try:
            asyncio.run(self.trader.save_to_store(self.store))
        except Exception as e:
            logger.warning(f"Store save failed ({e}); falling back to JSON.")
            try:
                self.trader.save(TRADE_FILE)
            except Exception as ee:
                logger.error(f"JSON save also failed: {ee}")

    def _save_analysis(self) -> None:
        """Persist analysis to JSON (dashboard) and CPR/signal rows to SQLite."""
        Path("data").mkdir(exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.analysis, f, indent=2, default=str)

        # Also persist structured rows to the store (for FastAPI / queries).
        from cpr_engine import CPRLevels, TradeSignal
        for symbol, payload in self.analysis.items():
            try:
                cpr = CPRLevels(**{k: v for k, v in payload["cpr"].items()})
                sig_dict = payload.get("signal")
                signal = None
                if sig_dict:
                    # cpr_levels is a required field on TradeSignal; reuse the
                    # dict we already persisted (it's a copy of the CPR dict).
                    sig_copy = dict(sig_dict)
                    sig_copy["cpr_levels"] = dict(payload["cpr"])
                    signal = TradeSignal(**sig_copy)
                asyncio.run(self.store.save_analysis(
                    symbol, cpr,
                    prev_candle=payload.get("prev_candle"),
                    signal=signal,
                ))
            except Exception as e:
                logger.warning(f"Could not persist analysis row for {symbol}: {e}")

    def get_status(self) -> dict:
        """Return current bot status as dict (used by dashboard API)"""
        stats = self.trader.get_stats()
        return {
            "timestamp": datetime.now().isoformat(),
            "mode": "live" if not self.mock else "mock",
            "connector": self.connector_type,
            "logged_in": getattr(self.connector, "is_logged_in", False),
            "paper_trading": True,
            "watchlist": WATCHLIST,
            "analysis": self.analysis,
            "portfolio": {
                "capital": self.trader.portfolio.current_capital,
                "starting": self.trader.portfolio.starting_capital,
                "trades": [t.to_dict() for t in self.trader.portfolio.trades],
            },
            "stats": stats,
        }


# ── ENTRY POINT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PivotBoss AI Trading Bot")
    parser.add_argument("--mock",    action="store_true",  help="Force mock data (overrides PIVOTBOSS_MOCK)")
    parser.add_argument("--live",    action="store_true",  help="Force live Kotak data (auto-login)")
    parser.add_argument("--setup",   action="store_true",  help="Run morning CPR setup only")
    parser.add_argument("--scan",    action="store_true",  help="Run market open scan only")
    parser.add_argument("--monitor", action="store_true",  help="Start position monitor loop")
    parser.add_argument("--summary", action="store_true",  help="Print portfolio summary")
    parser.add_argument("--all",     action="store_true",  help="Run full cycle (setup + scan + monitor)")
    args = parser.parse_args()

    if args.mock and args.live:
        parser.error("--mock and --live are mutually exclusive.")
    mock = True if args.mock else (False if args.live else None)
    bot = PivotBossBot(mock=mock)

    if args.summary:
        bot.print_summary()
    elif args.setup:
        bot.morning_setup()
    elif args.scan:
        bot.market_open_scan()
    elif args.monitor:
        bot.monitor_positions()
    elif args.all:
        bot.morning_setup()
        time.sleep(2)
        bot.market_open_scan()
        bot.print_summary()
    else:
        # Default: demo run
        logger.info("Running full demo cycle with MOCK data...")
        bot.morning_setup()
        time.sleep(1)
        bot.market_open_scan()
        bot.print_summary()
