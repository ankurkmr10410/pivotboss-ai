from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi.testclient import TestClient
from api_server import app


client = TestClient(app)


def test_health_and_ready_endpoints():
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_versioned_routes_require_api_key_when_configured():
    previous = os.environ.get("PIVOTBOSS_API_KEY")
    os.environ["PIVOTBOSS_API_KEY"] = "secret"
    try:
        response = client.get("/api/v1/status")
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid API key"

        response = client.get("/api/v1/status", headers={"X-API-Key": "secret"})
        assert response.status_code == 200
    finally:
        if previous is None:
            os.environ.pop("PIVOTBOSS_API_KEY", None)
        else:
            os.environ["PIVOTBOSS_API_KEY"] = previous
