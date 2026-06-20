"""
CPR Backtester.

Walks historical daily candles and evaluates the CPR strategy:
  - For each trading day D, build CPR from D-1's OHLC.
  - At D's open, evaluate the CPR signal (entry = open, SL at CPR boundary,
    3 targets at the CPR R/S levels).
  - Simulate the trade against D's intraday range (high/low) to decide exit.

Exit model (configurable; default: conservative-pessimistic):
  - A trade is checked against the day's full range in a fixed order:
    STOP-LOSS first, then targets T1..T3.
  - Reasoning: a pessimistic (stop-first) model is the safer assumption when you
    only have OHLC, not intraday ticks. An optimistic (target-first) model is
    also provided for bracketing the result. The truth lies between — report both.

This produces metrics: net P&L (points and %), win rate, expectancy, profit
factor, max drawdown, total trades, and per-CPR-type / per-signal breakdowns.

Run via scripts/run_backtest.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import List, Optional, Dict

from cpr_engine import calculate_cpr, generate_signal

logger = logging.getLogger(__name__)


# ── DATA ──────────────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    entry_date: str
    signal: str
    direction: str          # "LONG" / "SHORT"
    cpr_type: str
    is_virgin: bool
    entry: float
    stop_loss: float
    target1: float
    target2: float
    target3: float
    exit_date: str
    exit_price: float
    exit_reason: str        # "SL" | "T1" | "T2" | "T3" | "EOD"
    pnl_points: float
    pnl_pct: float
    strength: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestResult:
    symbol: str
    start_date: str
    end_date: str
    trades: List[BacktestTrade] = field(default_factory=list)
    exit_model: str = "pessimistic"

    # ── METRICS ──
    def metrics(self) -> dict:
        if not self.trades:
            return {
                "symbol": self.symbol, "total_trades": 0,
                "net_points": 0.0, "net_pct": 0.0, "win_rate": 0.0,
                "expectancy_points": 0.0, "profit_factor": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "max_drawdown_pct": 0.0,
            }

        pnls = [t.pnl_points for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        net = sum(pnls)

        # Max drawdown measured in POINTS of the equity curve, then expressed as
        # a % of a notional capital base = sum of each trade's notional entry
        # value. This stays meaningful even when the curve is net-negative
        # (dividing by a near-zero peak gives absurd numbers).
        equity, peak, max_dd_pts = 0.0, 0.0, 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            max_dd_pts = max(max_dd_pts, peak - equity)
        notional = sum(t.entry for t in self.trades) or 1.0
        max_dd = (max_dd_pts / notional) * 100

        return {
            "symbol": self.symbol,
            "total_trades": len(self.trades),
            "net_points": round(net, 2),
            "net_pct": round(sum(t.pnl_pct for t in self.trades) / len(self.trades), 3),
            "win_rate": round(len(wins) / len(self.trades) * 100, 2),
            "expectancy_points": round(net / len(self.trades), 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "max_drawdown_pct": round(max_dd, 2),
        }

    def breakdown_by_cpr_type(self) -> Dict[str, dict]:
        return self._breakdown(key=lambda t: t.cpr_type)

    def breakdown_by_signal(self) -> Dict[str, dict]:
        return self._breakdown(key=lambda t: t.signal)

    def _breakdown(self, key) -> Dict[str, dict]:
        groups: Dict[str, list] = {}
        for t in self.trades:
            groups.setdefault(key(t), []).append(t)
        out = {}
        for g, trades in groups.items():
            pnls = [t.pnl_points for t in trades]
            wins = [p for p in pnls if p > 0]
            out[g] = {
                "trades": len(trades),
                "net_points": round(sum(pnls), 2),
                "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            }
        return out


# ── ENGINE ────────────────────────────────────────────────────────────────────

class CPRBacktester:
    """Runs the CPR strategy over a historical candle series (most-recent LAST)."""

    def __init__(
        self,
        min_strength: int = 6,
        exit_model: str = "pessimistic",
        include_neutral: bool = False,
    ):
        """
        Args:
            min_strength:     minimum signal strength (1-10) to take a trade.
            exit_model:       "pessimistic" (SL first) or "optimistic" (targets first).
            include_neutral:  if False, NEUTRAL signals never open a position.
        """
        if exit_model not in ("pessimistic", "optimistic"):
            raise ValueError("exit_model must be 'pessimistic' or 'optimistic'")
        self.min_strength = min_strength
        self.exit_model = exit_model
        self.include_neutral = include_neutral

    def run(
        self,
        symbol: str,
        candles: List[dict],
    ) -> BacktestResult:
        """Backtest over a list of candle dicts {date, open, high, low, close}.

        candles must be ordered oldest-first. Trading starts at index 2
        (needs the prior 2 days: yesterday for CPR, day-before for virgin check).
        """
        if len(candles) < 3:
            logger.warning("Not enough candles to backtest %s (%d).", symbol, len(candles))
            return BacktestResult(
                symbol=symbol,
                start_date=candles[0]["date"] if candles else "",
                end_date=candles[-1]["date"] if candles else "",
                exit_model=self.exit_model,
            )

        result = BacktestResult(
            symbol=symbol,
            start_date=candles[0]["date"],
            end_date=candles[-1]["date"],
            exit_model=self.exit_model,
        )

        for i in range(2, len(candles)):
            today = candles[i]
            prev = candles[i - 1]
            prev2 = candles[i - 2]

            cpr = calculate_cpr(
                symbol=symbol,
                date=today["date"],
                high=prev["high"],
                low=prev["low"],
                close=prev["close"],
                prev_high=prev2["high"],
                prev_low=prev2["low"],
            )

            signal = generate_signal(
                cpr=cpr,
                current_price=today["open"],
                opening_price=today["open"],
            )

            sig_val = signal.signal.value if hasattr(signal.signal, "value") else str(signal.signal)
            if sig_val == "NEUTRAL" and not self.include_neutral:
                continue
            if signal.strength < self.min_strength:
                continue

            trade = self._simulate_trade(today, signal, sig_val, cpr)
            if trade:
                result.trades.append(trade)

        return result

    # ── TRADE SIMULATION ─────────────────────────────────────────────────────

    def _simulate_trade(self, today: dict, signal, sig_val: str, cpr) -> Optional[BacktestTrade]:
        entry = today["open"]
        sl = signal.stop_loss
        t1, t2, t3 = signal.target1, signal.target2, signal.target3
        day_high = today["high"]
        day_low = today["low"]
        direction = "LONG" if "BUY" in sig_val else "SHORT"

        def hit(level: float) -> bool:
            if direction == "LONG":
                return day_high >= level
            return day_low <= level

        exit_price, exit_reason = self._resolve_exit(
            direction=direction,
            sl_hit=hit(sl),
            t1_hit=hit(t1),
            t2_hit=hit(t2),
            t3_hit=hit(t3),
            sl=sl, t1=t1, t2=t2, t3=t3,
        )

        # If nothing was touched intraday → exit at close (EOD).
        if exit_reason is None:
            exit_price = today["close"]
            exit_reason = "EOD"

        pnl_points = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
        pnl_pct = (pnl_points / entry) * 100 if entry else 0.0

        return BacktestTrade(
            entry_date=today["date"],
            signal=sig_val,
            direction=direction,
            cpr_type=cpr.cpr_type,
            is_virgin=cpr.is_virgin,
            entry=round(entry, 2),
            stop_loss=round(sl, 2),
            target1=round(t1, 2),
            target2=round(t2, 2),
            target3=round(t3, 2),
            exit_date=today["date"],
            exit_price=round(exit_price, 2),
            exit_reason=exit_reason,
            pnl_points=round(pnl_points, 2),
            pnl_pct=round(pnl_pct, 3),
            strength=signal.strength,
        )

    def _resolve_exit(
        self, direction: str, sl_hit: bool, t1_hit: bool, t2_hit: bool, t3_hit: bool,
        sl: float, t1: float, t2: float, t3: float,
    ) -> (float, Optional[str]):
        """
        Decide which level to credit the exit to, given the day's range.

        Pessimistic (default): if both SL and any target were touchable today,
        assume the worse outcome for the trader (SL first). This is the safer
        overestimate of risk when only OHLC is available.

        Optimistic: assume targets are reached before SL (upper-bound of results).
        The true expectancy lies between the two.
        """
        # Direction-agnostic: for LONG, targets are above entry and SL below.
        # We pick the first reachable level in priority order.
        if self.exit_model == "pessimistic":
            order = [("SL", sl_hit, sl), ("T3", t3_hit, t3), ("T2", t2_hit, t2), ("T1", t1_hit, t1)]
        else:  # optimistic
            order = [("T3", t3_hit, t3), ("T2", t2_hit, t2), ("T1", t1_hit, t1), ("SL", sl_hit, sl)]

        for name, touched, level in order:
            if touched:
                return level, name
        return 0.0, None
