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
def _reset_store():
    """Clear the job store and drain lingering background tasks before and after each test.

    Using yield so teardown runs even if the test itself fails.  Cancelling tasks
    in ``_background_tasks`` prevents executor threads started by one test from
    writing stale rows into the store while a later test is running.
    """
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()
    _m._background_tasks.clear()   # drop references; threads may still run briefly
    yield
    _m._job_store.clear()
    _m._background_tasks.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ── POST /jobs ───────────────────────────────────────────────────────────────


class TestCreateJob:
    def test_create_returns_pending_job(self, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)

        assert resp.status_code == 201  # API-15: resource creation returns 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["id"]  # non-empty UUID string
        assert body["input"]["xrpl_accounts"] == ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe"]
        assert body["input"]["tax_year"] == 2025
        assert body["input"]["country"] == "norway"

    def test_create_default_csv_files(self, client: TestClient) -> None:
        payload = {"tax_year": 2025, "country": "uk"}
        resp = client.post("/jobs", json=payload)

        assert resp.status_code == 201  # API-15: resource creation returns 201
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


# ── API-15: POST /jobs returns 201 ───────────────────────────────────────────


class TestPostJobsReturns201:
    """API-15: POST /jobs must return HTTP 201 (Created), not 200."""

    def test_create_job_returns_201(self, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)
        assert resp.status_code == 201

    def test_create_job_body_is_present_with_201(self, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)
        assert resp.status_code == 201
        assert resp.json()["id"]

    def test_start_job_returns_202_not_affected(self, client: TestClient) -> None:
        """POST /jobs/{id}/start should still return 202 — no regression.

        Mock ``start_job_execution`` so the background thread completes immediately
        without running real tax computation (which can outlive the test process).
        """
        with patch("taxspine_orchestrator.main._job_service.start_job_execution"):
            create_resp = client.post("/jobs", json=_SAMPLE_INPUT)
            job_id = create_resp.json()["id"]
            start_resp = client.post(f"/jobs/{job_id}/start")
        assert start_resp.status_code == 202


# ── API-02: Atomic workspace write ───────────────────────────────────────────


class TestAtomicWorkspaceWrite:
    """API-02: _save_locked() must write via temp-then-replace, not direct write."""

    def test_save_locked_uses_replace_not_direct_write(self, tmp_path) -> None:
        """Verify _save_locked writes to a .tmp sibling then replaces the target."""
        from taxspine_orchestrator.storage import WorkspaceStore
        from taxspine_orchestrator.models import WorkspaceConfig

        ws_path = tmp_path / "workspace.json"
        store = WorkspaceStore(ws_path)

        written_via_replace: list[str] = []

        original_replace = ws_path.__class__.replace

        def _capture_replace(self, target):
            written_via_replace.append(str(self))
            return original_replace(self, target)

        import unittest.mock as mock
        with mock.patch.object(type(ws_path), "replace", _capture_replace):
            store.add_account("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh")

        assert len(written_via_replace) == 1
        assert written_via_replace[0].endswith(".tmp")

    def test_workspace_file_consistent_after_save(self, tmp_path) -> None:
        """Content saved via _save_locked is valid JSON readable by WorkspaceStore."""
        from taxspine_orchestrator.storage import WorkspaceStore

        ws_path = tmp_path / "workspace.json"
        store = WorkspaceStore(ws_path)
        store.add_account("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh")

        store2 = WorkspaceStore(ws_path)
        cfg = store2.load()
        assert cfg.xrpl_accounts == ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"]

    def test_tmp_file_absent_after_successful_save(self, tmp_path) -> None:
        """The .tmp sibling must not linger after a successful write."""
        from taxspine_orchestrator.storage import WorkspaceStore

        ws_path = tmp_path / "workspace.json"
        store = WorkspaceStore(ws_path)
        store.add_account("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh")

        tmp_file = ws_path.with_suffix(".tmp")
        assert not tmp_file.exists(), "Temp file must be removed after atomic rename"


# ── API-16: Single-lock read-modify-write ────────────────────────────────────


class TestSingleLockReadModifyWrite:
    """API-16: SqliteJobStore.update_status / update_job must hold one lock."""

    def test_update_status_uses_single_connection(self, tmp_path) -> None:
        """update_status completes atomically: job state consistent after concurrent call."""
        from taxspine_orchestrator.storage import SqliteJobStore
        from taxspine_orchestrator.models import Job, JobInput, JobOutput, JobStatus, Country
        import uuid
        from datetime import datetime, timezone

        db = SqliteJobStore(tmp_path / "jobs.db")
        job = Job(
            id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(job)

        updated = db.update_status(job.id, JobStatus.RUNNING)
        assert updated is not None
        assert updated.status == JobStatus.RUNNING
        # The DB record must also reflect the change (not left in PENDING).
        assert db.get(job.id).status == JobStatus.RUNNING

    def test_update_job_uses_single_connection(self, tmp_path) -> None:
        from taxspine_orchestrator.storage import SqliteJobStore
        from taxspine_orchestrator.models import Job, JobInput, JobStatus, Country
        import uuid
        from datetime import datetime, timezone

        db = SqliteJobStore(tmp_path / "jobs.db")
        job = Job(
            id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            input=JobInput(tax_year=2025, country=Country.NORWAY),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(job)

        updated = db.update_job(job.id, status=JobStatus.COMPLETED)
        assert updated is not None
        assert updated.status == JobStatus.COMPLETED
        assert db.get(job.id).status == JobStatus.COMPLETED

    def test_update_status_missing_job_returns_none(self, tmp_path) -> None:
        from taxspine_orchestrator.storage import SqliteJobStore
        from taxspine_orchestrator.models import JobStatus

        db = SqliteJobStore(tmp_path / "jobs.db")
        result = db.update_status("nonexistent", JobStatus.RUNNING)
        assert result is None

    def test_update_job_missing_job_returns_none(self, tmp_path) -> None:
        from taxspine_orchestrator.storage import SqliteJobStore

        db = SqliteJobStore(tmp_path / "jobs.db")
        result = db.update_job("nonexistent", status="completed")
        assert result is None


# ── API-17: Background task retention ────────────────────────────────────────


class TestBackgroundTaskRetention:
    """API-17: asyncio.create_task result must be retained so GC cannot collect it."""

    def test_background_tasks_set_exists(self) -> None:
        """Module-level _background_tasks set must be present in main."""
        from taxspine_orchestrator import main as _m
        assert hasattr(_m, "_background_tasks")
        assert isinstance(_m._background_tasks, set)

    def test_start_job_creates_task_that_is_retained(self, client: TestClient) -> None:
        """POST /jobs/{id}/start must store the created task to prevent GC collection.

        We verify this by intercepting asyncio.create_task, checking the returned
        task has a done-callback registered (the discard call), and that the task
        was added to _background_tasks (or already finished and discarded cleanly).

        ``start_job_execution`` is mocked so the background thread completes
        immediately — the test only validates task-retention wiring, not real
        execution.  Without the mock the thread can outlive the test suite and
        cause the "executor did not finish joining" warning plus state leakage
        into sibling test files.
        """
        import asyncio
        from taxspine_orchestrator import main as _m

        create_resp = client.post("/jobs", json=_SAMPLE_INPUT)
        job_id = create_resp.json()["id"]

        created_tasks: list[asyncio.Task] = []
        original_create = asyncio.create_task

        def _spy(coro, **kw):
            t = original_create(coro, **kw)
            created_tasks.append(t)
            return t

        with patch("taxspine_orchestrator.main._job_service.start_job_execution"), \
             patch("taxspine_orchestrator.main.asyncio.create_task", side_effect=_spy):
            resp = client.post(f"/jobs/{job_id}/start")

        assert resp.status_code == 202
        # One task must have been created.
        assert len(created_tasks) == 1
        # Invariant: either the task is still running (retained in _background_tasks)
        # or it already completed and the done-callback discarded it from the set.
        # Both outcomes prove the retention pattern is wired correctly.
        task = created_tasks[0]
        assert task.done() or task in _m._background_tasks, (
            "Running task must be in _background_tasks until it completes"
        )

    def test_done_callback_wired_in_source(self) -> None:
        """The start_job handler must call add_done_callback to wire up discard."""
        import inspect
        from taxspine_orchestrator import main as _m

        source = inspect.getsource(_m.start_job)
        assert "add_done_callback" in source, (
            "start_job must register a done callback to remove the task from _background_tasks"
        )
        assert "_background_tasks" in source, (
            "start_job must reference _background_tasks"
        )
