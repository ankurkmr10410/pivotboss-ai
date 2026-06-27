"""
CPR Engine - Based on 'Secrets of Pivot Boss' by Frank Ochoa
Calculates CPR levels, pivot points, and generates trading signals
"""

from dataclasses import dataclass, asdict
from typing import Optional
from enum import Enum


class Signal(Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class CPRType(Enum):
    NARROW = "NARROW"     # Trending day expected
    WIDE = "WIDE"         # Sideways/consolidation day expected
    NORMAL = "NORMAL"     # Normal day


@dataclass
class CPRLevels:
    symbol: str
    date: str

    # Core CPR
    pivot: float
    tc: float   # Top Central
    bc: float   # Bottom Central
    cpr_width: float
    cpr_width_pct: float

    # Support levels
    s1: float
    s2: float
    s3: float

    # Resistance levels
    r1: float
    r2: float
    r3: float

    # Camarilla levels (bonus)
    camarilla_s1: float
    camarilla_s2: float
    camarilla_r1: float
    camarilla_r2: float

    # Classification
    cpr_type: str
    is_virgin: bool = False
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    prev_day_close: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class TradeSignal:
    symbol: str
    date: str
    signal: str
    strength: int          # 1-10
    entry_price: float
    stop_loss: float
    target1: float
    target2: float
    target3: float
    risk_reward: float
    reason: list
    cpr_levels: dict

    def to_dict(self):
        return asdict(self)


def calculate_cpr(symbol: str, date: str,
                  high: float, low: float, close: float,
                  prev_high: Optional[float] = None,
                  prev_low: Optional[float] = None,
                  prev_cpr_high: Optional[float] = None,
                  prev_cpr_low: Optional[float] = None) -> CPRLevels:
    """
    Calculate CPR and all pivot levels from daily OHLC data.

    CPR (Central Pivot Range) = TC, Pivot, BC
    - Pivot = (H + L + C) / 3
    - TC (Top Central) = (Pivot + High) / 2
    - BC (Bottom Central) = (Pivot + Low) / 2

    Standard Pivot Support/Resistance:
    - R1 = (2 * Pivot) - Low
    - R2 = Pivot + (High - Low)
    - R3 = High + 2 * (Pivot - Low)
    - S1 = (2 * Pivot) - High
    - S2 = Pivot - (High - Low)
    - S3 = Low - 2 * (High - Pivot)
    """
    pivot = (high + low + close) / 3
    tc = (pivot + high) / 2
    bc = (pivot + low) / 2

    # Ensure TC is always top, BC is always bottom
    if bc > tc:
        tc, bc = bc, tc

    cpr_width = tc - bc
    cpr_width_pct = (cpr_width / pivot) * 100

    # Standard pivot levels
    r1 = (2 * pivot) - low
    r2 = pivot + (high - low)
    r3 = high + 2 * (pivot - low)

    s1 = (2 * pivot) - high
    s2 = pivot - (high - low)
    s3 = low - 2 * (high - pivot)

    # Camarilla pivot levels (more precise intraday levels)
    rng = high - low
    camarilla_r1 = close + rng * 1.1 / 12
    camarilla_r2 = close + rng * 1.1 / 6
    camarilla_s1 = close - rng * 1.1 / 12
    camarilla_s2 = close - rng * 1.1 / 6

    # CPR classification based on width %
    # Pivot Boss: narrow CPR = trending day, wide = sideways
    if cpr_width_pct < 0.3:
        cpr_type = CPRType.NARROW.value
    elif cpr_width_pct > 0.8:
        cpr_type = CPRType.WIDE.value
    else:
        cpr_type = CPRType.NORMAL.value

    # Virgin CPR: previous CPR never tested (price never entered the CPR zone)
    is_virgin = False
    if prev_cpr_high and prev_cpr_low and prev_high and prev_low:
        # If yesterday's price range never overlapped with yesterday's CPR
        price_entered_cpr = (prev_low <= prev_cpr_high) and (prev_high >= prev_cpr_low)
        is_virgin = not price_entered_cpr

    return CPRLevels(
        symbol=symbol,
        date=date,
        pivot=round(pivot, 2),
        tc=round(tc, 2),
        bc=round(bc, 2),
        cpr_width=round(cpr_width, 2),
        cpr_width_pct=round(cpr_width_pct, 3),
        r1=round(r1, 2),
        r2=round(r2, 2),
        r3=round(r3, 2),
        s1=round(s1, 2),
        s2=round(s2, 2),
        s3=round(s3, 2),
        camarilla_r1=round(camarilla_r1, 2),
        camarilla_r2=round(camarilla_r2, 2),
        camarilla_s1=round(camarilla_s1, 2),
        camarilla_s2=round(camarilla_s2, 2),
        cpr_type=cpr_type,
        is_virgin=is_virgin,
        prev_day_high=high,
        prev_day_low=low,
        prev_day_close=close,
    )


def generate_signal(cpr: CPRLevels, current_price: float,
                    opening_price: float, market_open: bool = True) -> TradeSignal:
    """
    Generate a trading signal based on CPR levels and current price action.

    Key Pivot Boss rules implemented:
    1. Price above CPR → Bullish bias
    2. Price below CPR → Bearish bias
    3. Price inside CPR → Wait / Neutral
    4. Virgin CPR → Very strong magnet (high-conviction trade)
    5. Narrow CPR → Strong trending day expected
    6. Wide CPR → Expect sideways, fade extremes
    7. Open above TC → Buy on dips to TC
    8. Open below BC → Sell on rallies to BC
    9. Gap open above R1 → Watch for reversal
    10. Gap open below S1 → Watch for reversal
    """
    reasons = []
    strength = 5  # default neutral

    # Determine price position relative to CPR
    price_above_tc = current_price > cpr.tc
    price_below_bc = current_price < cpr.bc
    price_in_cpr = cpr.bc <= current_price <= cpr.tc

    # Opening gap analysis
    gap_up = opening_price > cpr.tc
    gap_down = opening_price < cpr.bc
    open_in_cpr = cpr.bc <= opening_price <= cpr.tc

    # --- SIGNAL LOGIC ---

    if price_in_cpr:
        signal = Signal.NEUTRAL
        strength = 3
        reasons.append("Price inside CPR — wait for breakout")

        if cpr.is_virgin:
            reasons.append("Virgin CPR acting as strong magnet — high-probability setup on breakout")
            strength = 5

        if cpr.cpr_type == CPRType.NARROW.value:
            reasons.append("Narrow CPR → Explosive move expected, wait for direction")
        elif cpr.cpr_type == CPRType.WIDE.value:
            reasons.append("Wide CPR → Sideways day likely, range trading opportunities")

    elif price_above_tc:
        signal = Signal.BUY
        strength = 6
        reasons.append(f"Price ₹{current_price} above TC ₹{cpr.tc} → Bullish bias")

        if gap_up:
            reasons.append("Gap up open above TC → Strong bullish momentum")
            strength += 1

        if cpr.is_virgin:
            reasons.append("Virgin CPR above → Very strong support, high-conviction BUY")
            strength += 2

        if cpr.cpr_type == CPRType.NARROW.value:
            reasons.append("Narrow CPR → Strong trending up day expected")
            strength += 1

        # Check proximity to resistance — penalty but don't drop below 5
        if current_price >= cpr.r1 * 0.995:
            reasons.append(f"Near R1 ₹{cpr.r1} → Watch for resistance/reversal")
            strength = max(5, strength - 1)

        if current_price >= cpr.r2 * 0.995:
            reasons.append(f"Near R2 ₹{cpr.r2} → Strong resistance, reduce position size")
            strength = max(5, strength - 1)

        # Strong buy if just broke above TC with volume confirmation
        if opening_price <= cpr.tc and current_price > cpr.tc:
            reasons.append("Breakout above TC during session → Strong BUY signal")
            strength = min(10, strength + 1)

        signal = Signal.STRONG_BUY if strength >= 8 else Signal.BUY

    else:  # price below BC
        signal = Signal.SELL
        strength = 6
        reasons.append(f"Price ₹{current_price} below BC ₹{cpr.bc} → Bearish bias")

        if gap_down:
            reasons.append("Gap down open below BC → Strong bearish momentum")
            strength += 1

        if cpr.is_virgin:
            reasons.append("Virgin CPR below → Very strong resistance, high-conviction SELL")
            strength += 2

        if cpr.cpr_type == CPRType.NARROW.value:
            reasons.append("Narrow CPR → Strong trending down day expected")
            strength += 1

        # Check proximity to support — penalty but don't drop below 5
        if current_price <= cpr.s1 * 1.005:
            reasons.append(f"Near S1 ₹{cpr.s1} → Watch for support/bounce")
            strength = max(5, strength - 1)

        if current_price <= cpr.s2 * 1.005:
            reasons.append(f"Near S2 ₹{cpr.s2} → Strong support, reduce short size")
            strength = max(5, strength - 1)

        signal = Signal.STRONG_SELL if strength >= 8 else Signal.SELL

    strength = min(10, max(1, strength))

    # --- ENTRY, SL, TARGET CALCULATION ---
    if signal in [Signal.BUY, Signal.STRONG_BUY]:
        entry = current_price
        stop_loss = round(cpr.bc * 0.998, 2)        # Just below BC
        target1 = round(cpr.r1, 2)
        target2 = round(cpr.r2, 2)
        target3 = round(cpr.r3, 2)
    elif signal in [Signal.SELL, Signal.STRONG_SELL]:
        entry = current_price
        stop_loss = round(cpr.tc * 1.002, 2)        # Just above TC
        target1 = round(cpr.s1, 2)
        target2 = round(cpr.s2, 2)
        target3 = round(cpr.s3, 2)
    else:
        entry = current_price
        stop_loss = round(cpr.bc * 0.995, 2)
        target1 = round(cpr.tc, 2)
        target2 = round(cpr.r1, 2)
        target3 = round(cpr.r2, 2)

    # Risk:Reward ratio
    risk = abs(entry - stop_loss)
    reward = abs(target1 - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0

    return TradeSignal(
        symbol=cpr.symbol,
        date=cpr.date,
        signal=signal.value,
        strength=strength,
        entry_price=round(entry, 2),
        stop_loss=stop_loss,
        target1=target1,
        target2=target2,
        target3=target3,
        risk_reward=rr,
        reason=reasons,
        cpr_levels=cpr.to_dict()
    )


# ── SAMPLE DATA for testing ──────────────────────────────────────────────────

SAMPLE_DATA = {
    "NIFTY": {
        "symbol": "NIFTY",
        "date": "2025-06-04",
        "high": 24850.0,
        "low": 24450.0,
        "close": 24700.0,
        "current_price": 24780.0,
        "opening_price": 24720.0,
        "prev_high": 24820.0,
        "prev_low": 24580.0,
        "prev_cpr_high": 24750.0,
        "prev_cpr_low": 24650.0,
    },
    "BANKNIFTY": {
        "symbol": "BANKNIFTY",
        "date": "2025-06-04",
        "high": 52400.0,
        "low": 51800.0,
        "close": 52100.0,
        "current_price": 51950.0,
        "opening_price": 52050.0,
        "prev_high": 52300.0,
        "prev_low": 51900.0,
        "prev_cpr_high": 52200.0,
        "prev_cpr_low": 52050.0,
    },
    "RELIANCE": {
        "symbol": "RELIANCE",
        "date": "2025-06-04",
        "high": 2980.0,
        "low": 2920.0,
        "close": 2955.0,
        "current_price": 2968.0,
        "opening_price": 2960.0,
        "prev_high": 2975.0,
        "prev_low": 2930.0,
        "prev_cpr_high": 2965.0,
        "prev_cpr_low": 2940.0,
    }
}


def run_analysis(data: dict) -> dict:
    cpr = calculate_cpr(
        symbol=data["symbol"],
        date=data["date"],
        high=data["high"],
        low=data["low"],
        close=data["close"],
        prev_high=data.get("prev_high"),
        prev_low=data.get("prev_low"),
        prev_cpr_high=data.get("prev_cpr_high"),
        prev_cpr_low=data.get("prev_cpr_low"),
    )
    signal = generate_signal(
        cpr=cpr,
        current_price=data["current_price"],
        opening_price=data["opening_price"],
    )
    return {"cpr": cpr.to_dict(), "signal": signal.to_dict()}


if __name__ == "__main__":
    import io
    import sys as _sys
    # Ensure stdout handles Unicode on Windows (cp1252 lacks → ₹ etc.)
    _sys.stdout = io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")

    import json
    for sym, data in SAMPLE_DATA.items():
        result = run_analysis(data)
        print(f"\n{'='*60}")
        print(f"  {sym} ANALYSIS")
        print(f"{'='*60}")
        cpr = result["cpr"]
        sig = result["signal"]
        print(f"  CPR: BC={cpr['bc']} | Pivot={cpr['pivot']} | TC={cpr['tc']}")
        print(f"  Width: {cpr['cpr_width_pct']}% → {cpr['cpr_type']}")
        print(f"  Virgin CPR: {cpr['is_virgin']}")
        print(f"  S1={cpr['s1']} | S2={cpr['s2']} | R1={cpr['r1']} | R2={cpr['r2']}")
        print(f"\n  SIGNAL: {sig['signal']} (Strength: {sig['strength']}/10)")
        print(f"  Entry: ₹{sig['entry_price']} | SL: ₹{sig['stop_loss']}")
        print(f"  T1: ₹{sig['target1']} | T2: ₹{sig['target2']} | T3: ₹{sig['target3']}")
        print(f"  R:R = 1:{sig['risk_reward']}")
        print(f"\n  Reasons:")
        for r in sig["reason"]:
            print(f"    • {r}")
