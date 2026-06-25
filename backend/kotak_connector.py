"""
Kotak Neo API Connector
Handles: Login, Live OHLC data, WebSocket ticks, Order placement

HOW TO GET YOUR CREDENTIALS:
1. Open Kotak Neo app or web → Invest tab → Trade API card
2. Generate application → copy the Consumer Key & Consumer Secret
3. Put them in your .env file (see config/.env.example)

API Docs: https://github.com/Kotak-Neo/Kotak-neo-api-v2
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Callable, Optional
from dotenv import load_dotenv

load_dotenv("config/.env")

logger = logging.getLogger(__name__)

# ── SYMBOL MAP ────────────────────────────────────────────────────────────────
# Kotak Neo uses exchange tokens. The authoritative map now lives in
# config/watchlist.yaml (loaded by config_loader.Watchlist). The static dict
# below is a fallback used only if the yaml is unavailable, so the module still
# imports standalone.
_FALLBACK_SYMBOL_TOKENS = {
    "NIFTY":      {"exchange": "nse_fo", "token": "26000", "lot_size": 75},
    "BANKNIFTY":  {"exchange": "nse_fo", "token": "26009", "lot_size": 35},
    "RELIANCE":   {"exchange": "nse_cm", "token": "2885",  "lot_size": 1},
    "HDFCBANK":   {"exchange": "nse_cm", "token": "1333",  "lot_size": 1},
    "TCS":        {"exchange": "nse_cm", "token": "11536", "lot_size": 1},
    "INFY":       {"exchange": "nse_cm", "token": "1594",  "lot_size": 1},
}


def _load_symbol_tokens() -> dict:
    """Load SYMBOL_TOKENS from config/watchlist.yaml, with a static fallback."""
    try:
        from config_loader import Watchlist
        kotak_map = Watchlist().kotak_map()
        if kotak_map:
            return kotak_map
    except Exception:
        pass
    return dict(_FALLBACK_SYMBOL_TOKENS)


SYMBOL_TOKENS = _load_symbol_tokens()


class KotakConnector:
    """
    Wrapper around Kotak Neo API v2.
    Handles login (OTP flow), historical data, live ticks, and orders.
    """

    def __init__(self):
        self.consumer_key    = os.getenv("KOTAK_CONSUMER_KEY", "").strip()
        self.consumer_secret = os.getenv("KOTAK_CONSUMER_SECRET", "").strip()
        self.neo_fin_key     = os.getenv("KOTAK_NEOFINK", "").strip()
        raw_mobile           = os.getenv("KOTAK_MOBILE", "")
        self.mobile_number   = "".join(ch for ch in raw_mobile if ch.isdigit())
        self.ucc             = os.getenv("KOTAK_UCC", "").strip().upper()
        self.password        = os.getenv("KOTAK_PASSWORD", "").strip()
        self.mpin            = os.getenv("KOTAK_MPIN", "").strip()
        self.environment     = os.getenv("KOTAK_ENVIRONMENT", "prod")

        self.client = None
        self.is_logged_in = False
        self._tick_callbacks: list[Callable] = []

    # ── AUTH ────────────────────────────────────────────────────────────────

    def _init_client(self):
        """Create the Kotak Neo SDK client using the v2 constructor."""
        from neo_api_client import NeoAPI

        if self.client is None:
            self.client = NeoAPI(
                environment=self.environment,
                access_token=None,
                neo_fin_key=self.neo_fin_key or None,
                consumer_key=self.consumer_key,
            )
        return self.client

    def login(self) -> bool:
        """
        Kotak Neo Login flow (3 steps):
        1. login()         → sends OTP to mobile
        2. session_2fa()   → verify OTP, get session token
        3. Done            → client ready for trading
        """
        try:
            self._init_client()

            # Step 1: Initiate login — triggers OTP to your mobile
            resp = self.client.login(
                mobilenumber=self.mobile_number,
                password=self.password,
            )

            if resp and resp.get("data", {}).get("token"):
                logger.info("OTP sent to mobile. Call verify_otp(otp) next.")
                return True
            else:
                logger.error(f"Login failed: {resp}")
                return False

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def verify_otp(self, otp: str) -> bool:
        """Step 2: Verify OTP and complete login"""
        try:
            resp = self.client.session_2fa(OTP=otp)
            if resp and resp.get("data"):
                self.is_logged_in = True
                logger.info("Kotak Neo login successful!")
                return True
            else:
                logger.error(f"OTP verification failed: {resp}")
                return False
        except Exception as e:
            logger.error(f"OTP error: {e}")
            return False

    def _mask_mobile(self, mobile: str) -> str:
        return mobile[-4:].rjust(len(mobile), "*") if mobile else ""

    def totp_login(self, ucc: Optional[str] = None, totp: Optional[str] = None) -> bool:
        """Step 1 of Kotak Neo v2 TOTP flow using the SDK directly.

        Mobile must be in +91XXXXXXXXXX format.
        neo_fin_key defaults to "neotradeapi" if not set (SDK default).
        consumer_key = the API Access Token from the Kotak Neo Trade API portal.
        """
        try:
            self._init_client()
            ucc  = (ucc or self.ucc).strip()
            totp = (totp or "").strip()

            missing = []
            if not self.consumer_key:
                missing.append("KOTAK_CONSUMER_KEY (API Access Token from Trade API portal)")
            if not ucc:
                missing.append("KOTAK_UCC")
            if not totp:
                missing.append("TOTP")
            if missing:
                logger.error("Missing Kotak TOTP login value(s): %s", ", ".join(missing))
                return False

            # Mobile must be in +91XXXXXXXXXX format — Kotak server requires this.
            mobile = self.mobile_number.strip()
            if mobile.startswith("+"):
                pass  # already correct
            elif mobile.startswith("91") and len(mobile) == 12:
                mobile = "+" + mobile
            elif len(mobile) == 10:
                mobile = "+91" + mobile
            else:
                mobile = "+91" + mobile[-10:]

            logger.info("Attempting Kotak TOTP login | UCC: %s | mobile: %s", ucc, self._mask_mobile(mobile))
            logger.info(
                "Credentials | consumer_key=%s | neo_fin_key=%s",
                "SET" if self.consumer_key else "MISSING",
                "SET" if self.neo_fin_key else "using default",
            )

            resp = self.client.totp_login(
                mobile_number=mobile,
                ucc=ucc,
                totp=totp,
            )

            if self._response_has_error(resp):
                logger.error("TOTP login failed: %s", self._safe_response(resp))
                return False

            logger.info("Kotak Neo TOTP login accepted. Validate MPIN next.")
            return True
        except Exception as e:
            logger.error(f"TOTP login error: {e}")
            return False

    def totp_validate(self, mpin: Optional[str] = None) -> bool:
        """Step 2 of Kotak Neo v2 TOTP flow: validate MPIN and create trade token."""
        try:
            self._init_client()
            mpin = (mpin or self.mpin).strip()
            if not mpin:
                logger.error("Missing KOTAK_MPIN")
                return False
            if not (mpin.isdigit() and len(mpin) == 6):
                logger.error("KOTAK_MPIN must be exactly 6 digits.")
                return False

            resp = self.client.totp_validate(mpin=mpin)
            if self._response_has_error(resp):
                logger.error("MPIN validation failed: %s", self._safe_response(resp))
                return False

            self.is_logged_in = True
            logger.info("Kotak Neo TOTP login successful.")
            return True
        except Exception as e:
            logger.error(f"MPIN validation error: {e}")
            return False

    # ── NON-INTERACTIVE / AUTO LOGIN ──────────────────────────────────────────

    def totp_seed_login(self, seed: Optional[str] = None, mpin: Optional[str] = None) -> bool:
        """
        Fully non-interactive TOTP login using a stored base32 seed.

        If KOTAK_TOTP_SEED is set (the base32 secret from your authenticator's
        QR code), this generates the current 6-digit TOTP automatically and
        logs in with no human input — safe for unattended scheduling.

        Falls back gracefully if pyotp is not installed.
        """
        try:
            import pyotp
        except ImportError:
            logger.error("pyotp not installed. Run: pip install pyotp")
            return False

        seed = (seed or os.getenv("KOTAK_TOTP_SEED", "")).strip().replace(" ", "")
        if not seed:
            logger.error("KOTAK_TOTP_SEED not set. Cannot auto-generate TOTP.")
            return False

        totp_code = pyotp.TOTP(seed).now()
        logger.info("Generated TOTP from seed (valid for ~30s).")
        if not self.totp_login(totp=totp_code):
            return False
        return self.totp_validate(mpin=mpin)

    def login_interactive(self, mpin: Optional[str] = None) -> bool:
        """
        Interactive one-time login for environments without a TOTP seed.

        Prompts (via input()) for the current 6-digit TOTP, validates with the
        stored MPIN, and leaves the session active for the trading day. Use this
        once at server startup; the session then persists for the scheduler.

        Safe to call when stdin is a TTY. Not for unattended scheduling.
        """
        mpin = mpin or self.mpin
        print("\n-- Kotak Neo live login --")
        print(f"  Mobile: {self._mask_mobile(self.mobile_number)} | UCC: {self.ucc}")
        totp = input("  Enter current 6-digit TOTP from your authenticator: ").strip()
        if not totp:
            logger.error("No TOTP entered. Aborting live login.")
            return False
        if not self.totp_login(totp=totp):
            return False
        if not mpin:
            mpin = input("  Enter 6-digit MPIN (or blank to use KOTAK_MPIN): ").strip()
        return self.totp_validate(mpin=mpin)

    def ensure_logged_in(self) -> bool:
        """
        Best-effort login used by PivotBossBot on startup when mock=False.

        Preference order:
          1. KOTAK_TOTP_SEED set  → non-interactive seed login (best for schedule)
          2. stdin is a TTY      → interactive one-time prompt
          3. otherwise           → log a clear error and return False
        """
        if self.is_logged_in:
            return True
        if os.getenv("KOTAK_TOTP_SEED", "").strip():
            logger.info("KOTAK_TOTP_SEED found — attempting non-interactive login.")
            return self.totp_seed_login()
        try:
            if sys.stdin.isatty():
                logger.info("No TOTP seed. Starting interactive login (one-time).")
                return self.login_interactive()
        except Exception:
            pass
        logger.error(
            "Live Kotak login required but no seed set and not a TTY. "
            "Set KOTAK_TOTP_SEED in config/.env for unattended scheduling, "
            "or run 'python -m backend.api_server --login'."
        )
        return False

    @staticmethod
    def _response_has_error(resp) -> bool:
        if not resp:
            return True
        if isinstance(resp, dict):
            if resp.get("error") or resp.get("Error") or resp.get("Error Message"):
                return True
            return not resp.get("data")
        return False

    @staticmethod
    def _safe_response(resp):
        if not isinstance(resp, dict):
            return resp
        safe = json.loads(json.dumps(resp, default=str))
        data = safe.get("data")
        if isinstance(data, dict):
            for key in ("token", "sid", "rid", "searchAPIKey"):
                if data.get(key):
                    data[key] = "***"
        return safe

    # ── MARKET DATA ─────────────────────────────────────────────────────────

    def get_quotes(self, symbol: str) -> Optional[dict]:
        """Get live quote for a symbol"""
        if not self.is_logged_in:
            logger.error("Not logged in. Call login() + verify_otp() first.")
            return None

        try:
            info = SYMBOL_TOKENS.get(symbol)
            if not info:
                logger.error(f"Symbol {symbol} not in SYMBOL_TOKENS map.")
                return None

            instrument_tokens = [{
                "instrument_token": info["token"],
                "exchange_segment": info["exchange"],
            }]
            resp = self.client.quotes(
                instrument_tokens=instrument_tokens,
                quote_type="ltp",          # ltp = last traded price
            )

            if resp and resp.get("data"):
                d = resp["data"][0]
                return {
                    "symbol": symbol,
                    "ltp": float(d.get("last_traded_price", 0)) / 100,  # Kotak returns paise
                    "open": float(d.get("open_price", 0)) / 100,
                    "high": float(d.get("high_price", 0)) / 100,
                    "low": float(d.get("low_price", 0)) / 100,
                    "close": float(d.get("close_price", 0)) / 100,
                    "volume": int(d.get("volume", 0)),
                    "timestamp": datetime.now().isoformat(),
                }
        except Exception as e:
            logger.error(f"Quote error for {symbol}: {e}")
            return None

    def get_historical_ohlc(self, symbol: str, days: int = 5) -> list[dict]:
        """
        Get historical OHLC for CPR calculation.
        Returns list of daily candles: [{date, open, high, low, close}, ...]
        """
        if not self.is_logged_in:
            return []

        try:
            info = SYMBOL_TOKENS.get(symbol)
            if not info:
                return []

            to_date   = datetime.now().strftime("%d-%m-%Y")
            from_date = (datetime.now() - timedelta(days=days + 5)).strftime("%d-%m-%Y")  # extra buffer for weekends

            resp = self.client.historical_candles(
                instrument_token=info["token"],
                exchange=info["exchange"],
                to_date=to_date,
                from_date=from_date,
                timeframe="1D",
            )

            candles = []
            if resp and resp.get("data", {}).get("candles"):
                for c in resp["data"]["candles"][-days:]:
                    # Format: [timestamp, open, high, low, close, volume]
                    candles.append({
                        "date":  c[0][:10],
                        "open":  float(c[1]),
                        "high":  float(c[2]),
                        "low":   float(c[3]),
                        "close": float(c[4]),
                        "volume": int(c[5]),
                    })
            return candles

        except Exception as e:
            logger.error(f"Historical data error for {symbol}: {e}")
            return []

    # ── WEBSOCKET LIVE TICKS ────────────────────────────────────────────────

    def on_tick(self, callback: Callable):
        """Register a callback to receive live price ticks"""
        self._tick_callbacks.append(callback)

    def _on_message(self, message):
        """Internal handler for WebSocket messages"""
        try:
            for cb in self._tick_callbacks:
                cb(message)
        except Exception as e:
            logger.error(f"Tick callback error: {e}")

    def _on_error(self, error):
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, message):
        logger.warning(f"WebSocket closed: {message}")

    def _on_open(self, message):
        logger.info("WebSocket connected OK")

    def start_live_feed(self, symbols: list[str]):
        """Start WebSocket live feed for given symbols"""
        if not self.is_logged_in:
            logger.error("Login required before starting live feed")
            return

        try:
            instrument_tokens = []
            for sym in symbols:
                info = SYMBOL_TOKENS.get(sym)
                if info:
                    instrument_tokens.append({
                        "instrument_token": info["token"],
                        "exchange_segment": info["exchange"],
                    })

            self.client.subscribe(
                instrument_tokens=instrument_tokens,
                isIndex=False,
                isDepth=False,
            )

            self.client.on_message  = self._on_message
            self.client.on_error    = self._on_error
            self.client.on_close    = self._on_close
            self.client.on_open     = self._on_open

            logger.info(f"Live feed started for: {symbols}")

        except Exception as e:
            logger.error(f"Live feed error: {e}")

    # ── ORDER PLACEMENT ─────────────────────────────────────────────────────

    def place_order(self, symbol: str, action: str, quantity: int,
                    order_type: str = "MKT", price: float = 0.0,
                    product: str = "NRML") -> Optional[dict]:
        """
        Place an order via Kotak Neo API.

        Args:
            symbol:     e.g. "RELIANCE"
            action:     "BUY" or "SELL"
            quantity:   number of shares/lots
            order_type: "MKT" (market) or "LMT" (limit)
            price:      limit price (0 for market orders)
            product:    "NRML" (carry forward) or "MIS" (intraday)

        Returns:
            dict with order_id and status
        """
        if not self.is_logged_in:
            logger.error("Not logged in. Cannot place order.")
            return None

        # SAFETY CHECK: paper trading mode guard
        paper_mode = os.getenv("PAPER_TRADING_MODE", "true").lower() == "true"
        if paper_mode:
            logger.warning(f"[PAPER MODE] Would place: {action} {quantity} {symbol} @ {order_type}")
            return {
                "order_id": f"PAPER_{int(time.time())}",
                "status": "paper_executed",
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "price": price,
                "paper": True,
            }

        try:
            info = SYMBOL_TOKENS.get(symbol)
            if not info:
                logger.error(f"Unknown symbol: {symbol}")
                return None

            resp = self.client.place_order(
                exchange_segment=info["exchange"],
                product=product,
                price=str(price),
                order_type=order_type,
                quantity=str(quantity),
                validity="DAY",
                trading_symbol=symbol,
                transaction_type=action,          # "BUY" or "SELL"
                amo="NO",
                disclosed_quantity="0",
                market_protection="0",
                pf="N",
                trigger_price="0",
                tag=None,
            )

            if resp and resp.get("data", {}).get("nOrdNo"):
                order_id = resp["data"]["nOrdNo"]
                logger.info(f"Order placed: {order_id} | {action} {quantity} {symbol}")
                return {"order_id": order_id, "status": "placed", "symbol": symbol}
            else:
                logger.error(f"Order failed: {resp}")
                return None

        except Exception as e:
            logger.error(f"Order placement error: {e}")
            return None

    def get_positions(self) -> list[dict]:
        """Get current open positions"""
        if not self.is_logged_in:
            return []
        try:
            resp = self.client.positions()
            positions = []
            if resp and resp.get("data"):
                for p in resp["data"]:
                    positions.append({
                        "symbol":    p.get("trdSym", ""),
                        "quantity":  int(p.get("flQty", 0)),
                        "avg_price": float(p.get("avgPrc", 0)),
                        "pnl":       float(p.get("unrealizedMTOM", 0)),
                        "product":   p.get("prod", ""),
                    })
            return positions
        except Exception as e:
            logger.error(f"Positions error: {e}")
            return []

    def get_order_book(self) -> list[dict]:
        """Get today's order history"""
        if not self.is_logged_in:
            return []
        try:
            resp = self.client.order_report()
            if resp and resp.get("data"):
                return resp["data"]
            return []
        except Exception as e:
            logger.error(f"Order book error: {e}")
            return []


# ── MOCK CONNECTOR (for testing without real credentials) ─────────────────────

class MockKotakConnector(KotakConnector):
    """
    Simulates Kotak API responses with realistic data.
    Use this when you don't have API credentials yet,
    or want to test logic without a real connection.
    """

    MOCK_PRICES = {
        "NIFTY":     {"ltp": 24780, "open": 24720, "high": 24850, "low": 24450, "close": 24700},
        "BANKNIFTY": {"ltp": 51950, "open": 52050, "high": 52400, "low": 51800, "close": 52100},
        "RELIANCE":  {"ltp": 2968,  "open": 2960,  "high": 2980,  "low": 2920,  "close": 2955},
        "HDFCBANK":  {"ltp": 1745,  "open": 1738,  "high": 1760,  "low": 1730,  "close": 1742},
        "TCS":       {"ltp": 4125,  "open": 4108,  "high": 4150,  "low": 4090,  "close": 4118},
        "INFY":      {"ltp": 1890,  "open": 1882,  "high": 1905,  "low": 1870,  "close": 1885},
    }

    def login(self) -> bool:
        self.is_logged_in = True
        logger.info("Mock login successful (no real API call)")
        return True

    def verify_otp(self, otp: str) -> bool:
        return True

    def get_quotes(self, symbol: str) -> Optional[dict]:
        import random
        base = self.MOCK_PRICES.get(symbol)
        if not base:
            return None
        # Simulate small price movement
        noise = random.uniform(-0.002, 0.002)
        ltp = round(base["ltp"] * (1 + noise), 2)
        return {
            "symbol": symbol,
            "ltp": ltp,
            "open": base["open"],
            "high": max(base["high"], ltp),
            "low": min(base["low"], ltp),
            "close": base["close"],
            "volume": random.randint(100000, 500000),
            "timestamp": datetime.now().isoformat(),
        }

    def get_historical_ohlc(self, symbol: str, days: int = 5) -> list[dict]:
        """Generate realistic mock OHLC for the last N days"""
        import random
        base = self.MOCK_PRICES.get(symbol, {})
        if not base:
            return []

        candles = []
        price = base["close"]
        for i in range(days, 0, -1):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            change = random.uniform(-0.015, 0.015)
            close = round(price * (1 + change), 2)
            high  = round(max(price, close) * (1 + random.uniform(0, 0.008)), 2)
            low   = round(min(price, close) * (1 - random.uniform(0, 0.008)), 2)
            open_ = round(price * (1 + random.uniform(-0.005, 0.005)), 2)
            candles.append({"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": random.randint(50000, 2000000)})
            price = close
        return candles

    def place_order(self, symbol, action, quantity, **kwargs) -> dict:
        logger.info(f" Mock order: {action} {quantity} {symbol}")
        return {
            "order_id": f"MOCK_{int(time.time())}",
            "status": "mock_executed",
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "paper": True,
        }

    def get_positions(self) -> list:
        return []


# ── FACTORY ────────────────────────────────────────────────────────────────────

# Module-level singleton — ensures --login session is reused by the bot.
_connector_singleton: Optional[KotakConnector] = None


def set_connector(conn: KotakConnector) -> None:
    """Store a pre-logged-in connector so the bot reuses it (used by --login flow)."""
    global _connector_singleton
    _connector_singleton = conn
    logger.info("Connector singleton set: %s", type(conn).__name__)


def get_connector(mock: bool = False, auto_login: bool = True) -> KotakConnector:
    """
    Returns the appropriate connector.
    - If a singleton was stored via set_connector() (e.g. after --login), return it.
    - Set mock=True to use fake data (no credentials needed).
    - Set PAPER_TRADING_MODE=true in .env to prevent real orders even with live connector.
    - When mock=False and auto_login=True, attempts live Kotak login.
    """
    global _connector_singleton

    # Reuse pre-logged-in connector from --login flow.
    if _connector_singleton is not None:
        logger.info("Reusing pre-logged-in connector singleton: %s", type(_connector_singleton).__name__)
        return _connector_singleton

    if mock or not os.getenv("KOTAK_CONSUMER_KEY"):
        logger.info("Using MOCK connector (no credentials found)")
        conn = MockKotakConnector()
        conn.login()
        return conn

    conn = KotakConnector()
    if auto_login:
        if conn.ensure_logged_in():
            logger.info("Live Kotak connector ready.")
        else:
            logger.warning(
                "Live connector created but login failed — quotes will fail. "
                "Falling back to mock so the system stays up."
            )
            return get_connector(mock=True)
    return conn


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with mock connector
    conn = get_connector(mock=True)

    print("\n── Live Quotes ──")
    for sym in ["NIFTY", "BANKNIFTY", "RELIANCE"]:
        q = conn.get_quotes(sym)
        print(f"  {sym}: ₹{q['ltp']} | O:{q['open']} H:{q['high']} L:{q['low']} C:{q['close']}")

    print("\n── Historical OHLC (last 3 days for NIFTY) ──")
    candles = conn.get_historical_ohlc("NIFTY", days=3)
    for c in candles:
        print(f"  {c['date']} | O:{c['open']} H:{c['high']} L:{c['low']} C:{c['close']}")

    print("\n── Mock Order ──")
    order = conn.place_order("RELIANCE", "BUY", 10)
    print(f"  {order}")
