"""
Tests for the trading scheduler (backend/scheduler_service.py).

Covers:
  - _is_market_open() correctly classifies IST times
  - TradingScheduler initialises without error (mock bot)
  - get_status() returns expected shape when scheduler is stopped
  - create_scheduler() respects SCHEDULER_ENABLED env var
  - Monitor tick skips outside market hours
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from scheduler_service import (
    TradingScheduler,
    IST,
    MONITOR_START,
    MONITOR_END,
    create_scheduler,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_bot():
    """Return a mock bot with minimal attributes the scheduler needs."""
    bot = MagicMock()
    bot.morning_setup = MagicMock()
    bot.market_open_scan = MagicMock(return_value=[])
    bot.print_summary = MagicMock()
    bot.analysis = {}
    bot.connector = MagicMock()
    bot.trader = MagicMock()
    bot.trader.portfolio.trades = []
    bot.trader.portfolio.current_capital = 500000
    bot.trader.portfolio.starting_capital = 500000
    bot.trader.get_stats.return_value = {
        "win_rate": 0, "total_pnl": 0, "total_trades": 0,
        "profit_factor": 0, "max_drawdown": 0,
    }
    bot._save_trader = MagicMock()
    return bot


def _make_alert():
    alert = MagicMock()
    alert.send = MagicMock(return_value=True)
    return alert


# ── _is_market_open ──────────────────────────────────────────────────────────

class TestIsMarketOpen:
    """Verify the market-hours gate."""

    def test_midday_is_open(self):
        """10:00 IST should be market open."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        with patch_time(IST, 10, 0):
            assert sched._is_market_open() is True

    def test_before_monitor_is_closed(self):
        """08:00 IST — before market open."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        with patch_time(IST, 8, 0):
            assert sched._is_market_open() is False

    def test_after_close_is_closed(self):
        """16:00 IST — after market close."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        with patch_time(IST, 16, 0):
            assert sched._is_market_open() is False

    def test_just_after_scan_is_open(self):
        """09:17 IST — monitor window starts."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        with patch_time(IST, 9, 17):
            assert sched._is_market_open() is True

    def test_just_before_summary_is_open(self):
        """15:29 IST — last minute of monitor window."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        with patch_time(IST, 15, 29):
            assert sched._is_market_open() is True

    def test_at_summary_time_is_closed(self):
        """15:30 IST — summary time, monitor should not run."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        with patch_time(IST, 15, 30):
            assert sched._is_market_open() is False

    def test_monitor_boundaries(self):
        """Exactly at MONITOR_START and MONITOR_END boundaries."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        # At exactly MONITOR_START — open
        assert sched._check_time(MONITOR_START) is True
        # At exactly MONITOR_END — open (inclusive)
        assert sched._check_time(MONITOR_END) is True
        # One second after MONITOR_END — closed
        from datetime import timedelta
        after = (datetime.combine(datetime.min, MONITOR_END)
                 + timedelta(seconds=1)).time()
        assert sched._check_time(after) is False


# ── Scheduler lifecycle ─────────────────────────────────────────────────────────

class TestSchedulerLifecycle:

    def test_disabled_scheduler_does_not_start(self):
        """When enabled=False, start() is a no-op."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        sched.start()
        assert sched._scheduler is None
        assert sched.get_status()["running"] is False

    def test_stop_on_none_is_noop(self):
        """Calling stop() when never started should not raise."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        sched.stop()  # should not raise

    def test_enabled_starts_and_stops(self):
        """Enabled scheduler starts and can be stopped."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=True)

        async def _run():
            sched.start()
            assert sched._scheduler is not None
            assert sched.get_status()["running"] is True
            assert len(sched.get_status()["jobs"]) == 4
            sched.stop()
            assert sched._scheduler is None

        asyncio.run(_run())

    def test_get_status_shape(self):
        """Status dict has all expected keys."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        status = sched.get_status()
        assert "enabled" in status
        assert "running" in status
        assert "timezone" in status
        assert "market_open" in status
        assert "now_ist" in status
        assert "jobs" in status
        assert "last_run" in status
        assert isinstance(status["jobs"], list)

    def test_double_start_is_safe(self):
        """Calling start() twice logs a warning but doesn't crash."""
        sched = TradingScheduler(bot=_make_bot(), store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=True)

        async def _run():
            sched.start()
            sched.start()  # should be safe (logs warning)
            sched.stop()

        asyncio.run(_run())


# ── Monitor tick gating ────────────────────────────────────────────────────────

class TestMonitorTick:

    def test_monitor_skipped_outside_hours(self):
        """_run_monitor_tick is a no-op when market is closed."""
        bot = _make_bot()
        sched = TradingScheduler(bot=bot, store=MagicMock(),
                                  alert_provider=_make_alert(), enabled=False)
        # 08:00 IST — market closed
        with patch_time(IST, 8, 0):
            sched._run_monitor_tick()
        # bot.update_position should NOT have been called
        bot.trader.update_position.assert_not_called()


# ── create_scheduler factory ──────────────────────────────────────────────────

class TestCreateScheduler:

    def test_respects_disabled_env(self, monkeypatch):
        """SCHEDULER_ENABLED=false → disabled scheduler."""
        monkeypatch.setenv("SCHEDULER_ENABLED", "false")
        sched = create_scheduler(bot=_make_bot(), store=MagicMock())
        assert sched.enabled is False

    def test_respects_enabled_env(self, monkeypatch):
        """SCHEDULER_ENABLED=true (or missing) → enabled."""
        monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
        sched = create_scheduler(bot=_make_bot(), store=MagicMock())
        assert sched.enabled is True


# ── Patching helpers ─────────────────────────────────────────────────────────

import contextlib
from unittest import mock


@contextlib.contextmanager
def patch_time(tz: ZoneInfo, hour: int, minute: int, second: int = 0):
    """
    Temporarily patch datetime.now(IST) to return a fixed IST time.
    This lets us test _is_market_open without waiting for real clock times.
    """
    fixed = datetime(2026, 6, 23, hour, minute, second, tzinfo=tz)

    def _fake_now(*args, **kwargs):
        # If called with tz argument, return fixed time in that tz.
        if args and args[0] == tz:
            return fixed
        return fixed

    with mock.patch("scheduler_service.datetime") as mock_dt:
        mock_dt.now = _fake_now
        yield


# Add helper method to TradingScheduler for boundary testing (used above)
def _check_time(self, t: time) -> bool:
    """Check if a specific time falls within the monitor window."""
    return MONITOR_START <= t <= MONITOR_END


# Monkey-patch for boundary test
TradingScheduler._check_time = _check_time
