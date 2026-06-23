"""
FastAPI server (Phase 2.3) — serves the dashboard and exposes REST endpoints
backed by the SQLite store (Phase 2.5), with an integrated APScheduler
for the daily trading cycle (Phase 2.1).

Endpoints:
  GET  /api/status              — health + DB row counts
  GET  /api/portfolio           — capital, stats, config
  GET  /api/trades              — all paper trades (open + closed)
  GET  /api/signals             — latest signal per symbol
  GET  /api/signals/{symbol}    — signals for one symbol (history)
  GET  /api/cpr/{symbol}        — latest CPR levels for a symbol
  GET  /api/cpr/{symbol}/{date} — CPR levels for a specific date
  GET  /api/watchlist           — watchlist from config/watchlist.yaml
  GET  /api/candles/{symbol}    — cached OHLCV (optional ?days=N&start=&end=)
  GET  /api/equity              — equity curve snapshots
  GET  /api/scheduler           — scheduler status + last run results

Also serves the dashboard at / so a single `uvicorn` process is all you need.

Scheduler (Phase 2.1):
  09:00 IST  morning_setup   — Yahoo EOD → CPR levels
  09:16 IST  market_scan     — live quotes → signals → auto paper-trade
  60s        monitor_tick    — check SL/target hits (market hours only)
  15:30 IST  daily_summary   — portfolio stats + alert

Env vars:
  SCHEDULER_ENABLED=false   — disable the scheduler (API stays up)
  ALERT_CHANNEL=whatsapp    — "console" (default) or "whatsapp"

Run:
    uvicorn backend.api_server:app --reload --port 8000
or:
    python -m backend.api_server          # convenience entry point
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure backend/ is importable when uvicorn imports this as
# `backend.api_server:app` (in which case the parent dir, not backend/,
# is on sys.path).
_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from alerts import get_alert_provider
from config_loader import Watchlist
from data_provider import Candle
from db import MarketStore
from paper_trader import PaperTrader
from scheduler_service import TradingScheduler, create_scheduler
from trading_bot import PivotBossBot

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── paths ──────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "frontend" / "dashboard.html"

# ── app + store ────────────────────────────────────────────────────────────

app = FastAPI(
    title="PivotBoss AI",
    description="CPR-based trading system API (paper trading + analytics)",
    version="0.5.0",
)

# Allow the dashboard (opened as file://) and any local dev origin to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # local-only server; tighten for production
    allow_methods=["GET"],
    allow_headers=["*"],
)

store = MarketStore()

# Bot + scheduler — initialised on startup so they share the store singleton.
bot: PivotBossBot | None = None
scheduler: TradingScheduler | None = None


@app.on_event("startup")
async def _startup() -> None:
    """Ensure the schema exists, then boot the bot + scheduler."""
    await store.init()
    logger.info("API store ready at %s", store.db_path)

    global bot, scheduler
    bot = PivotBossBot()
    scheduler = create_scheduler(bot=bot, store=store)
    scheduler.start()

    # Expose on app.state so future endpoints / middleware can reach them.
    app.state.bot = bot
    app.state.scheduler = scheduler


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Gracefully stop the scheduler on process exit."""
    if scheduler is not None:
        scheduler.stop()
        logger.info("Scheduler shut down.")


# ── helpers ────────────────────────────────────────────────────────────────

def _run(coro):
    """Await a coroutine from a sync endpoint handler.

    FastAPI runs sync `def` endpoints in a threadpool with their own event
    loop, so asyncio.run() is safe here. (Async `def` endpoints would await
    directly — kept sync here for simplicity and to match the rest of the app.)
    """
    try:
        asyncio.get_running_loop()
        # We're inside an existing loop (shouldn't happen for sync def, but
        # be defensive): create a task on it.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    except RuntimeError:
        return asyncio.run(coro)


def _latest_signal_date(symbol: str) -> Optional[str]:
    """Return the most recent signal_date for a symbol, or None."""
    async def _q():
        async with store._connect() as db:
            cur = await db.execute(
                "SELECT MAX(signal_date) FROM signals WHERE symbol = ?",
                (symbol,),
            )
            (d,) = await cur.fetchone()
        return d
    return _run(_q())


def _latest_cpr_date(symbol: str) -> Optional[str]:
    async def _q():
        async with store._connect() as db:
            cur = await db.execute(
                "SELECT MAX(cpr_date) FROM cpr_levels WHERE symbol = ?",
                (symbol,),
            )
            (d,) = await cur.fetchone()
        return d
    return _run(_q())


# ── API endpoints ──────────────────────────────────────────────────────────

@app.get("/api/status")
def status() -> Dict[str, Any]:
    """Health check + DB row counts (handy for the dashboard header)."""
    async def _q():
        async with store._connect() as db:
            counts: Dict[str, int] = {}
            for tbl in ("candles", "cpr_levels", "signals",
                        "paper_trades", "equity_curve"):
                cur = await db.execute(f"SELECT COUNT(*) FROM {tbl}")
                (n,) = await cur.fetchone()
                counts[tbl] = n
            return counts
    counts = _run(_q())
    return {
        "status": "ok",
        "db_path": str(store.db_path),
        "counts": counts,
        "watchlist": Watchlist().names(),
    }


@app.get("/api/portfolio")
def portfolio() -> Dict[str, Any]:
    """Portfolio config + computed stats. Rebuilds the trader from the DB."""
    trader = _run(PaperTrader.load_from_store(store))
    stats = trader.get_stats()
    return {
        "starting_capital": trader.portfolio.starting_capital,
        "current_capital": trader.portfolio.current_capital,
        "max_risk_per_trade_pct": trader.portfolio.max_risk_per_trade_pct,
        "max_open_positions": trader.portfolio.max_open_positions,
        "stats": stats,
    }


@app.get("/api/trades")
def trades(status_filter: Optional[str] = Query(None, alias="status")) -> List[Dict[str, Any]]:
    """All paper trades (newest first). ?status=OPEN to filter."""
    if status_filter and status_filter.upper() == "OPEN":
        rows = _run(store.get_open_trades())
    else:
        rows = _run(store.get_all_trades())
    # newest first
    return [t.to_dict() for t in reversed(rows)]


@app.get("/api/signals")
def signals() -> List[Dict[str, Any]]:
    """Latest stored signal for each watchlist symbol."""
    wl = Watchlist().names()
    out: List[Dict[str, Any]] = []
    for sym in wl:
        d = _latest_signal_date(sym)
        if not d:
            continue
        sig = _run(store.get_signal(sym, d))
        if sig:
            sig["symbol"] = sym
            out.append(sig)
    return out


@app.get("/api/signals/{symbol}")
def signals_for(symbol: str) -> List[Dict[str, Any]]:
    """All stored signals for a symbol (history, oldest first)."""
    symbol = symbol.upper()
    async def _q():
        async with store._connect() as db:
            cur = await db.execute(
                """SELECT symbol, signal_date AS date, signal, strength,
                          entry_price, stop_loss, target1, target2, target3,
                          risk_reward, reason
                   FROM signals WHERE symbol = ? ORDER BY signal_date ASC""",
                (symbol,),
            )
            rows = await cur.fetchall()
        import json as _json
        out = []
        for r in rows:
            d = dict(r)
            d["reason"] = _json.loads(d["reason"]) if d.get("reason") else []
            out.append(d)
        return out
    return _run(_q())


@app.get("/api/cpr/{symbol}")
def cpr_latest(symbol: str):
    """Latest CPR levels for a symbol. 404 if none stored."""
    symbol = symbol.upper()
    d = _latest_cpr_date(symbol)
    if not d:
        raise HTTPException(404, f"No CPR data for {symbol}")
    cpr = _run(store.get_cpr(symbol, d))
    return cpr.to_dict() if cpr else {}


@app.get("/api/cpr/{symbol}/{cpr_date}")
def cpr_on(symbol: str, cpr_date: str):
    """CPR levels for a specific date."""
    cpr = _run(store.get_cpr(symbol.upper(), cpr_date))
    if not cpr:
        raise HTTPException(404, f"No CPR for {symbol} on {cpr_date}")
    return cpr.to_dict()


@app.get("/api/watchlist")
def watchlist() -> List[Dict[str, Any]]:
    """Watchlist symbols with their metadata (segment, lot size, ticker)."""
    wl = Watchlist()
    out = []
    for name in wl.names():
        m = wl.get(name)
        if m:
            out.append({
                "name": m.name,
                "segment": m.segment,
                "yahoo_ticker": m.yahoo_ticker,
                "lot_size": m.lot_size,
                "kotak_exchange": m.kotak_exchange,
                "kotak_token": m.kotak_token,
            })
    return out


@app.get("/api/candles/{symbol}")
def candles(
    symbol: str,
    days: Optional[int] = Query(None, ge=1, le=3650),
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Cached OHLCV for a symbol."""
    symbol = symbol.upper()
    if days and not start:
        start = str(date.today() - timedelta(days=days))
    rows = _run(store.get_candles(symbol, start=start, end=end))
    return [c.to_dict() for c in rows]


@app.get("/api/equity")
def equity() -> List[Dict[str, Any]]:
    """Equity-curve snapshots (oldest first)."""
    return _run(store.get_equity_curve())


# ── scheduler status ────────────────────────────────────────────────────────

@app.get("/api/scheduler")
def scheduler_status() -> Dict[str, Any]:
    """Scheduler state: enabled/running, job list, next run times, last results."""
    if scheduler is None:
        return {"enabled": False, "running": False, "jobs": []}
    return scheduler.get_status()


# ── dashboard + static ─────────────────────────────────────────────────────

@app.get("/")
def dashboard():
    """Serve the dashboard so a single uvicorn process is all you need."""
    if not _DASHBOARD.exists():
        raise HTTPException(404, "dashboard.html not found")
    return FileResponse(_DASHBOARD)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
