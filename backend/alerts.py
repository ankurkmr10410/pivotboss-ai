"""
Alerting — pluggable notification for signals, fills, summaries.

Swappable providers behind one interface so the trading bot / scheduler can
notify without knowing the channel:

  - ConsoleAlertProvider    → logs/prints (default, always works)
  - WhatsAppCallMeBotProvider → WhatsApp via CallMeBot (free, your own number)

Setup for WhatsApp (CallMeBot):
  1. Add the phone number +34 600 21 25 47 (CallMeBot) as a WhatsApp contact.
  2. Send it the message: "I allow callmebot to send me messages"
  3. You'll get an API key back. Put it + your number in config/.env.

WHATSAPP_NUMBER=<your number, international, digits only>   e.g. 919876543210
WHATSAPP_APIKEY=<the key CallMeBot gave you>

Upgrade path: for production / multi-user, swap in a TwilioAlertProvider
(official WhatsApp Business API). The interface stays identical.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class AlertProvider(ABC):
    """One method. Implementations send `message` to their channel."""

    @abstractmethod
    def send(self, message: str) -> bool:
        """Return True if delivered (or at least queued without error)."""
        ...


# ── CONSOLE ───────────────────────────────────────────────────────────────────

class ConsoleAlertProvider(AlertProvider):
    """Always-on default. Prints + logs the message."""

    def send(self, message: str) -> bool:
        try:
            print("\n" + "=" * 50)
            print("🔔 ALERT")
            print("=" * 50)
            print(message)
            print("=" * 50 + "\n")
            return True
        except Exception as e:
            logger.error("Console alert failed: %s", e)
            return False


# ── WHATSAPP (CallMeBot) ──────────────────────────────────────────────────────

class WhatsAppCallMeBotProvider(AlertProvider):
    """
    Send WhatsApp messages via CallMeBot's free API.

    Env:
      WHATSAPP_NUMBER  - recipient (your) number, international, digits only
      WHATSAPP_APIKEY  - API key from CallMeBot setup
    """

    API_URL = "https://api.callmebot.com/whatsapp.php"

    def __init__(self, number: Optional[str] = None, api_key: Optional[str] = None):
        self.number = (number or os.getenv("WHATSAPP_NUMBER", "")).strip()
        self.api_key = (api_key or os.getenv("WHATSAPP_APIKEY", "")).strip()
        if not self.number or not self.api_key:
            raise ValueError(
                "WhatsApp provider needs WHATSAPP_NUMBER and WHATSAPP_APIKEY in env. "
                "See config/.env.example and the setup steps in backend/alerts.py."
            )

    def send(self, message: str) -> bool:
        params = urllib.parse.urlencode({
            "phone": self.number,
            "text": message,
            "apikey": self.api_key,
        })
        url = f"{self.API_URL}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace").lower()
            ok = resp.status == 200 and "invalid" not in body and "error" not in body
            if not ok:
                logger.error("WhatsApp send returned non-OK: %s", body[:200])
            return ok
        except Exception as e:
            logger.error("WhatsApp alert failed: %s", e)
            return False


# ── FACTORY ───────────────────────────────────────────────────────────────────

def get_alert_provider(channel: str = "console") -> AlertProvider:
    """
    Build an alert provider by name.

      "console"  → ConsoleAlertProvider (default, always works)
      "whatsapp" → WhatsAppCallMeBotProvider (needs WHATSAPP_* env)
    """
    channel = (channel or "console").strip().lower()

    if channel == "whatsapp":
        try:
            return WhatsAppCallMeBotProvider()
        except ValueError as e:
            logger.warning("Falling back to console alerts: %s", e)
            return ConsoleAlertProvider()

    return ConsoleAlertProvider()
