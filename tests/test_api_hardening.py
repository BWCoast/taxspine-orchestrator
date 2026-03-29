"""Tests for Batch 11 API hardening findings.

API-03 — /workspace/run must not block the event loop (async + asyncio.to_thread)
API-04 — Start-job race: duplicate-start must be rejected via CAS
API-05 — Cancel uses CANCELLED status (not FAILED)
API-06 — UI job-list limit must be ≤200 (not 500)
API-07 — Cancel-during-execution: CANCELLED state must not be overwritten by COMPLETED
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import JobStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_job(client, *, case_name: str = "test", csv_files=None) -> dict:
    body: dict = {"tax_year": 2025, "country": "norway", "case_name": case_name}
    if csv_files is not None:
        body["csv_files"] = csv_files
    resp = client.post("/jobs", json=body)
    assert resp.status_code == 201, resp.json()
    return resp.json()


# ── API-03: workspace/run must be async ───────────────────────────────────────


class TestApi03WorkspaceRunAsync:
    """/workspace/run must offload blocking work to the thread pool."""

    def test_run_workspace_report_is_coroutine(self) -> None:
        """run_workspace_report must be an async function (coroutine function)."""
        import inspect
        from taxspine_orchestrator.main import run_workspace_report
        assert inspect.iscoroutinefunction(run_workspace_report), (
            "run_workspace_report must be declared 'async def' (API-03)"
        )

    def test_source_code_uses_asyncio_to_thread(self) -> None:
        """Static check: main.py must use BackgroundTasks in run_workspace_report.

        B-2 changed /workspace/run from asyncio.to_thread (still blocks the
        HTTP response) to FastAPI BackgroundTasks (true fire-and-forget).
        The endpoint must use background_tasks.add_task so the HTTP response
        is returned immediately with a PENDING job.
        """
        main_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        src = main_py.read_text(encoding="utf-8")
        start = src.find("async def run_workspace_report(")
        assert start >= 0, "run_workspace_report must exist and be async"
        # Read up to next @app. decorator.
        next_fn = src.find("\n@app.", start + 1)
        snippet = src[start: next_fn] if next_fn > 0 else src[start: start + 2000]
        assert "background_tasks.add_task" in snippet, (
            "run_workspace_report must use background_tasks.add_task to offload "
            "blocking start_job_execution and return immediately (B-2 / API-03)"
        )

    def test_workspace_run_returns_job(self, client: TestClient, tmp_path: Path) -> None:
        """Integration: /workspace/run returns immediately with a PENDING job (B-2).

        After the B-2 fix, /workspace/run is fire-and-forget: the HTTP response
        carries the newly-created PENDING job record and the pipeline runs in the
        background.  Callers must poll GET /jobs/{id} for the final status.
        """
        dummy_csv = tmp_path / "events.csv"
        dummy_csv.write_text(
            "event_id,timestamp,event_type,source,account,asset_in,amount_in,"
            "asset_out,amount_out,fee_asset,fee_amount,tx_hash,exchange_tx_id,label,"
            "complex_tax_treatment,note\n",
            encoding="utf-8",
        )
        from taxspine_orchestrator import main as _m
        from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType
        _m._workspace_store.add_csv(CsvFileSpec(path=str(dummy_csv)))

        resp = client.post(
            "/workspace/run",
            json={"tax_year": 2025, "country": "norway"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Fire-and-forget: response must carry the job before execution completes.
        assert "id" in body
        assert body["status"] in ("pending", "running", "completed", "failed")


# ── API-04: Start-job duplicate-start prevention ──────────────────────────────


class TestApi04StartJobCas:
    """POST /jobs/{id}/start must use CAS to prevent duplicate starts."""

    def test_start_nonexistent_job_returns_404(self, client: TestClient) -> None:
        resp = client.post("/jobs/nonexistent-id/start")
        assert resp.status_code == 404

    def test_start_pending_job_returns_202(self, client: TestClient) -> None:
        job = _create_job(client)
        with patch(
            "taxspine_orchestrator.main._job_service.start_job_execution",
            return_value=MagicMock(status=JobStatus.COMPLETED),
        ):
            resp = client.post(f"/jobs/{job['id']}/start")
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

    def test_start_already_running_returns_409(self, client: TestClient) -> None:
        """Once running, a second start attempt must be rejected with 409."""
        from taxspine_orchestrator import main as _m
        job = _create_job(client)
        # Force the job into RUNNING state.
        _m._job_store.update_status(job["id"], JobStatus.RUNNING)

        resp = client.post(f"/jobs/{job['id']}/start")
        assert resp.status_code == 409, (
            "Second start attempt on a RUNNING job must return 409 (API-04)"
        )

    def test_cas_transition_in_storage_source(self) -> None:
        """Static check: storage.py must define transition_status for CAS."""
        storage_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "storage.py"
        src = storage_py.read_text(encoding="utf-8")
        assert "def transition_status(" in src, (
            "SqliteJobStore must implement transition_status (API-04)"
        )

    def test_cas_used_in_start_job_endpoint(self) -> None:
        """Static check: main.py start_job must call transition_status."""
        main_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        src = main_py.read_text(encoding="utf-8")
        start = src.find("async def start_job(")
        assert start >= 0
        next_fn = src.find("\n@app.", start + 1)
        snippet = src[start: next_fn] if next_fn > 0 else src[start: start + 2000]
        assert "transition_status" in snippet, (
            "start_job endpoint must use transition_status CAS (API-04)"
        )

    def test_in_memory_store_transition_status_cas(self) -> None:
        """Unit: InMemoryJobStore.transition_status respects from_status guard."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.models import Job, JobInput, JobOutput, JobStatus, Country
        from datetime import datetime, timezone

        store = InMemoryJobStore()
        now = datetime.now(timezone.utc)
        job = Job(
            id="j1",
            status=JobStatus.PENDING,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            output=JobOutput(),
            created_at=now,
            updated_at=now,
        )
        store.add(job)

        # CAS from PENDING → RUNNING should succeed.
        result = store.transition_status("j1", JobStatus.PENDING, JobStatus.RUNNING)
        assert result is not None
        assert result.status == JobStatus.RUNNING

        # A second CAS from PENDING → RUNNING must fail (already RUNNING).
        result2 = store.transition_status("j1", JobStatus.PENDING, JobStatus.RUNNING)
        assert result2 is None, (
            "Second CAS from PENDING must fail when job is already RUNNING"
        )

    def test_sqlite_store_transition_status_cas(self, tmp_path: Path) -> None:
        """Unit: SqliteJobStore.transition_status respects from_status guard."""
        from taxspine_orchestrator.storage import SqliteJobStore
        from taxspine_orchestrator.models import Job, JobInput, JobOutput, JobStatus, Country
        from datetime import datetime, timezone

        store = SqliteJobStore(tmp_path / "cas_test.db")
        now = datetime.now(timezone.utc)
        job = Job(
            id="j2",
            status=JobStatus.PENDING,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            output=JobOutput(),
            created_at=now,
            updated_at=now,
        )
        store.add(job)

        result = store.transition_status("j2", JobStatus.PENDING, JobStatus.RUNNING)
        assert result is not None
        assert result.status == JobStatus.RUNNING

        # Concurrent second attempt must fail.
        result2 = store.transition_status("j2", JobStatus.PENDING, JobStatus.RUNNING)
        assert result2 is None


# ── API-05: CANCELLED distinct from FAILED ────────────────────────────────────


class TestApi05CancelledStatus:
    """POST /jobs/{id}/cancel must use CANCELLED, not FAILED."""

    def test_cancel_pending_job_sets_cancelled(self, client: TestClient) -> None:
        job = _create_job(client)
        resp = client.post(f"/jobs/{job['id']}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        # Verify the stored status.
        get_resp = client.get(f"/jobs/{job['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "cancelled", (
            "cancel_job must set status to 'cancelled', not 'failed' (API-05)"
        )

    def test_cancel_running_job_sets_cancelled(self, client: TestClient) -> None:
        from taxspine_orchestrator import main as _m
        job = _create_job(client)
        _m._job_store.update_status(job["id"], JobStatus.RUNNING)

        resp = client.post(f"/jobs/{job['id']}/cancel")
        assert resp.status_code == 200

        get_resp = client.get(f"/jobs/{job['id']}")
        assert get_resp.json()["status"] == "cancelled"

    def test_cancel_completed_job_returns_400(self, client: TestClient) -> None:
        from taxspine_orchestrator import main as _m
        job = _create_job(client)
        _m._job_store.update_status(job["id"], JobStatus.COMPLETED)

        resp = client.post(f"/jobs/{job['id']}/cancel")
        assert resp.status_code == 400

    def test_cancel_already_cancelled_returns_400(self, client: TestClient) -> None:
        from taxspine_orchestrator import main as _m
        job = _create_job(client)
        _m._job_store.update_status(job["id"], JobStatus.CANCELLED)

        resp = client.post(f"/jobs/{job['id']}/cancel")
        assert resp.status_code == 400

    def test_cancelled_status_in_models(self) -> None:
        """JobStatus enum must include CANCELLED."""
        assert JobStatus.CANCELLED.value == "cancelled", (
            "JobStatus.CANCELLED must equal 'cancelled' (API-05)"
        )

    def test_cancel_uses_cancelled_not_failed_in_source(self) -> None:
        """Static check: cancel_job must use JobStatus.CANCELLED."""
        main_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        src = main_py.read_text(encoding="utf-8")
        start = src.find("async def cancel_job(")
        assert start >= 0
        next_fn = src.find("\n@app.", start + 1)
        snippet = src[start: next_fn] if next_fn > 0 else src[start: start + 2000]
        assert "JobStatus.CANCELLED" in snippet, (
            "cancel_job must use JobStatus.CANCELLED (not FAILED) (API-05)"
        )
        assert "JobStatus.FAILED" not in snippet, (
            "cancel_job must not use JobStatus.FAILED (API-05)"
        )


# ── API-06: UI job-list limit ≤ 200 ──────────────────────────────────────────


class TestApi06UiJobListLimit:
    """UI must request at most 200 jobs, not 500."""

    def test_ui_does_not_fetch_500_jobs(self) -> None:
        """Static check: index.html must not contain limit=500."""
        index_html = Path(__file__).parent.parent / "ui" / "index.html"
        assert index_html.is_file(), "ui/index.html must exist"
        src = index_html.read_text(encoding="utf-8")
        assert "limit=500" not in src, (
            "UI must not request limit=500 from the jobs API — "
            "the server enforces le=200 (API-06)"
        )

    def test_ui_fetches_at_most_200_jobs(self) -> None:
        """Static check: UI job fetch calls must use limit=200 or less."""
        index_html = Path(__file__).parent.parent / "ui" / "index.html"
        src = index_html.read_text(encoding="utf-8")
        # Both fetch calls must use limit=200.
        assert "limit=200" in src, (
            "UI must use limit=200 when fetching jobs (API-06)"
        )

    def test_server_rejects_limit_above_200(self, client: TestClient) -> None:
        """The server-side endpoint enforces le=200 via Query validation."""
        resp = client.get("/jobs?limit=500")
        assert resp.status_code == 422, (
            "Server must reject limit=500 with 422 Unprocessable Entity (API-06)"
        )


# ── API-07: Cancel-during-execution CANCELLED not overwritten ─────────────────


class TestApi07CancelDuringExecution:
    """CANCELLED state must survive a concurrent execution thread setting COMPLETED."""

    def test_cancelled_not_overwritten_by_completed_in_services_source(self) -> None:
        """Static check: services.py must guard CANCELLED before setting COMPLETED."""
        services_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "services.py"
        src = services_py.read_text(encoding="utf-8")
        assert "JobStatus.CANCELLED" in src, (
            "services.py must reference JobStatus.CANCELLED to guard against "
            "overwriting cancel (API-07)"
        )

    def test_fail_job_does_not_overwrite_cancelled(self) -> None:
        """Unit: _fail_job must not overwrite CANCELLED with FAILED."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.services import JobService
        from taxspine_orchestrator.models import Job, JobInput, JobOutput, JobStatus, Country
        from datetime import datetime, timezone
        import tempfile

        store = InMemoryJobStore()
        now = datetime.now(timezone.utc)
        job = Job(
            id="j-cancel",
            status=JobStatus.CANCELLED,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            output=JobOutput(),
            created_at=now,
            updated_at=now,
        )
        store.add(job)

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = JobService(store)
            # Patch output dir to avoid real filesystem dependency.
            with patch.object(svc, "_job_output_dir", return_value=Path(tmpdir)):
                result = svc._fail_job(
                    "j-cancel",
                    error="test error",
                    log_lines=[],
                    output_dir=Path(tmpdir),
                )

        # Must preserve CANCELLED — not overwrite with FAILED.
        assert result is not None
        assert result.status == JobStatus.CANCELLED, (
            "_fail_job must not overwrite CANCELLED with FAILED (API-07)"
        )

    def test_start_job_execution_preserves_cancelled_state(self) -> None:
        """Unit: start_job_execution must return early if job is CANCELLED."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.services import JobService
        from taxspine_orchestrator.models import Job, JobInput, JobOutput, JobStatus, Country
        from datetime import datetime, timezone

        store = InMemoryJobStore()
        now = datetime.now(timezone.utc)
        job = Job(
            id="j-cancelled",
            status=JobStatus.CANCELLED,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            output=JobOutput(),
            created_at=now,
            updated_at=now,
        )
        store.add(job)

        svc = JobService(store)
        result = svc.start_job_execution("j-cancelled")
        # Must return the job as-is — not try to execute it.
        assert result is not None
        assert result.status == JobStatus.CANCELLED, (
            "start_job_execution must return early for CANCELLED jobs (API-07)"
        )
        # The store must still show CANCELLED.
        stored = store.get("j-cancelled")
        assert stored.status == JobStatus.CANCELLED
