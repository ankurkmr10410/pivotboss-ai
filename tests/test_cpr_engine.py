"""Offline unit tests for the CPR engine (no network, no broker)."""

import os
import sys
from pathlib import Path

# Make backend/ importable when running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from cpr_engine import calculate_cpr, generate_signal, CPRLevels, CPRType, Signal


def test_cpr_basic_levels():
    """Pivot = (H+L+C)/3; TC and BC derived from pivot and high/low."""
    cpr = calculate_cpr("XYZ", "2025-01-01", high=110, low=100, close=105)

    expected_pivot = (110 + 100 + 105) / 3
    assert cpr.pivot == round(expected_pivot, 2)
    assert cpr.tc == round((expected_pivot + 110) / 2, 2)   # (pivot+high)/2
    assert cpr.bc == round((expected_pivot + 100) / 2, 2)   # (pivot+low)/2


def test_cpr_tc_always_above_bc():
    """Even with an inverted-looking input, TC must end up >= BC."""
    cpr = calculate_cpr("XYZ", "2025-01-01", high=100, low=90, close=95)
    assert cpr.tc >= cpr.bc


def test_cpr_width_and_classification():
    # Very tight range → narrow CPR (trending day)
    narrow = calculate_cpr("XYZ", "2025-01-01", high=100.2, low=100.0, close=100.1)
    assert narrow.cpr_type == CPRType.NARROW.value
    assert narrow.cpr_width_pct < 0.3

    # Wide range → wide CPR (sideways day)
    wide = calculate_cpr("XYZ", "2025-01-01", high=150, low=50, close=100)
    assert wide.cpr_type == CPRType.WIDE.value
    assert wide.cpr_width_pct > 0.8


def test_pivot_support_resistance():
    """Standard pivot maths: R1 = 2*Pivot - Low; S1 = 2*Pivot - High."""
    cpr = calculate_cpr("XYZ", "2025-01-01", high=110, low=100, close=105)
    pivot = (110 + 100 + 105) / 3
    assert cpr.r1 == round(2 * pivot - 100, 2)
    assert cpr.s1 == round(2 * pivot - 110, 2)
    # R/S should ladder outward: R1<R2<R3 and S1>S2>S3
    assert cpr.r1 < cpr.r2 < cpr.r3
    assert cpr.s1 > cpr.s2 > cpr.s3


def test_signal_above_tc_is_buyish():
    cpr = calculate_cpr("XYZ", "2025-01-01", high=110, low=100, close=105)
    sig = generate_signal(cpr, current_price=120, opening_price=108)  # well above TC
    assert sig.signal in (Signal.BUY.value, Signal.STRONG_BUY.value)
    assert sig.target1 == cpr.r1


def test_signal_below_bc_is_sellish():
    cpr = calculate_cpr("XYZ", "2025-01-01", high=110, low=100, close=105)
    sig = generate_signal(cpr, current_price=90, opening_price=97)   # well below BC
    assert sig.signal in (Signal.SELL.value, Signal.STRONG_SELL.value)
    assert sig.target1 == cpr.s1


def test_signal_inside_cpr_is_neutral():
    cpr = calculate_cpr("XYZ", "2025-01-01", high=110, low=100, close=105)
    sig = generate_signal(cpr, current_price=(cpr.tc + cpr.bc) / 2,
                          opening_price=(cpr.tc + cpr.bc) / 2)
    assert sig.signal == Signal.NEUTRAL.value


def test_signal_reasons_are_list():
    cpr = calculate_cpr("XYZ", "2025-01-01", high=110, low=100, close=105)
    sig = generate_signal(cpr, current_price=120, opening_price=108)
    assert isinstance(sig.reason, list) and len(sig.reason) >= 1


def test_signal_dict_roundtrip():
    cpr = calculate_cpr("XYZ", "2025-01-01", high=110, low=100, close=105)
    sig = generate_signal(cpr, current_price=120, opening_price=108)
    d = sig.to_dict()
    assert d["symbol"] == "XYZ"
    assert "entry_price" in d and "stop_loss" in d
