from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi.testclient import TestClient
from api_server import app


os.environ["PIVOTBOSS_API_KEY"] = "secret"
client = TestClient(app)


def test_paginated_trades_endpoint():
    response = client.get("/api/v1/trades?page=1&page_size=5", headers={"X-API-Key": "secret"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 5
    assert isinstance(payload["items"], list)
    assert payload["total"] >= 1


def test_paginated_signals_endpoint():
    response = client.get("/api/v1/signals?page=1&page_size=5", headers={"X-API-Key": "secret"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 5
    assert isinstance(payload["items"], list)
