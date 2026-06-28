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


# ── EMAIL ─────────────────────────────────────────────────────────────────────

class EmailAlertProvider(AlertProvider):
    """
    Send trade alerts via email using Gmail SMTP.

    Env:
      EMAIL_FROM    - sender Gmail address
      EMAIL_TO      - recipient address (can be same as FROM)
      EMAIL_PASS    - Gmail App Password (not your main password)
                      Generate at: myaccount.google.com/apppasswords
    """

    def __init__(self):
        self.from_addr = os.getenv("EMAIL_FROM", "").strip()
        self.to_addr   = os.getenv("EMAIL_TO",   "").strip()
        self.password  = os.getenv("EMAIL_PASS",  "").strip()
        if not all([self.from_addr, self.to_addr, self.password]):
            raise ValueError("EmailAlertProvider needs EMAIL_FROM, EMAIL_TO, EMAIL_PASS in .env")

    def send(self, message: str) -> bool:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        try:
            subject_line = message.split("\n")[0][:60]
            msg = MIMEMultipart()
            msg["From"]    = self.from_addr
            msg["To"]      = self.to_addr
            msg["Subject"] = f"PivotBoss AI — {subject_line}"
            msg.attach(MIMEText(message, "plain"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.from_addr, self.password)
                server.sendmail(self.from_addr, self.to_addr, msg.as_string())

            logger.info("Email alert sent to %s", self.to_addr)
            return True
        except Exception as e:
            logger.error("Email alert failed: %s", e)
            return False


# ── TELEGRAM ──────────────────────────────────────────────────────────────────

class TelegramAlertProvider(AlertProvider):
    """
    Send alerts via Telegram bot.

    Setup:
      1. Message @BotFather on Telegram → /newbot → copy the token
      2. Message your new bot once (so it can send to you)
      3. Get your chat ID: https://api.telegram.org/bot<TOKEN>/getUpdates
      4. Add to .env:
           TELEGRAM_TOKEN=your_bot_token
           TELEGRAM_CHAT_ID=your_chat_id
    """

    def __init__(self):
        self.token   = os.getenv("TELEGRAM_TOKEN",   "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not self.token or not self.chat_id:
            raise ValueError("TelegramAlertProvider needs TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env")

    def send(self, message: str) -> bool:
        params = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text":    message,
            "parse_mode": "Markdown",
        })
        url = f"https://api.telegram.org/bot{self.token}/sendMessage?{params}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                ok = resp.status == 200
            return ok
        except Exception as e:
            logger.error("Telegram alert failed: %s", e)
            return False


# ── MULTI-CHANNEL ─────────────────────────────────────────────────────────────

class MultiAlertProvider(AlertProvider):
    """Send to multiple channels simultaneously."""

    def __init__(self, providers: list):
        self.providers = providers

    def send(self, message: str) -> bool:
        return all(p.send(message) for p in self.providers)


# ── FACTORY ───────────────────────────────────────────────────────────────────

def get_alert_provider(channel: str = "console") -> AlertProvider:
    """
    Build an alert provider by name.

      "console"   → ConsoleAlertProvider (default, always works)
      "whatsapp"  → WhatsAppCallMeBotProvider (needs WHATSAPP_* env)
      "email"     → EmailAlertProvider (needs EMAIL_FROM/TO/PASS env)
      "telegram"  → TelegramAlertProvider (needs TELEGRAM_TOKEN/CHAT_ID env)
      "all"       → MultiAlertProvider using all configured channels
    """
    channel = (channel or "console").strip().lower()

    if channel == "whatsapp":
        try:
            return WhatsAppCallMeBotProvider()
        except ValueError as e:
            logger.warning("Falling back to console alerts: %s", e)
            return ConsoleAlertProvider()

    if channel == "email":
        try:
            return EmailAlertProvider()
        except ValueError as e:
            logger.warning("Falling back to console alerts: %s", e)
            return ConsoleAlertProvider()

    if channel == "telegram":
        try:
            return TelegramAlertProvider()
        except ValueError as e:
            logger.warning("Falling back to console alerts: %s", e)
            return ConsoleAlertProvider()

    if channel == "all":
        providers = [ConsoleAlertProvider()]
        for cls, name in [(WhatsAppCallMeBotProvider, "WhatsApp"),
                          (EmailAlertProvider, "Email"),
                          (TelegramAlertProvider, "Telegram")]:
            try:
                providers.append(cls())
                logger.info("%s alerts enabled", name)
            except ValueError:
                pass
        return MultiAlertProvider(providers)

    return ConsoleAlertProvider()
