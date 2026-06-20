"""Backtester tests — deterministic, synthetic candle series (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from backtester import CPRBacktester, BacktestResult, BacktestTrade


def _up_trend(n=20, start=100.0):
    """Synthetic rising series: each day closes higher than it opens."""
    out = []
    px = start
    for i in range(n):
        o = px
        c = round(px + 2.0, 2)
        h = round(c + 3.0, 2)
        lo = round(o - 1.0, 2)
        out.append({"date": f"2024-01-{i + 1:02d}", "open": o, "high": h, "low": lo, "close": c})
        px = c
    return out


def _down_trend(n=20, start=200.0):
    """Synthetic falling series."""
    out = []
    px = start
    for i in range(n):
        o = px
        c = round(px - 2.0, 2)
        h = round(o + 1.0, 2)
        lo = round(c - 3.0, 2)
        out.append({"date": f"2024-02-{i + 1:02d}", "open": o, "high": h, "low": lo, "close": c})
        px = c
    return out


def test_backtester_runs_on_uptrend():
    bt = CPRBacktester(min_strength=5, exit_model="pessimistic")
    result = bt.run("TESTUP", _up_trend())
    assert isinstance(result, BacktestResult)
    # An uptrend should generate at least some trades (price keeps rising above CPR).
    assert result.start_date == "2024-01-01"


def test_too_few_candles_returns_empty():
    bt = CPRBacktester(min_strength=5)
    result = bt.run("TEST", [{"date": "2024-01-01", "open": 100, "high": 101, "low": 99, "close": 100.5}])
    assert result.trades == []
    assert result.metrics()["total_trades"] == 0


def test_pessimistic_vs_optimistic_differ_on_same_data():
    """Same data, different exit models → potentially different net points.
    At minimum both must run without error and return valid metrics."""
    candles = _up_trend(30)
    pess = CPRBacktester(min_strength=5, exit_model="pessimistic").run("X", candles)
    opt = CPRBacktester(min_strength=5, exit_model="optimistic").run("X", candles)
    pm, om = pess.metrics(), opt.metrics()
    # Optimistic should be >= pessimistic in expectancy (by construction).
    assert om["expectancy_points"] >= pm["expectancy_points"]


def test_min_strength_filter_reduces_trades():
    candles = _up_trend(30)
    loose = CPRBacktester(min_strength=1).run("X", candles)
    strict = CPRBacktester(min_strength=9).run("X", candles)
    assert len(loose.trades) >= len(strict.trades)


def test_metrics_have_expected_keys():
    bt = CPRBacktester(min_strength=5)
    result = bt.run("X", _up_trend(25))
    m = result.metrics()
    for key in ("total_trades", "net_points", "win_rate", "expectancy_points",
                "profit_factor", "max_drawdown_pct"):
        assert key in m


def test_invalid_exit_model_raises():
    try:
        CPRBacktester(exit_model="bogus")
        assert False, "should have raised"
    except ValueError:
        pass


def test_breakdowns_return_dicts():
    bt = CPRBacktester(min_strength=5)
    result = bt.run("X", _up_trend(25))
    assert isinstance(result.breakdown_by_cpr_type(), dict)
    assert isinstance(result.breakdown_by_signal(), dict)


def test_trade_dict_roundtrip():
    t = BacktestTrade(
        entry_date="2024-01-01", signal="BUY", direction="LONG", cpr_type="NORMAL",
        is_virgin=False, entry=100, stop_loss=98, target1=103, target2=105, target3=108,
        exit_date="2024-01-01", exit_price=103, exit_reason="T1",
        pnl_points=3.0, pnl_pct=3.0, strength=7,
    )
    d = t.to_dict()
    assert d["signal"] == "BUY" and d["pnl_points"] == 3.0
