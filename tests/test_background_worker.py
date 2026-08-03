"""Tests for P3-B: async background execution, crash recovery, health, and cancel.

Covers:
- POST /jobs/{id}/start returns 202 immediately; job completes in background.
- Starting an already-RUNNING job returns 409.
- Crash recovery: RUNNING jobs are marked FAILED on SqliteJobStore.__init__.
- Improved health endpoint checks DB, output_dir, and CLI binaries.
- POST /jobs/{id}/cancel endpoint behaviour.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import Job, JobInput, JobOutput, JobStatus, Country
from taxspine_orchestrator.storage import SqliteJobStore
from tests.conftest import start_and_wait


# ── Helpers ───────────────────────────────────────────────────────────────────


_NORWAY_INPUT = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}


def _make_ok(**overrides) -> MagicMock:
    m = MagicMock()
    m.returncode = overrides.get("returncode", 0)
    m.stdout = overrides.get("stdout", "")
    m.stderr = overrides.get("stderr", "")
    return m


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ── TestAsyncJobStart ─────────────────────────────────────────────────────────


class TestAsyncJobStart:
    """POST /jobs/{id}/start returns 202 and the job runs in the background."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_start_returns_202_immediately(self, mock_run, client: TestClient) -> None:
        """POST /jobs/{id}/start returns 202 Accepted without blocking."""
        mock_run.return_value = _make_ok()

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        start_resp = client.post(f"/jobs/{job_id}/start")
        assert start_resp.status_code == 202
        body = start_resp.json()
        assert body["status"] == "accepted"
        assert body["job_id"] == job_id

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_job_completes_after_background_execution(self, mock_run, client: TestClient) -> None:
        """After 202, polling GET /jobs/{id} eventually shows completed."""
        mock_run.return_value = _make_ok()

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        # Start (202) then poll for completion.
        job = start_and_wait(client, job_id)
        assert job["status"] == "completed"
        assert job["output"]["log_path"] is not None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_start_already_running_returns_409(self, mock_run, client: TestClient) -> None:
        """Starting a job that is already RUNNING returns 409 Conflict.

        We force the job into RUNNING state directly in the store before
        calling /start, simulating the window where a background thread
        is already executing.
        """
        mock_run.return_value = _make_ok()

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        # Force the job into RUNNING state.
        from taxspine_orchestrator import main as _m
        _m._job_store.update_status(job_id, JobStatus.RUNNING)

        start_resp = client.post(f"/jobs/{job_id}/start")
        assert start_resp.status_code == 409
        assert "already running" in start_resp.json()["detail"].lower()

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_start_completed_job_returns_200_with_status(self, mock_run, client: TestClient) -> None:
        """Starting a COMPLETED job returns 200 with its current status (idempotent)."""
        mock_run.return_value = _make_ok()

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        # Complete the job first.
        start_and_wait(client, job_id)

        # Second start attempt.
        resp2 = client.post(f"/jobs/{job_id}/start")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "completed"


# ── TestCrashRecovery ─────────────────────────────────────────────────────────


class TestCrashRecovery:
    """SqliteJobStore.__init__ marks RUNNING jobs as FAILED on startup."""

    def test_running_jobs_marked_failed_on_init(self, tmp_path: Path) -> None:
        """Insert a RUNNING job directly, then re-init the store → job is FAILED."""
        db_path = tmp_path / "jobs.db"
        store = SqliteJobStore(db_path)

        # Create a job and force it to RUNNING.
        from datetime import datetime, timezone
        import uuid

        job = Job(
            id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        store.add(job)
        store.update_status(job.id, JobStatus.RUNNING)

        # Verify the job is RUNNING before recovery.
        assert store.get(job.id).status == JobStatus.RUNNING

        # Re-initialise the store — recovery sweep should run.
        store2 = SqliteJobStore(db_path)
        recovered = store2.get(job.id)

        assert recovered.status == JobStatus.FAILED
        assert recovered.output.error_message == "Interrupted by server restart"

    def test_completed_jobs_not_affected_by_recovery(self, tmp_path: Path) -> None:
        """COMPLETED jobs are left untouched by the crash recovery sweep."""
        db_path = tmp_path / "jobs2.db"
        store = SqliteJobStore(db_path)

        from datetime import datetime, timezone
        import uuid

        job = Job(
            id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        store.add(job)
        store.update_job(job.id, status=JobStatus.COMPLETED)

        store2 = SqliteJobStore(db_path)
        assert store2.get(job.id).status == JobStatus.COMPLETED

    def test_pending_jobs_not_affected_by_recovery(self, tmp_path: Path) -> None:
        """PENDING jobs are left untouched by the crash recovery sweep."""
        db_path = tmp_path / "jobs3.db"
        store = SqliteJobStore(db_path)

        from datetime import datetime, timezone
        import uuid

        job = Job(
            id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        store.add(job)

        store2 = SqliteJobStore(db_path)
        assert store2.get(job.id).status == JobStatus.PENDING

    def test_multiple_running_jobs_all_recovered(self, tmp_path: Path) -> None:
        """All RUNNING jobs are recovered in a single sweep."""
        db_path = tmp_path / "jobs4.db"
        store = SqliteJobStore(db_path)

        from datetime import datetime, timezone
        import uuid

        ids = []
        for _ in range(3):
            job = Job(
                id=str(uuid.uuid4()),
                status=JobStatus.PENDING,
                input=JobInput(tax_year=2025, country=Country.NORWAY),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            store.add(job)
            store.update_status(job.id, JobStatus.RUNNING)
            ids.append(job.id)

        store2 = SqliteJobStore(db_path)
        for job_id in ids:
            assert store2.get(job_id).status == JobStatus.FAILED


# ── TestHealthCheck ────────────────────────────────────────────────────────────


class TestHealthCheck:
    """Improved /health endpoint checks DB, output_dir, and CLI binaries."""

    def test_health_returns_200_when_all_ok(self, client: TestClient) -> None:
        """When DB is reachable and output dir is writable, and CLIs exist → 200."""
        # We mock shutil.which so both CLIs appear present.
        import shutil

        def _mock_which(name: str):
            if name in ("taxspine-nor-report", "taxspine-xrpl-nor"):
                return f"/usr/local/bin/{name}"
            return shutil.which(name)

        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which):
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert body["output_dir"] == "ok"
        assert body["taxspine-nor-report"] == "ok"
        assert body["taxspine-xrpl-nor"] == "ok"

    def test_health_returns_degraded_when_output_dir_missing(self, client: TestClient) -> None:
        """When OUTPUT_DIR is not writable, /health returns 503 with degraded status in body.

        INFRA-08: /health now returns HTTP 503 (not 200) when a critical check
        fails so container orchestrators can detect and reroute a degraded pod.
        """
        import shutil

        def _mock_which(name: str):
            if name in ("taxspine-nor-report", "taxspine-xrpl-nor"):
                return f"/usr/local/bin/{name}"
            return shutil.which(name)

        with (
            patch("taxspine_orchestrator.main.os.access", return_value=False),
            patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which),
        ):
            resp = client.get("/health")

        assert resp.status_code == 503  # INFRA-08: critical failure → 503
        body = resp.json()
        assert body["status"] == "degraded"
        # SEC-16: output_dir now returns an opaque "error" string (no detail text).
        assert body["output_dir"] == "error"

    def test_health_db_field_present(self, client: TestClient) -> None:
        """Health response always includes a 'db' field."""
        resp = client.get("/health")
        assert "db" in resp.json()

    def test_health_cli_fields_present(self, client: TestClient) -> None:
        """Health response includes fields for both CLI binaries."""
        resp = client.get("/health")
        body = resp.json()
        assert "taxspine-nor-report" in body
        assert "taxspine-xrpl-nor" in body

    def test_health_returns_degraded_when_cli_missing(self, client: TestClient) -> None:
        """When CLI binaries are not on PATH, /health returns 200 with degraded status in body."""
        with patch("taxspine_orchestrator.main.shutil.which", return_value=None):
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["taxspine-nor-report"] == "missing"
        assert body["taxspine-xrpl-nor"] == "missing"

    def test_health_db_error_returns_degraded(self, client: TestClient) -> None:
        """If the DB ping fails, /health returns 503 with degraded status in body.

        INFRA-08: DB failure is a critical check → HTTP 503 so orchestrators
        detect the pod as unhealthy and stop routing traffic to it.
        """
        from taxspine_orchestrator import main as _m

        def _bad_ping() -> None:
            raise RuntimeError("disk full")

        with (
            patch.object(_m._job_store, "ping", side_effect=_bad_ping),
            patch("taxspine_orchestrator.main.shutil.which", return_value="/bin/x"),
        ):
            resp = client.get("/health")

        assert resp.status_code == 503  # INFRA-08: critical failure → 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert "error" in body["db"]


# ── TestCancelEndpoint ────────────────────────────────────────────────────────


class TestCancelEndpoint:
    """POST /jobs/{id}/cancel endpoint."""

    def test_cancel_pending_job_succeeds(self, client: TestClient) -> None:
        """Cancelling a PENDING job marks it FAILED and returns status='cancelled'."""
        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        cancel_resp = client.post(f"/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 200
        body = cancel_resp.json()
        assert body["status"] == "cancelled"
        assert body["job_id"] == job_id

        # API-05: cancel now sets CANCELLED (not FAILED) — distinct terminal state.
        job = client.get(f"/jobs/{job_id}").json()
        assert job["status"] == "cancelled"
        assert job["output"]["error_message"] == "Cancelled by user"

    def test_cancel_running_job_marks_cancelled(self, client: TestClient) -> None:
        """Cancelling a RUNNING job marks it CANCELLED (API-05)."""
        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        from taxspine_orchestrator import main as _m
        _m._job_store.update_status(job_id, JobStatus.RUNNING)

        cancel_resp = client.post(f"/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        job = client.get(f"/jobs/{job_id}").json()
        assert job["status"] == "cancelled"

    def test_cancel_completed_job_returns_400(self, client: TestClient) -> None:
        """Cancelling a COMPLETED job returns 400 Bad Request."""
        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        from taxspine_orchestrator import main as _m
        _m._job_store.update_job(job_id, status=JobStatus.COMPLETED)

        cancel_resp = client.post(f"/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 400
        assert "Cannot cancel" in cancel_resp.json()["detail"]

    def test_cancel_failed_job_returns_400(self, client: TestClient) -> None:
        """Cancelling an already-FAILED job returns 400 Bad Request."""
        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        from taxspine_orchestrator import main as _m
        _m._job_store.update_job(job_id, status=JobStatus.FAILED)

        cancel_resp = client.post(f"/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 400

    def test_cancel_nonexistent_job_returns_404(self, client: TestClient) -> None:
        """Cancelling a job that does not exist returns 404 Not Found."""
        cancel_resp = client.post("/jobs/does-not-exist/cancel")
        assert cancel_resp.status_code == 404
        assert "Job not found" in cancel_resp.json()["detail"]


# ── TestStartedAtTimestamp ────────────────────────────────────────────────────


class TestStartedAtTimestamp:
    """Job.started_at is set when a job transitions to RUNNING."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_started_at_is_none_before_start(self, mock_run, client: TestClient) -> None:
        """A freshly created PENDING job has started_at=None."""
        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job = resp.json()
        assert job["started_at"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_started_at_is_set_after_completion(self, mock_run, client: TestClient) -> None:
        """After execution, started_at is a non-None ISO timestamp."""
        mock_run.return_value = _make_ok()

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        job = start_and_wait(client, job_id)
        assert job["started_at"] is not None
        # Must be a valid ISO datetime.
        from datetime import datetime
        datetime.fromisoformat(job["started_at"])
