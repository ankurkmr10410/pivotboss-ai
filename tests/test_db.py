"""
Tests for the async SQLite persistence layer (backend/db.py).

All tests run against a temp-file DB (via the pytest `tmp_path` fixture) —
no real data dir, no network, no shared state between tests.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

# Bootstrap backend/ onto sys.path (matches every other test file).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import pytest

from cpr_engine import CPRLevels, TradeSignal
from data_provider import Candle
from db import MarketStore
from paper_trader import PaperTrader, Trade, PaperPortfolio


# ── helpers ────────────────────────────────────────────────────────────────

def run(coro):
    """Run an async coroutine to completion (sync test surface)."""
    return asyncio.run(coro)


def make_store(tmp_path) -> MarketStore:
    return MarketStore(db_path=tmp_path / "test.db")


def sample_cpr(symbol: str = "NIFTY", d: str = "2026-06-20") -> CPRLevels:
    return CPRLevels(
        symbol=symbol, date=d,
        pivot=100.0, tc=101.0, bc=99.0, cpr_width=2.0, cpr_width_pct=0.02,
        s1=98.0, s2=96.0, s3=94.0, r1=102.0, r2=104.0, r3=106.0,
        camarilla_s1=98.5, camarilla_s2=97.5, camarilla_r1=101.5, camarilla_r2=102.5,
        cpr_type="NARROW", is_virgin=True,
        prev_day_high=102.0, prev_day_low=98.0, prev_day_close=100.0,
    )


def sample_signal(symbol: str = "NIFTY", d: str = "2026-06-20") -> TradeSignal:
    return TradeSignal(
        symbol=symbol, date=d, signal="STRONG_BUY", strength=8,
        entry_price=100.5, stop_loss=98.0, target1=103.0, target2=105.0,
        target3=108.0, risk_reward=2.0, reason=["above CPR", "narrow"],
        cpr_levels={},
    )


def sample_trade(id: str = "abc12345", symbol: str = "NIFTY",
                 status: str = "OPEN") -> Trade:
    return Trade(
        id=id, symbol=symbol, date="2026-06-20", time="09:30:00",
        direction="LONG", entry_price=100.5, stop_loss=98.0,
        target1=103.0, target2=105.0, target3=108.0, quantity=50,
        signal_type="STRONG_BUY", signal_strength=8, cpr_type="NARROW",
        is_virgin_cpr=True, status=status,
    )


# ── tests ──────────────────────────────────────────────────────────────────

def test_init_creates_db_and_tables(tmp_path):
    store = make_store(tmp_path)
    assert not store.db_path.exists()
    run(store.init())
    assert store.db_path.exists()
    # All six tables should exist.
    import aiosqlite
    async def _tables():
        async with aiosqlite.connect(store.db_path) as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [r[0] for r in await cur.fetchall()]
    names = run(_tables())
    for expected in ("candles", "cpr_levels", "signals", "paper_trades",
                     "portfolio_config", "equity_curve"):
        assert expected in names, f"missing table {expected}"


def test_init_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    run(store.init())   # second call must not error
    assert store.db_path.exists()


def test_candle_upsert_and_get_roundtrip(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    candles = [Candle(f"2026-06-{d:02d}", 100 + d, 102 + d, 99 + d, 101 + d, 1000)
               for d in range(15, 20)]
    written = run(store.upsert_symbol_candles("NIFTY", candles, source="yahoo"))
    assert written == 5
    assert run(store.candle_count("NIFTY")) == 5

    got = run(store.get_candles("NIFTY"))
    assert len(got) == 5
    assert got[0].date == "2026-06-15"
    # close = 101 + d; last d is 19 → 120.0
    assert got[-1].close == 120.0
    # Field-level check
    assert got[0].open == 115.0 and got[0].volume == 1000


def test_candle_upsert_is_idempotent_on_conflict(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    c = Candle("2026-06-15", 100.0, 102.0, 99.0, 101.0, 1000)
    run(store.upsert_symbol_candles("RELIANCE", [c]))
    # Same date, new values — should UPDATE not duplicate.
    c2 = Candle("2026-06-15", 100.0, 105.0, 99.0, 104.0, 2000)
    run(store.upsert_symbol_candles("RELIANCE", [c2]))
    assert run(store.candle_count("RELIANCE")) == 1
    got = run(store.get_candles("RELIANCE"))[0]
    assert got.high == 105.0 and got.volume == 2000


def test_candle_date_filtering(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    candles = [Candle(f"2026-06-{d:02d}", 100, 101, 99, 100, 0)
               for d in [10, 12, 14, 16, 18, 20]]
    run(store.upsert_symbol_candles("NIFTY", candles))
    got = run(store.get_candles("NIFTY", start="2026-06-14", end="2026-06-18"))
    assert [c.date for c in got] == ["2026-06-14", "2026-06-16", "2026-06-18"]


def test_cpr_save_and_get_roundtrip(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    cpr = sample_cpr()
    run(store.save_cpr(cpr))
    got = run(store.get_cpr("NIFTY", "2026-06-20"))
    assert got is not None
    assert got.symbol == "NIFTY"
    assert got.pivot == 100.0
    assert got.tc == 101.0
    assert got.is_virgin is True
    assert got.cpr_type == "NARROW"
    # date field reconstructed from cpr_date column
    assert got.date == "2026-06-20"


def test_cpr_missing_returns_none(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    assert run(store.get_cpr("NOPE", "2026-01-01")) is None


def test_signal_save_and_get_roundtrip(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    # Signal has an FK → cpr_levels, so the parent row must exist first.
    run(store.save_cpr(sample_cpr()))
    run(store.save_signal(sample_signal()))
    got = run(store.get_signal("NIFTY", "2026-06-20"))
    assert got is not None
    assert got["signal"] == "STRONG_BUY"
    assert got["strength"] == 8
    # reason list should round-trip through JSON
    assert got["reason"] == ["above CPR", "narrow"]
    assert got["target1"] == 103.0


def test_save_analysis_persists_cpr_and_signal(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    prev_candle = {"date": "2026-06-19", "open": 99, "high": 102, "low": 98,
                   "close": 100, "volume": 1500}
    run(store.save_analysis(
        "NIFTY", sample_cpr(), prev_candle=prev_candle, signal=sample_signal()
    ))
    assert run(store.get_cpr("NIFTY", "2026-06-20")) is not None
    assert run(store.get_signal("NIFTY", "2026-06-20")) is not None
    # prev_candle should have been cached into the candles table.
    assert run(store.candle_count("NIFTY")) == 1


def test_trade_upsert_get_all_get_open(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    open_t = sample_trade(id="open01", status="OPEN")
    closed_t = sample_trade(id="closed01", symbol="RELIANCE", status="CLOSED_SL")
    run(store.upsert_trades([open_t, closed_t]))

    all_trades = run(store.get_all_trades())
    assert len(all_trades) == 2
    open_trades = run(store.get_open_trades())
    assert len(open_trades) == 1
    assert open_trades[0].id == "open01"
    assert open_trades[0].is_virgin_cpr is True   # bool reconstructed


def test_trade_upsert_updates_on_conflict(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    t = sample_trade(id="sameid", status="OPEN")
    run(store.upsert_trade(t))
    # Close it via a re-upsert with new exit fields.
    t.status = "CLOSED_TARGET1"
    t.exit_price = 103.0
    t.pnl = 125.0
    run(store.upsert_trade(t))
    all_t = run(store.get_all_trades())
    assert len(all_t) == 1
    assert all_t[0].status == "CLOSED_TARGET1"
    assert all_t[0].exit_price == 103.0
    assert all_t[0].pnl == 125.0
    assert run(store.get_open_trades()) == []


def test_portfolio_config_roundtrip(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    pf = PaperPortfolio(
        starting_capital=750000.0, current_capital=760000.0,
        max_risk_per_trade_pct=1.5, max_open_positions=5,
    )
    run(store.save_portfolio_config(pf))
    cfg = run(store.load_portfolio_config())
    assert cfg["starting_capital"] == 750000.0
    assert cfg["current_capital"] == 760000.0
    assert cfg["max_risk_per_trade_pct"] == 1.5
    assert cfg["max_open_positions"] == 5


def test_equity_curve_append_and_read(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    run(store.append_equity(500000.0, 0))
    run(store.append_equity(502500.0, 1))
    curve = run(store.get_equity_curve())
    assert len(curve) == 2
    assert curve[0]["capital"] == 500000.0
    assert curve[1]["open_positions"] == 1
    assert "timestamp" in curve[0]


def test_migrate_from_json_imports_trades_and_config(tmp_path):
    store = make_store(tmp_path)
    run(store.init())

    # Build a synthetic paper_trades.json exactly like PaperTrader emits.
    # Written directly to avoid the pre-existing get_stats() scoping bug.
    payload = {
        "starting_capital": 600000,
        "current_capital": 605000,
        "max_risk_per_trade_pct": 2.0,
        "max_open_positions": 4,
        "trades": [
            sample_trade(id="migrated01", symbol="NIFTY").to_dict(),
            sample_trade(id="migrated02", symbol="TCS", status="CLOSED_SL").to_dict(),
        ],
    }
    json_path = tmp_path / "paper_trades.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    # DB is empty → migration should run.
    assert run(store.needs_migration(json_path)) is True
    imported = run(store.migrate_from_json(json_path))
    assert imported == 2

    # After migration, no longer needed.
    assert run(store.needs_migration(json_path)) is False

    # Trades imported.
    all_t = run(store.get_all_trades())
    assert len(all_t) == 2
    assert {t.id for t in all_t} == {"migrated01", "migrated02"}

    # Portfolio config imported — including the fields the old loader dropped.
    cfg = run(store.load_portfolio_config())
    assert cfg["starting_capital"] == 600000
    assert cfg["current_capital"] == 605000
    assert cfg["max_risk_per_trade_pct"] == 2.0
    assert cfg["max_open_positions"] == 4


def test_migrate_skips_when_no_json(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    assert run(store.needs_migration(tmp_path / "nonexistent.json")) is False
    assert run(store.migrate_from_json(tmp_path / "nonexistent.json")) == 0


def test_bulk_candle_write_count(tmp_path):
    store = make_store(tmp_path)
    run(store.init())
    candles = [Candle(f"2025-01-{d:02d}", 100, 101, 99, 100, 500)
               for d in range(1, 31)]
    n = run(store.upsert_symbol_candles("BANKNIFTY", candles, source="kotak"))
    assert n == 30
    assert run(store.candle_count("BANKNIFTY")) == 30
    got = run(store.get_candles("BANKNIFTY"))
    assert got[0].date == "2025-01-01"
    assert got[-1].date == "2025-01-30"
