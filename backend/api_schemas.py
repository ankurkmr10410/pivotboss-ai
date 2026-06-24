from __future__ import annotations

from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorResponse(BaseModel):
    error: str
    detail: str


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    has_next: bool


class StatusCounts(BaseModel):
    candles: int
    cpr_levels: int
    signals: int
    paper_trades: int
    equity_curve: int


class StatusResponse(BaseModel):
    status: str
    db_path: str
    counts: StatusCounts
    watchlist: List[str]


class PortfolioStats(BaseModel):
    total_trades: int
    open_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    best_trade: float
    worst_trade: float
    profit_factor: float
    max_drawdown: float
    capital_change_pct: float


class PortfolioResponse(BaseModel):
    starting_capital: float
    current_capital: float
    max_risk_per_trade_pct: float
    max_open_positions: int
    stats: PortfolioStats


class WatchlistItem(BaseModel):
    name: str
    segment: str
    yahoo_ticker: str
    lot_size: int
    kotak_exchange: str
    kotak_token: str
