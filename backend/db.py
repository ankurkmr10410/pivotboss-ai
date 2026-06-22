"""
SQLite persistence layer (async via aiosqlite).

Replaces the previous JSON-file persistence (paper_trades.json,
daily_analysis.json, nifty_2y_cache.json) with a single database.

Schema (see `_CREATE_SQL`):
  - candles           : daily OHLCV (Yahoo / broker)
  - cpr_levels        : computed CPR per (symbol, date)
  - signals           : generated trade signals per (symbol, date)
  - paper_trades      : paper-trading journal
  - portfolio_config  : singleton-ish key/value for portfolio scalars
  - equity_curve      : running equity snapshots (new — was not in JSON)

The store is async. Sync callers (e.g. the trading bot entry point) bridge
into it via `asyncio.run(...)`. `db_path` is constructor-injected so tests
can point at a tmp file (same DI style as YahooProvider(fetcher=...)).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

import aiosqlite

from cpr_engine import CPRLevels, TradeSignal
from data_provider import Candle
from paper_trader import PaperTrader, Trade, PaperPortfolio

logger = logging.getLogger(__name__)


# Default DB location — overridable via PIVOTBOSS_DB_PATH env var.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pivotboss.db"


def default_db_path() -> Path:
    """Resolve the DB path, honouring PIVOTBOSS_DB_PATH if set."""
    env = os.getenv("PIVOTBOSS_DB_PATH")
    return Path(env) if env else _DEFAULT_DB_PATH


# ── Schema ──────────────────────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume INTEGER,
    source TEXT,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS cpr_levels (
    symbol          TEXT NOT NULL,
    cpr_date        TEXT NOT NULL,
    pivot           REAL,
    tc              REAL,
    bc              REAL,
    cpr_width       REAL,
    cpr_width_pct   REAL,
    s1              REAL,
    s2              REAL,
    s3              REAL,
    r1              REAL,
    r2              REAL,
    r3              REAL,
    camarilla_s1    REAL,
    camarilla_s2    REAL,
    camarilla_r1    REAL,
    camarilla_r2    REAL,
    cpr_type        TEXT,
    is_virgin       INTEGER,
    prev_day_high   REAL,
    prev_day_low    REAL,
    prev_day_close  REAL,
    PRIMARY KEY (symbol, cpr_date)
);

CREATE TABLE IF NOT EXISTS signals (
    symbol        TEXT NOT NULL,
    signal_date   TEXT NOT NULL,
    signal        TEXT,
    strength      INTEGER,
    entry_price   REAL,
    stop_loss     REAL,
    target1       REAL,
    target2       REAL,
    target3       REAL,
    risk_reward   REAL,
    reason        TEXT,
    PRIMARY KEY (symbol, signal_date),
    FOREIGN KEY (symbol, signal_date)
        REFERENCES cpr_levels (symbol, cpr_date)
        ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id              TEXT PRIMARY KEY,
    symbol          TEXT,
    date            TEXT,
    time            TEXT,
    direction       TEXT,
    entry_price     REAL,
    stop_loss       REAL,
    target1         REAL,
    target2         REAL,
    target3         REAL,
    quantity        INTEGER,
    signal_type     TEXT,
    signal_strength INTEGER,
    cpr_type        TEXT,
    is_virgin_cpr   INTEGER,
    status          TEXT,
    exit_price      REAL,
    exit_time       TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_config (
    key   TEXT PRIMARY KEY,
    value REAL
);

CREATE TABLE IF NOT EXISTS equity_curve (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT,
    capital        REAL,
    open_positions INTEGER
);
"""


class MarketStore:
    """Async SQLite store for all persisted market / portfolio state."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path: Path = Path(db_path) if db_path else default_db_path()

    # ── lifecycle ────────────────────────────────────────────────────────

    async def init(self) -> None:
        """Create the DB file (if missing) and all tables. Idempotent."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_CREATE_SQL)
            await db.commit()
        logger.info("MarketStore ready at %s", self.db_path)

    @asynccontextmanager
    async def _connect(self):
        """Async context manager: yields a connection with FK enforcement on.

        Usage:  `async with self._connect() as db: ...`
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    # ── candles ──────────────────────────────────────────────────────────

    async def upsert_candle(self, symbol: str, candle: Candle, source: str = "yahoo") -> None:
        """Upsert a single candle. Delegates to the bulk method."""
        await self.upsert_symbol_candles(symbol, [candle], source=source)

    async def upsert_symbol_candles(
        self, symbol: str, candles: List[Candle], source: str = "yahoo"
    ) -> int:
        """Bulk upsert candles for a single symbol. Returns rows written."""
        if not candles:
            return 0
        rows = [
            (symbol, c.date, c.open, c.high, c.low, c.close, c.volume, source)
            for c in candles
        ]
        async with self._connect() as db:
            await db.executemany(
                """
                INSERT INTO candles
                    (symbol, date, open, high, low, close, volume, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume, source=excluded.source
                """,
                rows,
            )
            await db.commit()
        return len(rows)

    async def get_candles(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[Candle]:
        """Return candles for `symbol` (oldest first), optionally date-bounded."""
        sql = "SELECT date, open, high, low, close, volume FROM candles WHERE symbol = ?"
        params: List[Any] = [symbol]
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY date ASC"
        async with self._connect() as db:
            cur = await db.execute(sql, params)
            rows = await cur.fetchall()
        return [Candle(d, o, h, l, c, v) for (d, o, h, l, c, v) in rows]

    async def candle_count(self, symbol: str) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM candles WHERE symbol = ?", (symbol,)
            )
            (n,) = await cur.fetchone()
        return n

    # ── CPR levels ───────────────────────────────────────────────────────

    async def save_cpr(self, cpr: CPRLevels) -> None:
        d = cpr.to_dict()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO cpr_levels (
                    symbol, cpr_date, pivot, tc, bc, cpr_width, cpr_width_pct,
                    s1, s2, s3, r1, r2, r3,
                    camarilla_s1, camarilla_s2, camarilla_r1, camarilla_r2,
                    cpr_type, is_virgin, prev_day_high, prev_day_low, prev_day_close
                ) VALUES (
                    :symbol, :date, :pivot, :tc, :bc, :cpr_width, :cpr_width_pct,
                    :s1, :s2, :s3, :r1, :r2, :r3,
                    :camarilla_s1, :camarilla_s2, :camarilla_r1, :camarilla_r2,
                    :cpr_type, :is_virgin, :prev_day_high, :prev_day_low, :prev_day_close
                )
                ON CONFLICT(symbol, cpr_date) DO UPDATE SET
                    pivot=excluded.pivot, tc=excluded.tc, bc=excluded.bc,
                    cpr_width=excluded.cpr_width, cpr_width_pct=excluded.cpr_width_pct,
                    s1=excluded.s1, s2=excluded.s2, s3=excluded.s3,
                    r1=excluded.r1, r2=excluded.r2, r3=excluded.r3,
                    camarilla_s1=excluded.camarilla_s1, camarilla_s2=excluded.camarilla_s2,
                    camarilla_r1=excluded.camarilla_r1, camarilla_r2=excluded.camarilla_r2,
                    cpr_type=excluded.cpr_type, is_virgin=excluded.is_virgin,
                    prev_day_high=excluded.prev_day_high,
                    prev_day_low=excluded.prev_day_low,
                    prev_day_close=excluded.prev_day_close
                """,
                d,
            )
            await db.commit()

    async def get_cpr(self, symbol: str, cpr_date: str) -> Optional[CPRLevels]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT * FROM cpr_levels WHERE symbol = ? AND cpr_date = ?",
                (symbol, cpr_date),
            )
            row = await cur.fetchone()
        return self._row_to_cpr(row) if row else None

    # ── signals ──────────────────────────────────────────────────────────

    async def save_signal(self, sig: TradeSignal) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO signals (
                    symbol, signal_date, signal, strength,
                    entry_price, stop_loss, target1, target2, target3,
                    risk_reward, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, signal_date) DO UPDATE SET
                    signal=excluded.signal, strength=excluded.strength,
                    entry_price=excluded.entry_price, stop_loss=excluded.stop_loss,
                    target1=excluded.target1, target2=excluded.target2,
                    target3=excluded.target3, risk_reward=excluded.risk_reward,
                    reason=excluded.reason
                """,
                (
                    sig.symbol, sig.date, sig.signal, sig.strength,
                    sig.entry_price, sig.stop_loss, sig.target1, sig.target2,
                    sig.target3, sig.risk_reward, json.dumps(sig.reason),
                ),
            )
            await db.commit()

    async def get_signal(
        self, symbol: str, signal_date: str
    ) -> Optional[Dict[str, Any]]:
        """Return signal as a dict (matches the old JSON shape)."""
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT symbol, signal_date AS date, signal, strength,
                          entry_price, stop_loss, target1, target2, target3,
                          risk_reward, reason
                   FROM signals WHERE symbol = ? AND signal_date = ?""",
                (symbol, signal_date),
            )
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["reason"] = json.loads(d["reason"]) if d.get("reason") else []
        return d

    # ── composite analysis write (replaces _save_analysis) ───────────────

    async def save_analysis(
        self,
        symbol: str,
        cpr: CPRLevels,
        prev_candle: Optional[Dict[str, Any]] = None,
        signal: Optional[TradeSignal] = None,
        quote: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a symbol's daily analysis snapshot.

        prev_candle and quote are optional metadata; today we persist the CPR
        (and signal if present). Candles from prev_candle can optionally be
        cached too — kept here for forward-compat with the dashboard.
        """
        await self.save_cpr(cpr)
        if signal is not None:
            await self.save_signal(signal)
        if prev_candle:
            try:
                await self.upsert_symbol_candles(
                    symbol,
                    [Candle(
                        date=prev_candle["date"],
                        open=float(prev_candle["open"]),
                        high=float(prev_candle["high"]),
                        low=float(prev_candle["low"]),
                        close=float(prev_candle["close"]),
                        volume=int(prev_candle.get("volume", 0) or 0),
                    )],
                    source="yahoo",
                )
            except Exception as e:
                logger.warning("Could not cache prev_candle for %s: %s", symbol, e)

    # ── paper trades ─────────────────────────────────────────────────────

    async def upsert_trade(self, trade: Trade) -> None:
        d = trade.to_dict()
        d["is_virgin_cpr"] = 1 if d.get("is_virgin_cpr") else 0
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO paper_trades (
                    id, symbol, date, time, direction, entry_price, stop_loss,
                    target1, target2, target3, quantity, signal_type,
                    signal_strength, cpr_type, is_virgin_cpr, status,
                    exit_price, exit_time, pnl, pnl_pct, notes
                ) VALUES (
                    :id, :symbol, :date, :time, :direction, :entry_price, :stop_loss,
                    :target1, :target2, :target3, :quantity, :signal_type,
                    :signal_strength, :cpr_type, :is_virgin_cpr, :status,
                    :exit_price, :exit_time, :pnl, :pnl_pct, :notes
                )
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, exit_price=excluded.exit_price,
                    exit_time=excluded.exit_time, pnl=excluded.pnl,
                    pnl_pct=excluded.pnl_pct, notes=excluded.notes
                """,
                d,
            )
            await db.commit()

    async def upsert_trades(self, trades: List[Trade]) -> int:
        for t in trades:
            await self.upsert_trade(t)
        return len(trades)

    async def get_all_trades(self) -> List[Trade]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT * FROM paper_trades ORDER BY date, time"
            )
            rows = await cur.fetchall()
        return [self._row_to_trade(r) for r in rows]

    async def get_open_trades(self) -> List[Trade]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY date, time"
            )
            rows = await cur.fetchall()
        return [self._row_to_trade(r) for r in rows]

    # ── portfolio config ─────────────────────────────────────────────────

    _PORTFOLIO_KEYS = (
        "starting_capital",
        "current_capital",
        "max_risk_per_trade_pct",
        "max_open_positions",
    )

    async def save_portfolio_config(self, portfolio: PaperPortfolio) -> None:
        """Persist the four portfolio scalars (fixes the JSON lossy-reload bug)."""
        values = {
            "starting_capital": portfolio.starting_capital,
            "current_capital": portfolio.current_capital,
            "max_risk_per_trade_pct": portfolio.max_risk_per_trade_pct,
            "max_open_positions": portfolio.max_open_positions,
        }
        async with self._connect() as db:
            await db.executemany(
                """
                INSERT INTO portfolio_config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                list(values.items()),
            )
            await db.commit()

    async def load_portfolio_config(self) -> Dict[str, float]:
        async with self._connect() as db:
            cur = await db.execute("SELECT key, value FROM portfolio_config")
            rows = await cur.fetchall()
        return {k: v for (k, v) in rows}

    # ── equity curve ─────────────────────────────────────────────────────

    async def append_equity(self, capital: float, open_positions: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO equity_curve (timestamp, capital, open_positions) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), capital, open_positions),
            )
            await db.commit()

    async def get_equity_curve(self) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT timestamp, capital, open_positions FROM equity_curve ORDER BY id"
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── one-time JSON → SQLite migration ─────────────────────────────────

    async def needs_migration(self, json_path: Path) -> bool:
        """True only if JSON exists AND the trades table is empty."""
        if not Path(json_path).exists():
            return False
        async with self._connect() as db:
            cur = await db.execute("SELECT COUNT(*) FROM paper_trades")
            (n,) = await cur.fetchone()
        return n == 0

    async def migrate_from_json(self, json_path: Path) -> int:
        """Import a paper_trades.json snapshot into the DB. Returns rows imported.

        Idempotent-guarded: skips if trades already exist (call needs_migration
        first, or this method will simply overwrite by id).
        """
        json_path = Path(json_path)
        if not json_path.exists():
            logger.info("Migration: %s not found, skipping.", json_path)
            return 0

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        trades = [Trade(**t) for t in data.get("trades", [])]
        imported = await self.upsert_trades(trades)

        # Restore portfolio config too (the bit the old loader dropped).
        portfolio = PaperPortfolio(
            starting_capital=data.get("starting_capital", 500000.0),
            current_capital=data.get("current_capital", data.get("starting_capital", 500000.0)),
            max_risk_per_trade_pct=data.get("max_risk_per_trade_pct", 1.0),
            max_open_positions=data.get("max_open_positions", 3),
        )
        await self.save_portfolio_config(portfolio)

        logger.info(
            "Migration: imported %d trades + portfolio config from %s",
            imported, json_path,
        )
        return imported

    # ── row → dataclass helpers ──────────────────────────────────────────

    @staticmethod
    def _row_to_candle(row) -> Candle:
        return Candle(row["date"], row["open"], row["high"], row["low"],
                      row["close"], row["volume"] or 0)

    @staticmethod
    def _row_to_cpr(row) -> CPRLevels:
        d = dict(row)
        d["date"] = d.pop("cpr_date")
        d["is_virgin"] = bool(d["is_virgin"])
        return CPRLevels(**d)

    @staticmethod
    def _row_to_trade(row) -> Trade:
        d = dict(row)
        d["is_virgin_cpr"] = bool(d["is_virgin_cpr"])
        return Trade(**d)
