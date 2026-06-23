"""
Tests for the FastAPI server (backend/api_server.py).

Uses FastAPI's TestClient (httpx) against a temp-file DB so tests are
fully offline and isolated.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi.testclient import TestClient
from db import MarketStore
from data_provider import Candle
from cpr_engine import CPRLevels, TradeSignal
from paper_trader import PaperTrader, Trade, PaperPortfolio
from api_server import app, store
import asyncio


def run(coro):
    return asyncio.run(coro)


def sample_cpr(sym="NIFTY", d="2026-06-20") -> CPRLevels:
    return CPRLevels(
        symbol=sym, date=d, pivot=100.0, tc=101.0, bc=99.0, cpr_width=2.0,
        cpr_width_pct=0.02, s1=98.0, s2=96.0, s3=94.0, r1=102.0, r2=104.0,
        r3=106.0, camarilla_s1=98.5, camarilla_s2=97.5, camarilla_r1=101.5,
        camarilla_r2=102.5, cpr_type="NARROW", is_virgin=True,
        prev_day_high=102.0, prev_day_low=98.0, prev_day_close=100.0,
    )


def sample_signal(sym="NIFTY", d="2026-06-20") -> TradeSignal:
    return TradeSignal(
        symbol=sym, date=d, signal="STRONG_BUY", strength=8,
        entry_price=100.5, stop_loss=98.0, target1=103.0, target2=105.0,
        target3=108.0, risk_reward=2.0, reason=["above CPR", "narrow"],
        cpr_levels={},
    )


def sample_trade(id="api01", sym="NIFTY", status="OPEN") -> Trade:
    return Trade(
        id=id, symbol=sym, date="2026-06-20", time="09:30:00",
        direction="LONG", entry_price=100.5, stop_loss=98.0,
        target1=103.0, target2=105.0, target3=108.0, quantity=50,
        signal_type="STRONG_BUY", signal_strength=8, cpr_type="NARROW",
        is_virgin_cpr=True, status=status,
    )


# ── fixture: seed the module-level store with a temp DB ──────────────────────

tmp_db = Path("D:/pivotboss-ai/data/test_api.db")

async def seed():
    store.db_path = tmp_db
    await store.init()
    # Seed candles
    candles = [Candle(f"2026-06-{d:02d}", 100 + d, 102 + d, 99 + d, 101 + d, 500)
               for d in range(10, 20)]
    await store.upsert_symbol_candles("NIFTY", candles)
    # Seed CPR + signal
    await store.save_cpr(sample_cpr())
    await store.save_signal(sample_signal())
    # Seed a trade + portfolio config
    await store.upsert_trades([sample_trade()])
    pf = PaperPortfolio(starting_capital=500000, current_capital=500000)
    await store.save_portfolio_config(pf)
    await store.append_equity(500000, 1)

run(seed())

client = TestClient(app)


# ── tests ──────────────────────────────────────────────────────────────────

def test_status_returns_ok():
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "counts" in data
    assert data["counts"]["candles"] > 0
    assert "NIFTY" in data["watchlist"]


def test_portfolio_returns_config_and_stats():
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    data = r.json()
    assert data["starting_capital"] == 500000
    assert "stats" in data
    assert "win_rate" in data["stats"]


def test_trades_returns_list():
    r = client.get("/api/trades")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["id"] == "api01"


def test_trades_filter_open():
    r = client.get("/api/trades?status=OPEN")
    assert r.status_code == 200
    data = r.json()
    assert all(t["status"] == "OPEN" for t in data)


def test_signals_returns_latest_per_symbol():
    r = client.get("/api/signals")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # At least NIFTY should have a signal
    syms = [s["symbol"] for s in data]
    assert "NIFTY" in syms


def test_signals_for_symbol():
    r = client.get("/api/signals/NIFTY")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["signal"] == "STRONG_BUY"


def test_cpr_latest():
    r = client.get("/api/cpr/NIFTY")
    assert r.status_code == 200
    data = r.json()
    assert data["pivot"] == 100.0
    assert data["cpr_type"] == "NARROW"
    assert data["is_virgin"] is True


def test_cpr_specific_date():
    r = client.get("/api/cpr/NIFTY/2026-06-20")
    assert r.status_code == 200
    assert r.json()["pivot"] == 100.0


def test_cpr_missing_returns_404():
    r = client.get("/api/cpr/NOPE")
    assert r.status_code == 404


def test_watchlist_returns_symbols():
    r = client.get("/api/watchlist")
    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data]
    assert "NIFTY" in names
    assert "RELIANCE" in names
    # Each entry has expected fields
    assert data[0].get("lot_size") is not None


def test_candles_with_days_param():
    r = client.get("/api/candles/NIFTY?days=30")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "date" in data[0]
    assert "close" in data[0]


def test_equity_returns_curve():
    r = client.get("/api/equity")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "capital" in data[0]
    assert "timestamp" in data[0]


def test_dashboard_served_at_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "PivotBoss" in r.text
    assert "CPR" in r.text


# ── cleanup ──────────────────────────────────────────────────────────────

def _cleanup():
    if tmp_db.exists():
        tmp_db.unlink()
    journal = tmp_db.with_suffix(".db-journal")
    if journal.exists():
        journal.unlink()

import atexit
atexit.register(_cleanup)
