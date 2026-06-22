"""
Paper Trading Simulator
Simulates trades based on CPR signals without real money.
Tracks P&L, win rate, and trade journal.
"""

import json
import uuid
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Optional
from enum import Enum


class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED_TARGET1 = "CLOSED_TARGET1"
    CLOSED_TARGET2 = "CLOSED_TARGET2"
    CLOSED_TARGET3 = "CLOSED_TARGET3"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"


class TradeDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Trade:
    id: str
    symbol: str
    date: str
    time: str
    direction: str
    entry_price: float
    stop_loss: float
    target1: float
    target2: float
    target3: float
    quantity: int
    signal_type: str
    signal_strength: int
    cpr_type: str
    is_virgin_cpr: bool
    status: str = TradeStatus.OPEN.value
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    notes: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class PaperPortfolio:
    starting_capital: float = 500000.0   # ₹5 lakh starting capital
    current_capital: float = 500000.0
    trades: List[Trade] = field(default_factory=list)
    max_risk_per_trade_pct: float = 1.0   # Max 1% risk per trade
    max_open_positions: int = 3           # Max 3 positions at a time

    def get_stats(self) -> dict:
        """Compute aggregate stats from the closed-trade list.

        Lives on the portfolio (not on PaperTrader) so to_dict() / persistence
        can call it without a trader reference.
        """
        closed = [t for t in self.trades if t.status != TradeStatus.OPEN.value]
        if not closed:
            return {
                "total_trades": 0, "open_trades": len(self.trades),
                "win_rate": 0, "total_pnl": 0, "avg_pnl": 0,
                "best_trade": 0, "worst_trade": 0,
                "profit_factor": 0, "max_drawdown": 0,
                "capital_change_pct": 0.0,
            }

        winners = [t for t in closed if t.pnl > 0]
        losers = [t for t in closed if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in closed)
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))

        # Track running capital for drawdown
        running = self.starting_capital
        peak = running
        max_dd = 0
        for t in closed:
            running += t.pnl
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100
            max_dd = max(max_dd, dd)

        return {
            "total_trades": len(closed),
            "open_trades": len([t for t in self.trades if t.status == TradeStatus.OPEN.value]),
            "win_rate": round(len(winners) / len(closed) * 100, 1) if closed else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / len(closed), 2) if closed else 0,
            "best_trade": round(max((t.pnl for t in closed), default=0), 2),
            "worst_trade": round(min((t.pnl for t in closed), default=0), 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
            "max_drawdown": round(max_dd, 2),
            "capital_change_pct": round(
                (self.current_capital - self.starting_capital) /
                self.starting_capital * 100, 2
            ),
        }

    def to_dict(self):
        return {
            "starting_capital": self.starting_capital,
            "current_capital": round(self.current_capital, 2),
            "trades": [t.to_dict() for t in self.trades],
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "max_open_positions": self.max_open_positions,
            "stats": self.get_stats(),
        }


class PaperTrader:
    def __init__(self, starting_capital: float = 500000.0):
        self.portfolio = PaperPortfolio(
            starting_capital=starting_capital,
            current_capital=starting_capital
        )

    def calculate_quantity(self, entry: float, stop_loss: float) -> int:
        """Position sizing based on risk per trade"""
        risk_per_trade = self.portfolio.current_capital * (self.portfolio.max_risk_per_trade_pct / 100)
        risk_per_unit = abs(entry - stop_loss)
        if risk_per_unit == 0:
            return 1
        qty = int(risk_per_trade / risk_per_unit)
        return max(1, qty)

    def open_position(self, signal: dict) -> Optional[Trade]:
        """Open a new paper trade based on CPR signal"""
        open_trades = [t for t in self.portfolio.trades if t.status == TradeStatus.OPEN.value]

        if len(open_trades) >= self.portfolio.max_open_positions:
            return None  # Max positions reached

        # Only trade STRONG_BUY / STRONG_SELL / BUY / SELL, not NEUTRAL
        if signal["signal"] == "NEUTRAL":
            return None

        if signal["signal_strength"] < 5:
            return None  # Minimum strength threshold

        # Don't open duplicate position on same symbol
        if any(t.symbol == signal["symbol"] for t in open_trades):
            return None

        direction = TradeDirection.LONG if "BUY" in signal["signal"] else TradeDirection.SHORT
        entry = signal["entry_price"]
        stop_loss = signal["stop_loss"]
        qty = self.calculate_quantity(entry, stop_loss)

        trade = Trade(
            id=str(uuid.uuid4())[:8],
            symbol=signal["symbol"],
            date=signal["date"],
            time=datetime.now().strftime("%H:%M:%S"),
            direction=direction.value,
            entry_price=entry,
            stop_loss=stop_loss,
            target1=signal["target1"],
            target2=signal["target2"],
            target3=signal["target3"],
            quantity=qty,
            signal_type=signal["signal"],
            signal_strength=signal["signal_strength"],
            cpr_type=signal.get("cpr_type", "NORMAL"),
            is_virgin_cpr=signal.get("is_virgin_cpr", False),
        )

        self.portfolio.trades.append(trade)
        return trade

    def update_position(self, symbol: str, current_price: float) -> Optional[Trade]:
        """Check if any open position hit SL or target"""
        open_trades = [t for t in self.portfolio.trades
                       if t.status == TradeStatus.OPEN.value and t.symbol == symbol]

        for trade in open_trades:
            status = None

            if trade.direction == TradeDirection.LONG.value:
                if current_price <= trade.stop_loss:
                    status = TradeStatus.CLOSED_SL
                elif current_price >= trade.target3:
                    status = TradeStatus.CLOSED_TARGET3
                elif current_price >= trade.target2:
                    status = TradeStatus.CLOSED_TARGET2
                elif current_price >= trade.target1:
                    status = TradeStatus.CLOSED_TARGET1

            else:  # SHORT
                if current_price >= trade.stop_loss:
                    status = TradeStatus.CLOSED_SL
                elif current_price <= trade.target3:
                    status = TradeStatus.CLOSED_TARGET3
                elif current_price <= trade.target2:
                    status = TradeStatus.CLOSED_TARGET2
                elif current_price <= trade.target1:
                    status = TradeStatus.CLOSED_TARGET1

            if status:
                self._close_trade(trade, current_price, status)
                return trade

        return None

    def _close_trade(self, trade: Trade, exit_price: float, status: TradeStatus):
        """Close a trade and calculate P&L"""
        trade.status = status.value
        trade.exit_price = exit_price
        trade.exit_time = datetime.now().strftime("%H:%M:%S")

        if trade.direction == TradeDirection.LONG.value:
            trade.pnl = round((exit_price - trade.entry_price) * trade.quantity, 2)
        else:
            trade.pnl = round((trade.entry_price - exit_price) * trade.quantity, 2)

        trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2)
        self.portfolio.current_capital += trade.pnl

        if status == TradeStatus.CLOSED_SL:
            trade.notes = "Stop loss hit"
        elif "TARGET" in status.value:
            target_num = status.value.split("TARGET")[1]
            trade.notes = f"Target {target_num} achieved"

    def get_stats(self) -> dict:
        """Delegate to the portfolio (stats now live there)."""
        return self.portfolio.get_stats()

    # ── JSON persistence (legacy, kept as backup) ────────────────────────

    def save(self, path: str = "paper_trades.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.portfolio.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str = "paper_trades.json") -> "PaperTrader":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        trader = cls(starting_capital=data["starting_capital"])
        trader.portfolio.current_capital = data["current_capital"]
        trader.portfolio.trades = [Trade(**t) for t in data["trades"]]
        # Restore config the old loader dropped (best-effort).
        trader.portfolio.max_risk_per_trade_pct = data.get("max_risk_per_trade_pct", 1.0)
        trader.portfolio.max_open_positions = data.get("max_open_positions", 3)
        return trader

    # ── SQLite persistence (async, primary store) ───────────────────────

    async def save_to_store(self, store: "MarketStore") -> None:
        """Persist all trades + portfolio config to the async SQLite store."""
        await store.upsert_trades(self.portfolio.trades)
        await store.save_portfolio_config(self.portfolio)
        # Snapshot equity for the curve.
        open_n = len([t for t in self.portfolio.trades
                      if t.status == TradeStatus.OPEN.value])
        await store.append_equity(self.portfolio.current_capital, open_n)

    @classmethod
    async def load_from_store(cls, store: "MarketStore") -> "PaperTrader":
        """Reconstruct a trader from the store. Restores all config fields."""
        cfg = await store.load_portfolio_config()
        starting = cfg.get("starting_capital", 500000.0)
        trader = cls(starting_capital=starting)
        trader.portfolio.current_capital = cfg.get("current_capital", starting)
        trader.portfolio.max_risk_per_trade_pct = cfg.get("max_risk_per_trade_pct", 1.0)
        trader.portfolio.max_open_positions = int(cfg.get("max_open_positions", 3))
        trader.portfolio.trades = await store.get_all_trades()
        return trader


if __name__ == "__main__":
    # Quick demo
    trader = PaperTrader(starting_capital=500000)

    # Simulate some trades
    signals = [
        {"symbol": "NIFTY", "date": "2025-06-04", "signal": "STRONG_BUY",
         "signal_strength": 8, "entry_price": 24780, "stop_loss": 24620,
         "target1": 24950, "target2": 25100, "target3": 25300,
         "cpr_type": "NARROW", "is_virgin_cpr": True},
        {"symbol": "BANKNIFTY", "date": "2025-06-04", "signal": "SELL",
         "signal_strength": 6, "entry_price": 51950, "stop_loss": 52250,
         "target1": 51700, "target2": 51400, "target3": 51000,
         "cpr_type": "NORMAL", "is_virgin_cpr": False},
    ]

    for s in signals:
        trade = trader.open_position(s)
        if trade:
            print(f"Opened: {trade.symbol} {trade.direction} @ ₹{trade.entry_price} | Qty: {trade.quantity}")

    # Simulate price move - NIFTY hits target1
    trader.update_position("NIFTY", 24955)
    trader.update_position("BANKNIFTY", 52260)  # SL hit

    stats = trader.get_stats()
    print(f"\nStats: Trades={stats['total_trades']} | Win Rate={stats['win_rate']}%")
    print(f"Total P&L: ₹{stats['total_pnl']} | Capital Change: {stats['capital_change_pct']}%")
