"""
Options Backtester for PivotBoss AI
=====================================
Validates the CPR -> ATM options strategy using historical index data.

Since historical option premium data isn't readily available, this uses
a delta-approximation model: ATM options have delta ~0.5, so premium
change is approximated as 0.5 x (index point move). This is a standard,
defensible simplification used in retail options backtesting -- it
captures directional P&L correctly even though it doesn't model IV
crush, theta decay precisely, or skew.

This backtester reuses the proven CPR signal logic from backtester.py
and converts the same trades into options P&L using:
  - ATM strike (nearest round number to entry index price)
  - Premium move = 0.5 x index point move (delta approximation)
  - 2x target / 50% SL on premium (matches options_engine.py rules)
  - Conviction sizing: Virgin=2x lots, Narrow=1.5x lots, else 1x

Run via scripts/run_options_backtest.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from cpr_engine import calculate_cpr, generate_signal
from options_engine import STRIKE_STEP, LOT_SIZE, OPTIONS_TARGET_MULT, OPTIONS_SL_MULT

logger = logging.getLogger(__name__)

# Delta approximation for ATM options (industry standard ~0.45-0.55)
ATM_DELTA = 0.5

# Baseline premium as % of spot (used as starting premium estimate)
# NIFTY ATM weekly ~0.8-1.2% of spot, BANKNIFTY ~0.4-0.7%
BASE_PREMIUM_PCT = {
    "NIFTY":     0.009,   # ~0.9% of spot (more realistic than 1%)
    "BANKNIFTY": 0.005,   # ~0.5% of spot
}

# 15-min intraday confirmation: entry only after first 15 min candle confirms direction.
# Simulated by requiring open move of at least 0.1% beyond TC/BC before entry.
CONFIRMATION_PCT = 0.001   # 0.1% beyond CPR level required for entry


@dataclass
class OptionsBacktestTrade:
    entry_date:      str
    symbol:          str
    direction:       str       # LONG (CE) or SHORT (PE)
    option_type:     str       # CE or PE
    strike:          int
    cpr_type:        str
    is_virgin:       bool
    lots:            int
    entry_premium:   float
    target_premium:  float
    sl_premium:      float
    exit_premium:    float
    exit_reason:     str       # TARGET, SL, EOD
    index_move_pts:  float     # how much the index moved that day
    pnl_per_lot:     float
    pnl_total:       float     # pnl_per_lot * lots * lot_size

    def to_dict(self) -> dict:
        return {
            "entry_date": self.entry_date, "symbol": self.symbol,
            "direction": self.direction, "option_type": self.option_type,
            "strike": self.strike, "cpr_type": self.cpr_type,
            "is_virgin": self.is_virgin, "lots": self.lots,
            "entry_premium": round(self.entry_premium, 2),
            "target_premium": round(self.target_premium, 2),
            "sl_premium": round(self.sl_premium, 2),
            "exit_premium": round(self.exit_premium, 2),
            "exit_reason": self.exit_reason,
            "index_move_pts": round(self.index_move_pts, 1),
            "pnl_per_lot": round(self.pnl_per_lot, 2),
            "pnl_total": round(self.pnl_total, 2),
        }


@dataclass
class OptionsBacktestResult:
    symbol: str
    trades: List[OptionsBacktestTrade] = field(default_factory=list)

    def metrics(self) -> dict:
        if not self.trades:
            return {
                "total_trades": 0, "win_rate": 0, "total_pnl": 0,
                "avg_win": 0, "avg_loss": 0, "profit_factor": 0,
                "max_drawdown_pct": 0, "expectancy": 0,
            }

        pnls = [t.pnl_total for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_pnl = sum(pnls)
        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (999 if gross_win > 0 else 0)

        # Max drawdown on cumulative P&L curve
        cum = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = peak - cum
            max_dd = max(max_dd, dd)
        max_dd_pct = round((max_dd / peak * 100), 2) if peak > 0 else 0

        return {
            "total_trades":     len(self.trades),
            "win_rate":         round(len(wins) / len(self.trades) * 100, 2),
            "total_pnl":        round(total_pnl, 2),
            "avg_win":          round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss":         round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor":    profit_factor,
            "max_drawdown_pct": max_dd_pct,
            "expectancy":       round(total_pnl / len(self.trades), 2),
        }

    def breakdown_by_conviction(self) -> Dict[str, dict]:
        groups: Dict[str, List[float]] = {"VIRGIN": [], "NARROW": [], "STANDARD": []}
        for t in self.trades:
            key = "VIRGIN" if t.is_virgin else ("NARROW" if t.cpr_type == "NARROW" else "STANDARD")
            groups[key].append(t.pnl_total)
        out = {}
        for k, pnls in groups.items():
            if not pnls:
                continue
            wins = [p for p in pnls if p > 0]
            out[k] = {
                "trades": len(pnls),
                "win_rate": round(len(wins) / len(pnls) * 100, 2),
                "total_pnl": round(sum(pnls), 2),
            }
        return out


class OptionsBacktester:
    """
    Walks historical daily candles, generates CPR signals, and simulates
    ATM options trades using delta-approximated premium movement.
    """

    def __init__(self, min_strength: int = 6, strike_type: str = "ATM"):
        self.min_strength = min_strength
        self.strike_type = strike_type

    def run(self, symbol: str, candles: List[dict], capital: float = 500000) -> OptionsBacktestResult:
        result = OptionsBacktestResult(symbol=symbol)
        step = STRIKE_STEP.get(symbol, 50)
        lot_size = LOT_SIZE.get(symbol, 75)
        base_pct = BASE_PREMIUM_PCT.get(symbol, 0.01)

        for i in range(2, len(candles)):
            today = candles[i]
            prev = candles[i - 1]
            prev2 = candles[i - 2]

            cpr = calculate_cpr(
                symbol=symbol, date=today["date"],
                high=prev["high"], low=prev["low"], close=prev["close"],
                prev_high=prev2["high"], prev_low=prev2["low"],
            )

            signal = generate_signal(cpr=cpr, current_price=today["open"], opening_price=today["open"])
            sig_val = signal.signal.value if hasattr(signal.signal, "value") else str(signal.signal)

            if sig_val == "NEUTRAL" or signal.strength < self.min_strength:
                continue

            is_buy = "BUY" in sig_val
            option_type = "CE" if is_buy else "PE"
            direction = "LONG" if is_buy else "SHORT"

            # 15-min intraday confirmation: skip trade if opening move doesn't confirm direction.
            # BUY: open must be > TC by at least CONFIRMATION_PCT to confirm bullish breakout.
            # SELL: open must be < BC by at least CONFIRMATION_PCT to confirm bearish breakout.
            if is_buy and today["open"] < cpr.tc * (1 + CONFIRMATION_PCT):
                continue   # no confirmed breakout above TC
            if not is_buy and today["open"] > cpr.bc * (1 - CONFIRMATION_PCT):
                continue   # no confirmed breakout below BC

            entry_index = today["open"]
            strike = int(round(entry_index / step) * step)

            # Entry premium estimate (ATM ~ base_pct of spot)
            entry_premium = max(entry_index * base_pct, 5.0)
            target_premium = round(entry_premium * OPTIONS_TARGET_MULT, 2)
            sl_premium = round(entry_premium * OPTIONS_SL_MULT, 2)

            # How far index needs to move (in points) to hit target/SL,
            # using delta approximation: premium_move = ATM_DELTA * index_move
            target_index_move = (target_premium - entry_premium) / ATM_DELTA
            sl_index_move = (entry_premium - sl_premium) / ATM_DELTA  # positive = adverse move needed

            day_high = today["high"]
            day_low = today["low"]

            if is_buy:
                # CE profits when index rises
                favorable_move = day_high - entry_index
                adverse_move = entry_index - day_low
            else:
                # PE profits when index falls
                favorable_move = entry_index - day_low
                adverse_move = day_high - entry_index

            target_hit = favorable_move >= target_index_move
            sl_hit = adverse_move >= sl_index_move

            # Resolve exit using same proximity rule as equity backtester
            if target_hit and sl_hit:
                # Both touchable same day — pick whichever needed a smaller move (likely hit first)
                if sl_index_move <= target_index_move:
                    exit_premium, exit_reason, index_move = sl_premium, "SL", -sl_index_move
                else:
                    exit_premium, exit_reason, index_move = target_premium, "TARGET", target_index_move
            elif target_hit:
                exit_premium, exit_reason, index_move = target_premium, "TARGET", target_index_move
            elif sl_hit:
                exit_premium, exit_reason, index_move = sl_premium, "SL", -sl_index_move
            else:
                # EOD exit — use close price to estimate premium
                eod_move = (today["close"] - entry_index) if is_buy else (entry_index - today["close"])
                exit_premium = max(entry_premium + ATM_DELTA * eod_move, 0.5)
                exit_reason = "EOD"
                index_move = eod_move

            # Conviction sizing (mirrors options_engine.select_option_contract)
            risk_amount = capital * 0.02
            sl_loss_per_lot = entry_premium * lot_size * (1 - OPTIONS_SL_MULT)
            base_lots = max(1, int(risk_amount / sl_loss_per_lot)) if sl_loss_per_lot > 0 else 1

            is_virgin = bool(cpr.is_virgin)
            if is_virgin:
                lots = max(1, round(base_lots * 2.0))
            elif cpr.cpr_type == "NARROW":
                lots = max(1, round(base_lots * 1.5))
            else:
                lots = base_lots

            pnl_per_lot = (exit_premium - entry_premium) * lot_size
            pnl_total = pnl_per_lot * lots

            result.trades.append(OptionsBacktestTrade(
                entry_date=today["date"], symbol=symbol, direction=direction,
                option_type=option_type, strike=strike, cpr_type=cpr.cpr_type,
                is_virgin=is_virgin, lots=lots, entry_premium=entry_premium,
                target_premium=target_premium, sl_premium=sl_premium,
                exit_premium=exit_premium, exit_reason=exit_reason,
                index_move_pts=index_move, pnl_per_lot=pnl_per_lot, pnl_total=pnl_total,
            ))

        return result
