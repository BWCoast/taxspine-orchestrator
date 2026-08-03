"""Tests for the /health endpoint.

The /health endpoint always returns HTTP 200 (liveness probe semantics).
When CLI binaries, DB, or output dir are unavailable the body carries
"status": "degraded" — callers that need readiness information should
inspect the body rather than the HTTP status code.  Detailed degraded-state
assertions live in test_background_worker.py.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app

client = TestClient(app)


def test_health_returns_status_field() -> None:
    """Health endpoint always returns a JSON body with a 'status' field."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded")
    assert "db" in body
    assert "output_dir" in body
