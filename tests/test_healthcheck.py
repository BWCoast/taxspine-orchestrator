"""Tests for the /health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
