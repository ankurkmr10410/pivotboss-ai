"""
Strategy Verification Script
=============================
Run this to confirm backtest results are genuine before deploying real money.

Usage:
    python scripts/verify_strategy.py
    python scripts/verify_strategy.py --symbol NIFTY --years 2
"""
import sys, os, argparse, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'config', '.env'))

from backtester import CPRBacktester
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
        print(f"  Using {len(cached)} cached candles from DB")
        return [c.to_dict() for c in cached]
    print(f"  Fetching {years}yr of {symbol} from Yahoo...")
    wl = Watchlist()
    provider = YahooProvider.from_watchlist(wl)
    candles = provider.get_history_range(symbol, start, end)
    print(f"  Got {len(candles)} candles")
    return [c.to_dict() for c in candles]

def run_verification(symbol, years, min_strength=6):
    print(f"\n{'='*60}")
    print(f"  STRATEGY VERIFICATION — {symbol}")
    print(f"  {years} years | min strength {min_strength}/10")
    print(f"{'='*60}")

    candles = fetch_candles(symbol, years)
    if len(candles) < 60:
        print(f"  ERROR: Not enough data ({len(candles)} candles). Need at least 60.")
        return False

    print(f"\n[1/4] FULL PERIOD BACKTEST")
    r = CPRBacktester(min_strength=min_strength, exit_model='pessimistic').run(symbol, candles)
    m = r.metrics()
    print(f"      Trades: {m['total_trades']}  Win rate: {m['win_rate']}%  PF: {m['profit_factor']}  Net: {m['net_points']:+.0f}pts")

    print(f"\n[2/4] WALK-FORWARD VALIDATION (4 quarters)")
    quarter = len(candles) // 4
    quarter_results = []
    all_positive = True
    for q in range(4):
        start_i = max(0, q * quarter - 2)
        end_i   = min(len(candles), (q+1) * quarter + 2)
        chunk   = candles[start_i:end_i]
        rq = CPRBacktester(min_strength=min_strength, exit_model='pessimistic').run(symbol, chunk)
        mq = rq.metrics()
        quarter_results.append(mq)
        status = "✅" if mq['net_points'] >= 0 else "❌"
        if mq['net_points'] < 0: all_positive = False
        q_start = candles[q * quarter]['date']
        q_end   = candles[min((q+1)*quarter-1, len(candles)-1)]['date']
        print(f"      Q{q+1} ({q_start}→{q_end}): {mq['total_trades']} trades  win={mq['win_rate']}%  pf={mq['profit_factor']:.2f}  net={mq['net_points']:+.0f}pts {status}")

    print(f"\n[3/4] OVERFITTING CHECK (75%/25% split)")
    split = int(len(candles) * 0.75)
    r_in  = CPRBacktester(min_strength=min_strength, exit_model='pessimistic').run(symbol, candles[:split+2])
    r_out = CPRBacktester(min_strength=min_strength, exit_model='pessimistic').run(symbol, candles[split:])
    m_in, m_out = r_in.metrics(), r_out.metrics()
    diff = abs(m_in['win_rate'] - m_out['win_rate'])
    print(f"      In-sample  (75%): {m_in['total_trades']} trades  win={m_in['win_rate']}%  pf={m_in['profit_factor']:.2f}")
    print(f"      Out-sample (25%): {m_out['total_trades']} trades  win={m_out['win_rate']}%  pf={m_out['profit_factor']:.2f}")
    stable = diff < 15
    print(f"      Win rate gap: {diff:.1f}%  {'STABLE ✅' if stable else 'UNSTABLE ⚠️ — possible overfitting'}")

    print(f"\n[4/4] EXIT BREAKDOWN")
    exits = {}
    for t in r.trades: exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    for reason, count in sorted(exits.items(), key=lambda x: -x[1]):
        pct = count / len(r.trades) * 100 if r.trades else 0
        print(f"      {reason:10s}: {count:3d} trades ({pct:.1f}%)")

    print(f"\n{'='*60}")
    print(f"  VERDICT")
    go_criteria = {
        'Win rate > 55%':    m['win_rate'] >= 55,
        'Profit factor > 1.5': m['profit_factor'] >= 1.5,
        'Max drawdown < 15%':  m['max_drawdown_pct'] < 15,
        'All quarters positive': all_positive,
        'Out-of-sample stable':  stable,
    }
    all_pass = all(go_criteria.values())
    for criterion, passed in go_criteria.items():
        print(f"  {'✅' if passed else '❌'} {criterion}")
    print(f"\n  {'🟢 GO — ready for paper trading' if all_pass else '🔴 NO GO — review failed criteria'}")
    print(f"{'='*60}")
    return all_pass

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Verify CPR strategy before deployment')
    p.add_argument('--symbol',   default='NIFTY')
    p.add_argument('--years',    type=float, default=2)
    p.add_argument('--strength', type=int,   default=6)
    p.add_argument('--all',      action='store_true', help='Run for all watchlist symbols')
    args = p.parse_args()

    if args.all:
        wl = Watchlist()
        for sym in wl.names():
            run_verification(sym, args.years, args.strength)
    else:
        run_verification(args.symbol, args.years, args.strength)
