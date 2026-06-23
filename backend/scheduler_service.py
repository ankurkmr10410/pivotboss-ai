"""
Trading scheduler — APScheduler integration for the daily market cycle.

Bridges the (synchronous) PivotBossBot with APScheduler so a single
uvicorn process serves the API and runs the full trading day unattended:

  09:00 IST  morning_setup   — Yahoo EOD → CPR levels for all symbols
  09:16 IST  market_scan     — live quotes → signals → auto paper-trade
  60s interval (09:17–15:29)  monitor_tick  — check SL/target hits on open trades
  15:30 IST  daily_summary   — portfolio stats + alert

Usage:
  The TradingScheduler is created and started by the FastAPI server's
  on_startup event (backend/api_server.py).  It can also be used
  standalone for testing:

      bot = PivotBossBot()
      sched = TradingScheduler(bot, store, alerter)
      sched.start()
      # ... runs forever

Env vars:
  SCHEDULER_ENABLED   — "false" disables the scheduler (API stays up). Default: "true"
  ALERT_CHANNEL       — "console" (default) or "whatsapp"
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 0)        # 09:00 IST
SCAN_TIME = time(9, 16)         # 09:16 IST  (first 16 min of NSE session)
MARKET_CLOSE = time(15, 30)     # 15:30 IST
MONITOR_START = time(9, 17)    # start monitoring after scan
MONITOR_END = time(15, 29)      # stop before close summary
MONITOR_INTERVAL_SECONDS = 60  # poll every 60 s


class TradingScheduler:
    """
    APScheduler wrapper that drives the PivotBossBot through the daily
    market cycle.  Designed to run inside the FastAPI event loop.
    """

    def __init__(
        self,
        bot: Any,
        store: Any,
        alert_provider: Any,
        enabled: bool = True,
    ) -> None:
        """
        Parameters
        ----------
        bot : PivotBossBot
            The sync trading bot whose methods we call on schedule.
        store : MarketStore
            Shared async SQLite store (used by the bot internally; kept here
            so the /api/scheduler endpoint can report DB health).
        alert_provider : AlertProvider
            Sends notifications for signals, fills, daily summaries.
        enabled : bool
            When False the scheduler registers but skips all job execution.
            Useful for running the API without the daily cycle.
        """
        self.bot = bot
        self.store = store
        self.alerts = alert_provider
        self.enabled = enabled
        self._scheduler: Optional[AsyncIOScheduler] = None

        # Track last-run results for the status endpoint.
        self._last_run: Dict[str, Dict[str, Any]] = {
            "morning_setup": {},
            "market_scan": {},
            "monitor_tick": {},
            "daily_summary": {},
        }

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Create and start the APScheduler AsyncIOScheduler."""
        if self._scheduler is not None:
            logger.warning("Scheduler already running — skipping start().")
            return

        if not self.enabled:
            logger.info("Scheduler is DISABLED (SCHEDULER_ENABLED=false). "
                        "API endpoints will work but no automated jobs will run.")
            return

        self._scheduler = AsyncIOScheduler(timezone=IST, logger=logger)

        # --- Cron jobs at fixed IST times ---
        self._scheduler.add_job(
            self._run_morning_setup,
            CronTrigger(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
                        timezone=IST),
            id="morning_setup",
            name="Morning CPR Setup (09:00 IST)",
            replace_existing=True,
        )

        self._scheduler.add_job(
            self._run_market_scan,
            CronTrigger(hour=SCAN_TIME.hour, minute=SCAN_TIME.minute,
                        timezone=IST),
            id="market_scan",
            name="Market Open Scan (09:16 IST)",
            replace_existing=True,
        )

        self._scheduler.add_job(
            self._run_daily_summary,
            CronTrigger(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute,
                        timezone=IST),
            id="daily_summary",
            name="Daily Summary (15:30 IST)",
            replace_existing=True,
        )

        # --- Interval job: monitor during market hours ---
        self._scheduler.add_job(
            self._run_monitor_tick,
            IntervalTrigger(seconds=MONITOR_INTERVAL_SECONDS),
            id="monitor_tick",
            name="Position Monitor (every 60s, market hours)",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info("Scheduler started — jobs registered:")
        for job in self._scheduler.get_jobs():
            logger.info("  %s  [%s] next_run=%s", job.id, job.name,
                        job.next_run_time)

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler is None:
            return
        try:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")
        except Exception as e:
            logger.error("Error stopping scheduler: %s", e)
        finally:
            self._scheduler = None

    # ── Scheduled handlers ─────────────────────────────────────────────────────
    # Each wraps a bot method, catches exceptions, and updates _last_run.

    def _run_morning_setup(self) -> None:
        """09:00 IST — fetch Yahoo EOD, compute CPR for all symbols."""
        logger.info("[scheduler] Running morning_setup…")
        try:
            self.bot.morning_setup()
            self._last_run["morning_setup"] = {
                "status": "ok",
                "at": datetime.now(IST).isoformat(),
            }
            self.alerts.send(
                f"✅ Morning setup complete — CPR levels calculated for "
                f"{len(self.bot.analysis)} symbols."
            )
        except Exception as e:
            logger.error("[scheduler] morning_setup failed: %s", e, exc_info=True)
            self._last_run["morning_setup"] = {
                "status": "error",
                "error": str(e),
                "at": datetime.now(IST).isoformat(),
            }
            self.alerts.send(f"❌ Morning setup FAILED: {e}")

    def _run_market_scan(self) -> None:
        """09:16 IST — live quotes → signals → auto paper-trade."""
        logger.info("[scheduler] Running market_scan…")
        try:
            signals = self.bot.market_open_scan()

            # Build alert message for strong signals.
            strong = [s for s in (signals or []) if getattr(s, "strength", 0) >= 7]
            msg_parts = [
                f"📊 Market scan complete — {len(signals or [])} signals generated."
            ]
            for s in strong:
                emoji = "🟢" if "BUY" in s.signal else "🔴"
                msg_parts.append(
                    f"  {emoji} {s.symbol} {s.signal} (strength {s.strength}) "
                    f"@ ₹{s.entry_price}"
                )
            self.alerts.send("\n".join(msg_parts))

            self._last_run["market_scan"] = {
                "status": "ok",
                "signals": len(signals or []),
                "strong": len(strong),
                "at": datetime.now(IST).isoformat(),
            }
        except Exception as e:
            logger.error("[scheduler] market_scan failed: %s", e, exc_info=True)
            self._last_run["market_scan"] = {
                "status": "error",
                "error": str(e),
                "at": datetime.now(IST).isoformat(),
            }
            self.alerts.send(f"❌ Market scan FAILED: {e}")

    def _run_monitor_tick(self) -> None:
        """
        Every 60s during market hours — check open trades for SL/target hits.
        Skipped outside market hours.
        """
        if not self._is_market_open():
            return

        try:
            open_before = self._count_open_trades()
            self._monitor_once()
            open_after = self._count_open_trades()
            closed = open_before - open_after

            if closed > 0:
                self._last_run["monitor_tick"] = {
                    "status": "ok",
                    "trades_closed": closed,
                    "at": datetime.now(IST).isoformat(),
                }
                stats = self.bot.trader.get_stats()
                self.alerts.send(
                    f"📋 Monitor: {closed} trade(s) closed | "
                    f"Capital: ₹{self.bot.trader.portfolio.current_capital:,.0f} | "
                    f"Win Rate: {stats['win_rate']}% | "
                    f"Total P&L: ₹{stats['total_pnl']:+,.0f}"
                )
        except Exception as e:
            logger.error("[scheduler] monitor_tick failed: %s", e, exc_info=True)

    def _run_daily_summary(self) -> None:
        """15:30 IST — print + alert portfolio summary."""
        logger.info("[scheduler] Running daily_summary…")
        try:
            self.bot.print_summary()
            stats = self.bot.trader.get_stats()
            cap = self.bot.trader.portfolio.current_capital
            cap_start = self.bot.trader.portfolio.starting_capital
            pct = (cap - cap_start) / cap_start * 100 if cap_start else 0

            self.alerts.send(
                f"📈 End-of-day Summary\n"
                f"  Capital: ₹{cap:,.0f} ({pct:+.2f}%)\n"
                f"  Total Trades: {stats['total_trades']}\n"
                f"  Win Rate: {stats['win_rate']}%\n"
                f"  Profit Factor: {stats['profit_factor']}\n"
                f"  Max Drawdown: {stats['max_drawdown']}%\n"
                f"  Total P&L: ₹{stats['total_pnl']:+,.0f}"
            )
            self._last_run["daily_summary"] = {
                "status": "ok",
                "stats": stats,
                "at": datetime.now(IST).isoformat(),
            }
        except Exception as e:
            logger.error("[scheduler] daily_summary failed: %s", e, exc_info=True)
            self._last_run["daily_summary"] = {
                "status": "error",
                "error": str(e),
                "at": datetime.now(IST).isoformat(),
            }
            self.alerts.send(f"❌ Daily summary FAILED: {e}")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _is_market_open(self) -> bool:
        """Return True if the current IST time is within market hours (09:17–15:29)."""
        now_ist = datetime.now(IST).time()
        return MONITOR_START <= now_ist <= MONITOR_END

    def _count_open_trades(self) -> int:
        """Count currently open paper trades."""
        from paper_trader import TradeStatus
        return sum(
            1 for t in self.bot.trader.portfolio.trades
            if t.status == TradeStatus.OPEN.value
        )

    def _monitor_once(self) -> None:
        """
        Perform a single monitor pass: check each open trade against live
        LTP, close if SL/target hit.  Mirrors the logic in
        PivotBossBot.monitor_positions() but runs exactly once (no loop).
        """
        from paper_trader import TradeStatus

        open_trades = [
            t for t in self.bot.trader.portfolio.trades
            if t.status == TradeStatus.OPEN.value
        ]

        if not open_trades:
            return

        for trade in open_trades:
            quote = self.bot.connector.get_quotes(trade.symbol)
            if not quote:
                continue

            ltp = quote["ltp"]
            closed = self.bot.trader.update_position(trade.symbol, ltp)
            if closed:
                pnl_emoji = "✅" if closed.pnl > 0 else "❌"
                logger.info(
                    "[monitor] %s %s closed | %s | Exit: ₹%s | P&L: ₹%+.2f",
                    pnl_emoji, trade.symbol, closed.status,
                    closed.exit_price, closed.pnl,
                )
                self.bot._save_trader()

    # ── Status (for the /api/scheduler endpoint) ───────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Return scheduler state for the dashboard API."""
        jobs: List[Dict[str, Any]] = []
        if self._scheduler is not None:
            for job in self._scheduler.get_jobs():
                jobs.append({
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat()
                    if job.next_run_time else None,
                    "trigger": str(job.trigger),
                })

        return {
            "enabled": self.enabled,
            "running": self._scheduler is not None and self._scheduler.running,
            "timezone": str(IST),
            "market_open": self._is_market_open(),
            "now_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "jobs": jobs,
            "last_run": self._last_run,
        }


# ── Factory ─────────────────────────────────────────────────────────────────────

def create_scheduler(
    bot: Any,
    store: Any,
    alert_channel: Optional[str] = None,
) -> TradingScheduler:
    """
    Build a TradingScheduler with config from the environment.

    Parameters
    ----------
    bot : PivotBossBot
    store : MarketStore
    alert_channel : str or None
        Override for ALERT_CHANNEL env var.  "console" or "whatsapp".
    """
    from alerts import get_alert_provider

    channel = alert_channel or os.getenv("ALERT_CHANNEL", "console")
    enabled = os.getenv("SCHEDULER_ENABLED", "true").strip().lower() != "false"
    provider = get_alert_provider(channel)

    return TradingScheduler(
        bot=bot,
        store=store,
        alert_provider=provider,
        enabled=enabled,
    )
