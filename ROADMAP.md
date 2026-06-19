# PivotBoss AI — Product Roadmap

> Goal: a reliable, production-grade **intraday trading system for the Indian market**
> built on the **Central Pivot Range (CPR)** technique. Paper-trade first, go live
> only after the system is proven.

This roadmap is split into:
1. **Where we are** — an honest audit of the current code.
2. **Where we're going** — the target architecture.
3. **Phased plan** — concrete, prioritized milestones with deliverables.
4. **The immediate job** — Yahoo Finance for previous-day EOD data (your current ask).
5. **Open decisions** — things to confirm before we build.

---

## 1. Where We Are Today (Audit)

### ✅ Solid foundations
| Area | Status | File |
|------|--------|------|
| CPR math + signal engine (Ochoa rules + Camarilla) | ✅ Complete, clean | `backend/cpr_engine.py` |
| Paper trader (P&L, win rate, drawdown, position sizing) | ✅ Complete | `backend/paper_trader.py` |
| Kotak connector (TOTP/OTP login, quotes, historical, WS skeleton, orders) | ✅ Working, login just hardened | `backend/kotak_connector.py` |
| Orchestration (setup → scan → monitor → summary) | ✅ Works | `backend/trading_bot.py` |
| Risk controls (1% risk, 3 positions, paper guard) | ✅ Built-in | multiple |
| Static HTML dashboard | ✅ Exists (not yet connected) | `frontend/dashboard.html` |

### ⚠️ Gaps & risks (the things a "best" app must fix)
1. **Historical data is coupled to Kotak login.** `morning_setup()` calls `connector.get_historical_ohlc()`, which only works *after* a full live login. If the broker session or rate-limit hiccups, you get **no CPR levels** for the day. EOD data should be independent and bulletproof. → **This is your Yahoo ask.**
2. **Only 6 symbols hardcoded** in `SYMBOL_TOKENS`; lot sizes are stale (NIFTY 25, BANKNIFTY 15) and NSE revises these periodically.
3. **No data-provider abstraction.** Adding Zerodha (roadmap) means rewriting call sites. Yahoo also needs a home.
4. **Monitor polls `get_quotes()`** in a loop instead of consuming the WebSocket — slow and rate-limit-prone.
5. **JSON files for persistence** (`data/*.json`) — fine for a demo, fragile for production.
6. **No scheduler.** The 9:00 / 9:16 / 3:30 cycle is manual.
7. **No REST API** — the dashboard is static and can't see live state.
8. **No backtesting** — you can't validate the strategy before risking capital.
9. **No automated tests.**
10. **No alerting** (Telegram/WhatsApp) for signals and fills.
11. **Symbol universe is index-heavy.** For futures (NIFTY/BANKNIFTY lots), CPR is usually computed on the **spot/underlying** or the **front-month future** — needs a deliberate choice.

---

## 2. Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Presentation                          │
│   frontend/dashboard.html  ←─ HTTP/SSE ─→  FastAPI server    │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────┐
│                     Orchestration / Scheduler                │
│   APScheduler: 09:00 setup · 09:16 scan · intraday monitor   │
└───────┬───────────────────────┬──────────────────────┬───────┘
        │                       │                      │
┌───────▼─────────┐   ┌─────────▼──────────┐   ┌───────▼────────┐
│  Strategy layer │   │  Execution layer   │   │ Persistence    │
│  CPR engine     │   │  Broker adapter    │   │ SQLite (async) │
│  Signal gen     │   │  (Kotak/Zerodha)   │   │ + trade journal │
└───────▲─────────┘   └─────────┬──────────┘   └────────────────┘
        │                       │
┌───────┴───────────────────────┴──────────────────────────────┐
│                Data layer (provider abstraction)              │
│  MarketDataProvider                                          │
│   ├─ YahooProvider   (EOD / previous-day OHLC — no login)    │
│   ├─ KotakProvider   (live LTP, intraday, historical, WS)    │
│   └─ ZerodhaProvider (Phase 3)                               │
└──────────────────────────────────────────────────────────────┘
```

**Guiding principles**
- **EOD vs live are different concerns.** Previous-day OHLC for CPR = EOD provider (Yahoo). Intraday LTP for signals/monitoring = live provider (Kotak). Never make CPR setup depend on a live login.
- **One interface, many providers.** Strategy and execution code talks to `MarketDataProvider`, never to a specific broker.
- **Paper mode is the default.** Live order placement is a deliberately hard switch.

---

## 3. Phased Plan

### Phase 1 — Reliable data foundation *(do this first)*
**Goal: CPR setup works every morning, broker or no broker.**

- [ ] **1.1 Data-provider abstraction.** New `backend/data_provider.py` defining a `MarketDataProvider` ABC with: `get_eod_ohlc(symbol, days)`, `get_ltp(symbol)`, `get_historical(...)`.
- [ ] **1.2 Yahoo Finance provider.** New `backend/providers/yahoo_provider.py` using `yfinance`.
  - Symbols: stocks → `RELIANCE.NS`; NIFTY 50 → `^NSEI`; BANK NIFTY → `^NSEBANK`.
  - Returns previous-day OHLC for CPR. No login, no rate-limit pain.
  - *Caveat to handle:* Yahoo doesn't reliably serve index **futures**. For NIFTY/BANKNIFTY we compute CPR on the **spot index** (standard practice) unless you explicitly want the future.
- [ ] **1.3 Refactor `morning_setup()`** to pull EOD from Yahoo; keep Kotak only for live LTP.
- [ ] **1.4 Externalize config.** Move watchlist + symbols to `config/watchlist.yaml` (symbol, exchange, yahoo ticker, kotak token, lot size, segment). Single source of truth — no more stale hardcoded lot sizes.
- [ ] **1.5 Unit tests** for the CPR engine and the Yahoo provider (offline fixtures).

**Deliverable:** `python backend/trading_bot.py --setup` produces correct CPR levels every morning using Yahoo, with zero dependency on a Kotak session.

### Phase 2 — Live operation & connectivity
**Goal: a running service, not a script you babysit.**

- [ ] **2.1 Scheduler.** Use `APScheduler` to auto-run setup (09:00), scan (09:16), monitor (market hours), summary (15:30). One long-lived process.
- [ ] **2.2 WebSocket-driven monitor.** Replace the polling `get_quotes()` loop in `monitor_positions()` with the Kotak WS feed (subscribe once, fan out to monitor + dashboard). Polling stays as fallback.
- [ ] **2.3 FastAPI server.** Endpoints: `/status`, `/signals`, `/portfolio`, `/cpr/{symbol}`. SSE or WebSocket push for live ticks.
- [ ] **2.4 Connect the dashboard.** Wire `frontend/dashboard.html` to the FastAPI server so it shows live signals, CPR levels, and P&L.
- [ ] **2.5 SQLite persistence.** Replace JSON files with a small schema: `candles`, `cpr_levels`, `signals`, `trades`, `equity_curve`. Async via `aiosqlite`.
- [ ] **2.6 Alerts.** Telegram bot for: new STRONG signals, SL/target hits, daily summary.

**Deliverable:** A process that runs itself through the trading day and a live dashboard.

### Phase 3 — Validation before real money
**Goal: prove the edge exists before risking capital.**

- [ ] **3.1 Historical data store.** Backfill 1–2 years of daily candles (Yahoo) so you can backtest.
- [ ] **3.2 Backtester.** Replay historical days through `cpr_engine` + `paper_trader`; report expectancy, win rate, max drawdown, per-CPR-type breakdown (narrow vs wide vs virgin).
- [ ] **3.3 Parameter tuning.** Tune thresholds (width %, strength gates, R:R) with discipline — train/test split, no overfitting.
- [ ] **3.4 Forward paper trading.** Run 2–3 months live-on-paper and compare to backtest expectations.

**Deliverable:** A report showing whether the strategy has a real edge, before any live order.

### Phase 4 — Go live (deliberately)
**Goal: real execution with safety rails.**

- [ ] **4.1 Live kill-switch.** Hard daily-loss limit, max trades/day, PAPER mode toggle that is fail-closed.
- [ ] **4.2 Live order path.** Flip `PAPER_TRADING_MODE`; route through broker adapter; reconcile fills from order book.
- [ ] **4.3 Broker abstraction + Zerodha.** Add `ZerodhaProvider`; everything else unchanged.
- [ ] **4.4 Observability.** Structured logs, run metrics, alerting on errors.

**Deliverable:** Small-size live trading with full safety controls.

### Phase 5 — Scale & productize (later)
- [ ] Multi-user accounts, auth (Phase 4 SaaS vision from README).
- [ ] Options strategies on CPR levels.
- [ ] Deployment (Docker + cloud / VPS in NSE region for latency).
- [ ] SEBI compliance review if commercialized.

---

## 4. The Immediate Job — Yahoo Finance EOD (Phase 1.1–1.3)

This is what we build first. Concretely:

1. **`backend/data_provider.py`** — `MarketDataProvider` ABC.
2. **`backend/providers/yahoo_provider.py`** — `yfinance`-backed EOD provider:
   ```python
   # conceptually:
   yf.Ticker("RELIANCE.NS").history(period="5d")        # → previous-day OHLC
   yf.Ticker("^NSEI").history(period="5d")              # NIFTY spot
   ```
3. **`MockKotakConnector` stays** as a fallback; **Yahoo becomes the default EOD source** for `morning_setup()`.
4. **`config/watchlist.yaml`** holds the symbol map (yahoo ticker + kotak token + lot size), killing the hardcoded `SYMBOL_TOKENS` drift.

Add to `requirements.txt`:
```
yfinance>=0.2.40
pyyaml>=6.0
```

**Why Yahoo is the right call for EOD:** free, no login, no rate limits for daily EOD, mature library, and it decouples your most important morning step from the broker.

---

## 5. Open Decisions (please confirm before Phase 1 build)

1. **Index CPR basis** — compute NIFTY/BANKNIFTY CPR on the **spot index** (`^NSEI`/`^NSEBANK`)? *(recommended)* Or on the front-month future?
2. **Persistence** — OK to move from JSON to **SQLite** in Phase 2? *(recommended)*
3. **Deployment target** — local desktop for now, or do you want it cloud-deployable from the start?
4. **Alert channel** — Telegram preferred?
5. **Symbol universe** — stick to NIFTY/BANKNIFTY + a few large caps, or expand to a broader NSE scan?

---

## Recommended build order (at a glance)

```
Phase 1  → Yahoo EOD + provider abstraction + config + tests   ← START HERE
Phase 2  → Scheduler + WS monitor + FastAPI + dashboard + SQLite
Phase 3  → Backtester + tuning + 3-month forward paper run
Phase 4  → Live kill-switch + live orders + Zerodha
Phase 5  → Productize / SaaS
```
