"""
Run the options backtester on historical index data.

Usage:
    python scripts/run_options_backtest.py --symbol NIFTY --years 2
    python scripts/run_options_backtest.py --all --years 2
"""
import sys, os, argparse, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'config', '.env'))

from options_backtester import OptionsBacktester
from providers.yahoo_provider import YahooProvider
from config_loader import Watchlist
from db import MarketStore
import asyncio
from datetime import date, timedelta

logging.basicConfig(level=logging.WARNING)


def fetch_candles(symbol, years):
    store = MarketStore()
    asyncio.run(store.init())
    end   = date.today()
    start = end - timedelta(days=int(years * 365))
    cached = asyncio.run(store.get_candles(symbol, start=str(start), end=str(end)))
    if cached and len(cached) > 10:
        return [c.to_dict() for c in cached]
    wl = Watchlist()
    provider = YahooProvider.from_watchlist(wl)
    candles = provider.get_history_range(symbol, start, end)
    return [c.to_dict() for c in candles]


def run_one(symbol, years, min_strength=6, strike_type="ATM"):
    print(f"\n{'='*60}")
    print(f"  OPTIONS BACKTEST -- {symbol} ({strike_type})")
    print(f"  {years} years | min strength {min_strength}/10")
    print(f"  Model: delta-approximated ATM premium (delta=0.5)")
    print(f"{'='*60}")

    candles = fetch_candles(symbol, years)
    if len(candles) < 60:
        print(f"  ERROR: Not enough data ({len(candles)} candles)")
        return

    bt = OptionsBacktester(min_strength=min_strength, strike_type=strike_type)
    result = bt.run(symbol, candles)
    m = result.metrics()

    print(f"\n  Total trades:    {m['total_trades']}")
    print(f"  Win rate:        {m['win_rate']}%")
    print(f"  Total P&L:       Rs {m['total_pnl']:,.0f}")
    print(f"  Avg win:         Rs {m['avg_win']:,.0f}")
    print(f"  Avg loss:        Rs {m['avg_loss']:,.0f}")
    print(f"  Profit factor:   {m['profit_factor']}")
    print(f"  Max drawdown:    {m['max_drawdown_pct']}%")
    print(f"  Expectancy:      Rs {m['expectancy']:,.0f} per trade")

    print(f"\n  Breakdown by conviction:")
    for tag, stats in result.breakdown_by_conviction().items():
        print(f"    {tag:10s}: {stats['trades']:3d} trades | win={stats['win_rate']}% | P&L=Rs{stats['total_pnl']:,.0f}")

    exits = {}
    for t in result.trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    print(f"\n  Exit breakdown:")
    for reason, count in sorted(exits.items(), key=lambda x: -x[1]):
        pct = count / len(result.trades) * 100
        print(f"    {reason:10s}: {count:3d} ({pct:.1f}%)")

    print(f"{'='*60}")
    return m


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Options backtest for CPR strategy')
    p.add_argument('--symbol',   default='NIFTY')
    p.add_argument('--years',    type=float, default=2)
    p.add_argument('--strength', type=int,   default=6)
    p.add_argument('--strike',   default='ATM', choices=['ATM', 'OTM1'])
    p.add_argument('--all',      action='store_true', help='Run NIFTY and BANKNIFTY')
    args = p.parse_args()

    if args.all:
        for sym in ['NIFTY', 'BANKNIFTY']:
            run_one(sym, args.years, args.strength, args.strike)
    else:
        run_one(args.symbol, args.years, args.strength, args.strike)
