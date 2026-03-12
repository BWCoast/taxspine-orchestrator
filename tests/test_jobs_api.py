"""Tests for the /jobs endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from tests.conftest import start_and_wait

# ── Fixtures ─────────────────────────────────────────────────────────────────

_SAMPLE_INPUT = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe"],
    "tax_year": 2025,
    "country": "norway",
    "csv_files": [],
}


def _ok_subprocess(*_args, **_kwargs):
    """Fake subprocess.run that always returns rc=0."""
    from unittest.mock import MagicMock

    result = MagicMock()
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    return result


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Clear the in-memory store between tests so they don't leak state."""
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ── POST /jobs ───────────────────────────────────────────────────────────────


class TestCreateJob:
    def test_create_returns_pending_job(self, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["id"]  # non-empty UUID string
        assert body["input"]["xrpl_accounts"] == ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe"]
        assert body["input"]["tax_year"] == 2025
        assert body["input"]["country"] == "norway"

    def test_create_default_csv_files(self, client: TestClient) -> None:
        payload = {"tax_year": 2025, "country": "uk"}
        resp = client.post("/jobs", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["input"]["csv_files"] == []
        assert body["input"]["xrpl_accounts"] == []

    def test_create_output_slots_initially_empty(self, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)

        body = resp.json()
        out = body["output"]
        assert out["gains_csv_path"] is None
        assert out["wealth_csv_path"] is None
        assert out["summary_json_path"] is None
        assert out["log_path"] is None
        assert out["error_message"] is None


# ── GET /jobs ────────────────────────────────────────────────────────────────


class TestListJobs:
    def test_list_initially_empty(self, client: TestClient) -> None:
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/jobs", json=_SAMPLE_INPUT)
        client.post("/jobs", json=_SAMPLE_INPUT)

        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ── GET /jobs/{job_id} ───────────────────────────────────────────────────────


class TestGetJob:
    def test_get_existing_job(self, client: TestClient) -> None:
        create_resp = client.post("/jobs", json=_SAMPLE_INPUT)
        job_id = create_resp.json()["id"]

        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    def test_get_nonexistent_job_returns_404(self, client: TestClient) -> None:
        resp = client.get("/jobs/does-not-exist")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Job not found"


# ── POST /jobs/{job_id}/start ────────────────────────────────────────────────


class TestStartJob:
    @patch("taxspine_orchestrator.services.subprocess.run", side_effect=_ok_subprocess)
    def test_start_completes_job(self, mock_run, client: TestClient) -> None:
        create_resp = client.post("/jobs", json=_SAMPLE_INPUT)
        job_id = create_resp.json()["id"]

        body = start_and_wait(client, job_id)
        assert body["status"] == "completed"

    def test_start_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = client.post("/jobs/does-not-exist/start")
        assert resp.status_code == 404

    @patch("taxspine_orchestrator.services.subprocess.run", side_effect=_ok_subprocess)
    def test_get_after_start_shows_completed(
        self, mock_run, client: TestClient,
    ) -> None:
        create_resp = client.post("/jobs", json=_SAMPLE_INPUT)
        job_id = create_resp.json()["id"]
        start_and_wait(client, job_id)

        resp = client.get(f"/jobs/{job_id}")
        assert resp.json()["status"] == "completed"
