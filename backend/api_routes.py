from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api_schemas import PaginatedResponse, PortfolioResponse, StatusResponse, WatchlistItem
from auth import get_api_key
from config_loader import Watchlist
from db import MarketStore
from paper_trader import PaperTrader
from trading_bot import PivotBossBot

router = APIRouter(prefix="/api/v1", tags=["api"])


def _get_protected_dependency(api_key: str = Depends(get_api_key)) -> str:
    return api_key


def get_store() -> MarketStore:
    from api_server import store

    return store


def get_bot() -> PivotBossBot:
    from api_server import bot

    if bot is None:
        raise HTTPException(status_code=503, detail="Bot not initialised")
    return bot


def _latest_signal_date(store: MarketStore, symbol: str) -> Optional[str]:
    async def _q():
        async with store._connect() as db:
            cur = await db.execute(
                "SELECT MAX(signal_date) FROM signals WHERE symbol = ?",
                (symbol,),
            )
            (d,) = await cur.fetchone()
            return d

    return _run(_q())


def _run(coro):
    import asyncio

    try:
        asyncio.get_running_loop()
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    except RuntimeError:
        return asyncio.run(coro)


@router.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok"}


@router.get("/ready")
def ready(store: MarketStore = Depends(get_store)) -> Dict[str, Any]:
    async def _q():
        async with store._connect() as db:
            cur = await db.execute("SELECT 1")
            await cur.fetchone()
            return True

    try:
        _run(_q())
        return {"status": "ready"}
    except Exception as exc:  # pragma: no cover - defensive path
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/status", response_model=StatusResponse)
def status_v1(
    store: MarketStore = Depends(get_store),
    _api_key: str = Depends(_get_protected_dependency),
) -> Dict[str, Any]:
    async def _q():
        async with store._connect() as db:
            counts: Dict[str, int] = {}
            for tbl in ("candles", "cpr_levels", "signals", "paper_trades", "equity_curve"):
                cur = await db.execute(f"SELECT COUNT(*) FROM {tbl}")
                (n,) = await cur.fetchone()
                counts[tbl] = n
            return counts

    counts = _run(_q())
    return {
        "status": "ok",
        "db_path": str(store.db_path),
        "counts": {
            "candles": counts.get("candles", 0),
            "cpr_levels": counts.get("cpr_levels", 0),
            "signals": counts.get("signals", 0),
            "paper_trades": counts.get("paper_trades", 0),
            "equity_curve": counts.get("equity_curve", 0),
        },
        "watchlist": Watchlist().names(),
    }


@router.get("/portfolio", response_model=PortfolioResponse)
def portfolio_v1(
    store: MarketStore = Depends(get_store),
    _api_key: str = Depends(_get_protected_dependency),
) -> Dict[str, Any]:
    trader = _run(PaperTrader.load_from_store(store))
    return {
        "starting_capital": trader.portfolio.starting_capital,
        "current_capital": trader.portfolio.current_capital,
        "max_risk_per_trade_pct": trader.portfolio.max_risk_per_trade_pct,
        "max_open_positions": trader.portfolio.max_open_positions,
        "stats": trader.get_stats(),
    }


@router.get("/watchlist", response_model=List[WatchlistItem])
def watchlist_v1(
    _api_key: str = Depends(_get_protected_dependency),
) -> List[Dict[str, Any]]:
    wl = Watchlist()
    out = []
    for name in wl.names():
        meta = wl.get(name)
        if meta:
            out.append(
                {
                    "name": meta.name,
                    "segment": meta.segment,
                    "yahoo_ticker": meta.yahoo_ticker,
                    "lot_size": meta.lot_size,
                    "kotak_exchange": meta.kotak_exchange,
                    "kotak_token": meta.kotak_token,
                }
            )
    return out


@router.get("/trades", response_model=PaginatedResponse[dict])
def trades_v1(
    store: MarketStore = Depends(get_store),
    _api_key: str = Depends(_get_protected_dependency),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None),
) -> Dict[str, Any]:
    try:
        if status_filter and status_filter.upper() == "OPEN":
            rows = _run(store.get_open_trades())
        else:
            rows = _run(store.get_all_trades())
    except Exception as exc:  # pragma: no cover - defensive path
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    ordered = [t.to_dict() for t in reversed(rows)]
    start = (page - 1) * page_size
    end = start + page_size
    page_items = ordered[start:end]
    return {
        "items": page_items,
        "total": len(ordered),
        "page": page,
        "page_size": page_size,
        "has_next": end < len(ordered),
    }


@router.get("/signals", response_model=PaginatedResponse[dict])
def signals_v1(
    store: MarketStore = Depends(get_store),
    _api_key: str = Depends(_get_protected_dependency),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    wl = Watchlist().names()
    out: List[Dict[str, Any]] = []
    for sym in wl:
        d = _latest_signal_date(store, sym)
        if not d:
            continue
        sig = _run(store.get_signal(sym, d))
        if sig:
            sig["symbol"] = sym
            out.append(sig)

    start = (page - 1) * page_size
    end = start + page_size
    page_items = out[start:end]
    return {
        "items": page_items,
        "total": len(out),
        "page": page,
        "page_size": page_size,
        "has_next": end < len(out),
    }


@router.get("/ltp/{symbol}")
def ltp(
    symbol: str,
    bot: PivotBossBot = Depends(get_bot),
    _api_key: str = Depends(_get_protected_dependency),
) -> Dict[str, Any]:
    sym = symbol.strip().upper()
    quote = bot.connector.get_quotes(sym)
    if not quote:
        raise HTTPException(status_code=404, detail=f"No quote for {sym} (connector may be offline)")
    return {
        "symbol": sym,
        "ltp": quote.get("ltp"),
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
        "volume": quote.get("volume"),
        "source": "live" if not bot.mock else "mock",
        "timestamp": quote.get("timestamp") or datetime.now().isoformat(),
    }
