# PivotBoss AI — CPR Trading System 🎯

An AI-powered intraday trading system built on the **Central Pivot Range (CPR)** technique from *"Secrets of Pivot Boss"* by Frank Ochoa. Currently integrated with **Kotak Neo API**, with Zerodha support coming in Phase 2.

---

## What It Does

Every morning before market open, this system:
1. **Fetches** previous day's OHLC data for your watchlist
2. **Calculates** CPR levels (TC, BC, Pivot, S1/S2/S3, R1/R2/R3)
3. **Classifies** the CPR (Narrow = trending day, Wide = sideways day, Virgin CPR = strong magnet)
4. **Generates** AI-enhanced trading signals with entry, stop loss, and 3 targets
5. **Paper trades** automatically — tracks P&L, win rate, drawdown

---

## CPR Logic Implemented (from Pivot Boss book)

| Rule | Description |
|------|-------------|
| Narrow CPR | Width < 0.3% → Trending day expected |
| Wide CPR | Width > 0.8% → Sideways/range day |
| Virgin CPR | Never tested → Extremely strong magnet |
| Open above TC | Bullish day bias |
| Open below BC | Bearish day bias |
| Price in CPR | Wait for breakout |
| Gap above R1 | Watch for reversal |
| Gap below S1 | Watch for reversal |

---

## Project Structure

```
cpr_trader/
├── backend/
│   ├── cpr_engine.py        # Core CPR math + signal generator
│   ├── paper_trader.py      # Paper trading simulator + P&L tracker
│   ├── kotak_connector.py   # Kotak Neo API integration (live + mock)
│   └── trading_bot.py       # Main bot — ties everything together
├── frontend/
│   └── dashboard.html       # Trading dashboard (open in any browser)
├── config/
│   └── .env.example         # Copy this to .env and fill credentials
├── logs/                    # Bot logs (auto-created)
├── data/                    # Trade data (auto-created)
├── requirements.txt
└── README.md
```

---

## Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/YOUR_USERNAME/pivotboss-ai.git
cd pivotboss-ai
pip install -r requirements.txt
```

### 2. Configure
```bash
cp config/.env.example config/.env
# Open config/.env and fill your Kotak Neo credentials
```

### 3. Get Kotak Neo API Credentials
1. Open **Kotak Neo app** or web → **Invest tab** → **Trade API card**
2. Click **Generate Application**
3. Copy your **Consumer Key** and **Consumer Secret**
4. Register TOTP for Trade API access and note your UCC from the Kotak Neo profile
5. Put the Consumer Key, registered mobile number, UCC, and 6-digit Neo MPIN in `config/.env`

To test only the Kotak login flow before running the bot:

```bash
python scripts/kotak_login.py
```

### 4. Run the Bot

**Demo mode (no credentials needed):**
```bash
cd cpr_trader
python backend/trading_bot.py
```

**With real Kotak API:**
```bash
python backend/trading_bot.py --live
```

**Morning setup only (9:00 AM):**
```bash
python backend/trading_bot.py --setup
```

**Market scan (9:16 AM):**
```bash
python backend/trading_bot.py --scan
```

**Full cycle:**
```bash
python backend/trading_bot.py --all
```

### 5. Open Dashboard
Just open `frontend/dashboard.html` in any browser — no server needed!

---

## Daily Workflow

```
9:00 AM  →  python trading_bot.py --setup    (fetch OHLC, calculate CPR)
9:16 AM  →  python trading_bot.py --scan     (get opening price, generate signals)
9:16 AM+ →  python trading_bot.py --monitor  (watch positions, auto close on SL/target)
3:30 PM  →  python trading_bot.py --summary  (see day's P&L)
```

---

## Risk Management (Built-in)

- Max **1% capital risk** per trade (auto position sizing)
- Max **3 open positions** at a time
- Stop loss always set at CPR boundary
- 3 profit targets (T1 = R1/S1, T2 = R2/S2, T3 = R3/S3)
- **PAPER_TRADING_MODE=true** blocks real orders even with live API

---

## Roadmap

- [x] Phase 1 — CPR Engine + Paper Trading Dashboard
- [x] Phase 1 — Kotak Neo API integration (mock + live)
- [ ] Phase 2 — Live data WebSocket feed
- [ ] Phase 2 — Auto scheduler (cron at 9:00 AM)
- [ ] Phase 2 — REST API to connect dashboard to backend live
- [ ] Phase 3 — Live order execution (after 3 months paper trading)
- [ ] Phase 3 — Zerodha Kite API support
- [ ] Phase 3 — Backtesting on 1-year historical data
- [ ] Phase 4 — SEBI licensed SaaS product

---

## ⚠️ Disclaimer

This software is for **educational and personal use only**.  
- Always paper trade first — minimum 3 months before going live
- Past CPR accuracy does not guarantee future results
- The author is not a SEBI-registered advisor
- Never risk money you cannot afford to lose

---

## Tech Stack

- **Python 3.10+** — Backend engine
- **Kotak Neo API v2** — Market data + order execution  
- **HTML/CSS/JS** — Dashboard (zero dependencies, opens in browser)
- **FastAPI** — REST API server (Phase 2)
