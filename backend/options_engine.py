"""
Options Trading Engine for PivotBoss AI
========================================
Handles strike selection, expiry calculation, option chain fetching,
and paper/live order placement for NIFTY and BANKNIFTY weekly options.

Strategy:
  CPR BUY  signal -> Buy ATM/OTM Call (CE)
  CPR SELL signal -> Buy ATM/OTM Put  (PE)

Exit rules:
  Target: premium doubles (2x entry premium)
  SL:     premium drops 50% (0.5x entry premium)
  EOD:    exit at 3:20 PM to avoid theta decay
"""

import os
import logging
from datetime import date, datetime, timedelta
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── CONSTANTS ──────────────────────────────────────────────────────────────────

STRIKE_STEP = {
    "NIFTY":     50,
    "BANKNIFTY": 100,
}

LOT_SIZE = {
    "NIFTY":     75,
    "BANKNIFTY": 35,
}

OPTIONS_RISK_PCT    = 0.02   # 2% of capital per trade
OPTIONS_TARGET_MULT = 2.0    # exit when premium 2x
OPTIONS_SL_MULT     = 0.5    # exit when premium 50% below entry


# ── DATACLASSES ────────────────────────────────────────────────────────────────

@dataclass
class OptionContract:
    """Represents a specific option contract."""
    symbol:        str
    expiry:        str
    strike:        int
    option_type:   str
    tradingsymbol: str
    lot_size:      int
    lots:          int = 1
    direction:     str = ""


@dataclass
class OptionTrade:
    """A paper or live options trade."""
    id:              str
    symbol:          str
    contract:        str
    option_type:     str
    strike:          int
    expiry:          str
    lots:            int
    entry_premium:   float
    target_premium:  float
    sl_premium:      float
    entry_time:      str
    exit_premium:    float = 0.0
    exit_time:       str   = ""
    exit_reason:     str   = ""
    status:          str   = "OPEN"

    @property
    def pnl_points(self) -> float:
        if self.exit_premium == 0:
            return 0.0
        return (self.exit_premium - self.entry_premium) * LOT_SIZE.get(self.symbol, 1) * self.lots

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "symbol":         self.symbol,
            "contract":       self.contract,
            "option_type":    self.option_type,
            "strike":         self.strike,
            "expiry":         self.expiry,
            "lots":           self.lots,
            "entry_premium":  self.entry_premium,
            "target_premium": self.target_premium,
            "sl_premium":     self.sl_premium,
            "entry_time":     self.entry_time,
            "exit_premium":   self.exit_premium,
            "exit_time":      self.exit_time,
            "exit_reason":    self.exit_reason,
            "status":         self.status,
            "pnl_points":     self.pnl_points,
        }


# ── EXPIRY CALCULATOR ──────────────────────────────────────────────────────────

def get_nearest_expiry(symbol: str, from_date: Optional[date] = None) -> date:
    """
    Get the nearest weekly expiry date.
    NIFTY     expires on Thursday.
    BANKNIFTY expires on Wednesday.
    """
    today = from_date or date.today()
    expiry_weekday = 3 if symbol == "NIFTY" else 2   # Thu=3, Wed=2
    now = datetime.now()

    days_ahead = expiry_weekday - today.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0 and now.hour >= 15:
        days_ahead = 7

    return today + timedelta(days=days_ahead)


def format_expiry_kotak(expiry: date) -> str:
    """Format expiry date into Kotak Neo trading symbol format."""
    yy  = expiry.strftime("%y")
    mon = expiry.strftime("%b").upper()[:3]
    dd  = expiry.strftime("%d")
    return f"{yy}{mon}{dd}"


def get_atm_strike(spot_price: float, symbol: str) -> int:
    """Round spot price to nearest valid strike."""
    step = STRIKE_STEP.get(symbol, 50)
    return int(round(spot_price / step) * step)


def get_otm_strike(spot_price: float, symbol: str, option_type: str, steps: int = 1) -> int:
    """Get an OTM strike N steps away from ATM."""
    step = STRIKE_STEP.get(symbol, 50)
    atm = get_atm_strike(spot_price, symbol)
    if option_type == "CE":
        return atm + step * steps
    else:
        return atm - step * steps


def build_trading_symbol(symbol: str, expiry: date, strike: int, option_type: str) -> str:
    """Build Kotak Neo trading symbol, e.g. NIFTY24JUN2724150CE"""
    exp_str = format_expiry_kotak(expiry)
    return f"{symbol}{exp_str}{strike}{option_type}"


# ── STRIKE SELECTOR ────────────────────────────────────────────────────────────

def select_option_contract(
    symbol:      str,
    spot_price:  float,
    direction:   str,
    capital:     float = 500000,
    strike_type: str   = "ATM",
) -> OptionContract:
    """Select the best option contract for a given CPR signal."""
    option_type = "CE" if direction in ("BUY", "STRONG_BUY") else "PE"
    expiry      = get_nearest_expiry(symbol)
    lot_size    = LOT_SIZE.get(symbol, 75)

    if strike_type == "OTM1":
        strike = get_otm_strike(spot_price, symbol, option_type, steps=1)
    else:
        strike = get_atm_strike(spot_price, symbol)

    tradingsymbol = build_trading_symbol(symbol, expiry, strike, option_type)

    risk_amount = capital * OPTIONS_RISK_PCT
    est_premium = spot_price * (0.01 if symbol == "NIFTY" else 0.005)
    est_premium = max(est_premium, 50)

    sl_loss_per_lot = est_premium * lot_size * (1 - OPTIONS_SL_MULT)
    lots = max(1, int(risk_amount / sl_loss_per_lot)) if sl_loss_per_lot > 0 else 1

    contract = OptionContract(
        symbol=symbol,
        expiry=expiry.strftime("%d-%b-%Y"),
        strike=strike,
        option_type=option_type,
        tradingsymbol=tradingsymbol,
        lot_size=lot_size,
        lots=lots,
        direction=direction,
    )

    logger.info(
        "Option selected: %s | Strike: %d %s | Expiry: %s | Lots: %d",
        symbol, strike, option_type, contract.expiry, lots
    )
    return contract


# ── PAPER OPTIONS TRADER ───────────────────────────────────────────────────────

class PaperOptionsTrader:
    """Paper trading engine for options. Tracks open positions and simulates P&L."""

    def __init__(self):
        self.open_trades   = []
        self.closed_trades = []
        self._trade_counter = 0

    def open_trade(self, contract: OptionContract, entry_premium: float) -> OptionTrade:
        """Open a new paper options trade."""
        self._trade_counter += 1
        trade_id = f"OPT{self._trade_counter:04d}"

        trade = OptionTrade(
            id=trade_id,
            symbol=contract.symbol,
            contract=contract.tradingsymbol,
            option_type=contract.option_type,
            strike=contract.strike,
            expiry=contract.expiry,
            lots=contract.lots,
            entry_premium=entry_premium,
            target_premium=round(entry_premium * OPTIONS_TARGET_MULT, 2),
            sl_premium=round(entry_premium * OPTIONS_SL_MULT, 2),
            entry_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        self.open_trades.append(trade)
        logger.info(
            "[PAPER OPTIONS] Opened %s | %s | Entry: Rs%.2f | Target: Rs%.2f | SL: Rs%.2f",
            trade_id, contract.tradingsymbol, entry_premium,
            trade.target_premium, trade.sl_premium
        )
        return trade

    def monitor_trades(self, current_premiums: dict) -> list:
        """Check all open trades against current premiums. Returns closed trades."""
        closed = []
        still_open = []
        now = datetime.now()

        for trade in self.open_trades:
            current = current_premiums.get(trade.contract)
            if current is None:
                still_open.append(trade)
                continue

            reason = None
            if current >= trade.target_premium:
                reason = "TARGET"
            elif current <= trade.sl_premium:
                reason = "SL"
            elif now.hour >= 15 and now.minute >= 20:
                reason = "EOD"

            if reason:
                trade.exit_premium = current
                trade.exit_time    = now.strftime("%Y-%m-%d %H:%M:%S")
                trade.exit_reason  = reason
                trade.status       = "CLOSED"
                self.closed_trades.append(trade)
                closed.append(trade)
                logger.info(
                    "[PAPER OPTIONS] Closed %s | %s | P&L: Rs%.0f | Reason: %s",
                    trade.id, trade.contract, trade.pnl_points, reason
                )
            else:
                still_open.append(trade)

        self.open_trades = still_open
        return closed

    def eod_exit_all(self, current_premiums: dict) -> list:
        """Force close all open trades at EOD."""
        for trade in self.open_trades:
            trade.exit_premium = current_premiums.get(trade.contract, trade.entry_premium * 0.8)
            trade.exit_time    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            trade.exit_reason  = "EOD"
            trade.status       = "CLOSED"
            self.closed_trades.append(trade)
        closed = self.open_trades.copy()
        self.open_trades = []
        return closed

    def summary(self) -> dict:
        all_trades = self.open_trades + self.closed_trades
        closed = self.closed_trades
        wins = [t for t in closed if t.pnl_points > 0]
        losses = [t for t in closed if t.pnl_points < 0]
        total_pnl = sum(t.pnl_points for t in closed)
        return {
            "total_trades":  len(all_trades),
            "open_trades":   len(self.open_trades),
            "closed_trades": len(closed),
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(len(wins) / len(closed) * 100, 2) if closed else 0,
            "total_pnl":     round(total_pnl, 2),
            "avg_win":       round(sum(t.pnl_points for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss":      round(sum(t.pnl_points for t in losses) / len(losses), 2) if losses else 0,
        }
