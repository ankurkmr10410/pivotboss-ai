"""
PivotBoss AI Trading Bot
Ties together: Kotak Neo API → CPR Engine → Signal Generator → Paper Trader
Run this every morning at 9:00 AM IST before market opens.
"""

import os
import json
import time
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from cpr_engine    import calculate_cpr, generate_signal, run_analysis
from paper_trader  import PaperTrader, TradeStatus
from kotak_connector import get_connector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/trading_bot.log"),
    ]
)
logger = logging.getLogger(__name__)

WATCHLIST = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "TCS"]
DATA_FILE  = "data/daily_analysis.json"
TRADE_FILE = "data/paper_trades.json"


class PivotBossBot:

    def __init__(self, mock: bool = True):
        self.mock      = mock
        self.connector = get_connector(mock=mock)
        self.trader    = self._load_trader()
        self.analysis  = {}   # symbol → {cpr, signal}
        logger.info(f"PivotBoss Bot initialized | Mock={mock}")

    def _load_trader(self) -> PaperTrader:
        if os.path.exists(TRADE_FILE):
            try:
                return PaperTrader.load(TRADE_FILE)
            except Exception:
                pass
        return PaperTrader(starting_capital=500000)

    # ── STEP 1: MORNING SETUP (run at 9:00 AM) ─────────────────────────────

    def morning_setup(self):
        """
        Fetch yesterday's OHLC, calculate CPR for all symbols.
        Run this at 9:00 AM before market opens.
        """
        logger.info("=" * 60)
        logger.info("  MORNING SETUP — Calculating CPR for all symbols")
        logger.info("=" * 60)

        today = datetime.now().strftime("%Y-%m-%d")

        for symbol in WATCHLIST:
            try:
                candles = self.connector.get_historical_ohlc(symbol, days=3)
                if len(candles) < 2:
                    logger.warning(f"Not enough data for {symbol}")
                    continue

                # Yesterday = second-last candle, day before = third-last
                prev   = candles[-1]   # yesterday
                prev2  = candles[-2]   # day before yesterday (for virgin check)

                # Build CPR from yesterday's data
                cpr = calculate_cpr(
                    symbol=symbol,
                    date=today,
                    high=prev["high"],
                    low=prev["low"],
                    close=prev["close"],
                    prev_high=prev2["high"] if prev2 else None,
                    prev_low=prev2["low"]   if prev2 else None,
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

                # Auto paper trade if strength ≥ 7
                if signal.strength >= 7 and sig_val != "NEUTRAL":
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
            self.trader.save(TRADE_FILE)

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
                    self.trader.save(TRADE_FILE)

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

    def _save_analysis(self):
        Path("data").mkdir(exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(self.analysis, f, indent=2, default=str)

    def get_status(self) -> dict:
        """Return current bot status as dict (used by dashboard API)"""
        stats = self.trader.get_stats()
        return {
            "timestamp": datetime.now().isoformat(),
            "mode": "mock" if self.mock else "live",
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
    parser.add_argument("--live",    action="store_true",  help="Use real Kotak API (default: mock)")
    parser.add_argument("--setup",   action="store_true",  help="Run morning CPR setup only")
    parser.add_argument("--scan",    action="store_true",  help="Run market open scan only")
    parser.add_argument("--monitor", action="store_true",  help="Start position monitor loop")
    parser.add_argument("--summary", action="store_true",  help="Print portfolio summary")
    parser.add_argument("--all",     action="store_true",  help="Run full cycle (setup + scan + monitor)")
    args = parser.parse_args()

    bot = PivotBossBot(mock=not args.live)

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
