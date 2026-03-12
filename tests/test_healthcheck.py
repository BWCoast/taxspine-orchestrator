"""Tests for the /health endpoint.

The improved /health endpoint checks DB, output dir, and CLI binaries.
In the test environment CLIs are typically not installed, so the endpoint
may return 503 with status "degraded".  The test_background_worker.py
module tests the health endpoint in more detail.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app

client = TestClient(app)


def test_health_returns_status_field() -> None:
    """Health endpoint always returns a JSON body with a 'status' field."""
    resp = client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded")
    assert "db" in body
    assert "output_dir" in body
