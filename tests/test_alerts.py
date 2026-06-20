"""Alerts tests — no real network calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from alerts import ConsoleAlertProvider, WhatsAppCallMeBotProvider, get_alert_provider


def test_console_provider_sends(capys=None):
    p = ConsoleAlertProvider()
    assert p.send("hello world") is True


def test_whatsapp_provider_requires_credentials(monkeypatch):
    # Strip env so it must raise.
    monkeypatch.delenv("WHATSAPP_NUMBER", raising=False)
    monkeypatch.delenv("WHATSAPP_APIKEY", raising=False)
    try:
        WhatsAppCallMeBotProvider()
        assert False, "should have raised ValueError"
    except ValueError:
        pass


def test_whatsapp_provider_constructs_with_explicit_args():
    # Constructor only — we don't hit the network.
    p = WhatsAppCallMeBotProvider(number="919999999999", api_key="dummykey")
    assert p.number == "919999999999"
    assert p.api_key == "dummykey"


def test_factory_console_default():
    p = get_alert_provider("console")
    assert isinstance(p, ConsoleAlertProvider)


def test_factory_whatsapp_without_creds_falls_back_to_console(monkeypatch):
    monkeypatch.delenv("WHATSAPP_NUMBER", raising=False)
    monkeypatch.delenv("WHATSAPP_APIKEY", raising=False)
    p = get_alert_provider("whatsapp")
    # Missing creds → graceful fallback, not a crash.
    assert isinstance(p, ConsoleAlertProvider)


def test_factory_empty_string_defaults_to_console():
    p = get_alert_provider("")
    assert isinstance(p, ConsoleAlertProvider)
