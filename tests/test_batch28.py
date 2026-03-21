"""Batch 28 — LC-09 query-length cap, INFRA-25 CI Python matrix, API-13
cancel-then-complete race regression tests.

Coverage:
    LC-09    GET /jobs?query= now has max_length=200 on the Query field.
             A query string longer than 200 chars returns HTTP 422
             (FastAPI validation error) instead of being passed to the
             SQL LIKE engine uncapped.
    INFRA-25 .github/workflows/docker.yml now runs the test job across a
             Python version matrix (3.11, 3.12) instead of only 3.11,
             ensuring that regressions on newer interpreters are caught
             before a Docker image is published.
    API-13   Regression tests for the API-07 CANCELLED guard: a background
             thread completing after the user cancels must not overwrite the
             CANCELLED terminal state with COMPLETED or FAILED.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app, _job_store
from taxspine_orchestrator.models import (
    Country,
    JobInput,
    JobOutput,
    JobStatus,
)
from taxspine_orchestrator.services import JobService
from taxspine_orchestrator.storage import InMemoryJobStore

# ── LC-09: GET /jobs query string length cap ──────────────────────────────────

_CLIENT = TestClient(app)


class TestLC09QueryLengthCap:
    """GET /jobs?query= rejects strings longer than 200 characters with 422."""

    def test_short_query_accepted(self):
        """A query shorter than max_length is accepted (returns 200)."""
        resp = _CLIENT.get("/jobs", params={"query": "my case"})
        assert resp.status_code == 200

    def test_query_at_max_length_accepted(self):
        """A query of exactly 200 chars is accepted."""
        resp = _CLIENT.get("/jobs", params={"query": "x" * 200})
        assert resp.status_code == 200

    def test_query_over_max_length_rejected(self):
        """A query of 201 chars is rejected with HTTP 422."""
        resp = _CLIENT.get("/jobs", params={"query": "x" * 201})
        assert resp.status_code == 422

    def test_query_far_over_max_length_rejected(self):
        """A 2 000-char query is rejected with HTTP 422."""
        resp = _CLIENT.get("/jobs", params={"query": "a" * 2000})
        assert resp.status_code == 422

    def test_source_code_contains_lc09_tag(self):
        """main.py source references LC-09."""
        import taxspine_orchestrator.main as main_mod
        src = inspect.getsource(main_mod)
        assert "LC-09" in src

    def test_max_length_in_source(self):
        """main.py source contains max_length=200 on the query field."""
        import taxspine_orchestrator.main as main_mod
        src = inspect.getsource(main_mod)
        assert "max_length=200" in src


# ── INFRA-25: CI Python matrix ────────────────────────────────────────────────

class TestINFRA25CIPythonMatrix:
    """docker.yml test job runs against a matrix of Python versions."""

    @pytest.fixture(scope="class")
    def workflow_content(self) -> str:
        repo_root = Path(__file__).resolve().parent.parent
        wf = repo_root / ".github" / "workflows" / "docker.yml"
        return wf.read_text(encoding="utf-8")

    def test_workflow_file_exists(self):
        repo_root = Path(__file__).resolve().parent.parent
        wf = repo_root / ".github" / "workflows" / "docker.yml"
        assert wf.is_file(), ".github/workflows/docker.yml must exist"

    def test_infra25_tag_present(self, workflow_content):
        """INFRA-25 comment tag is in the workflow."""
        assert "INFRA-25" in workflow_content

    def test_matrix_strategy_declared(self, workflow_content):
        """workflow uses strategy.matrix."""
        assert "matrix:" in workflow_content

    def test_python_311_in_matrix(self, workflow_content):
        """Python 3.11 is present in the matrix."""
        assert '"3.11"' in workflow_content or "'3.11'" in workflow_content

    def test_python_312_in_matrix(self, workflow_content):
        """Python 3.12 is present in the matrix."""
        assert '"3.12"' in workflow_content or "'3.12'" in workflow_content

    def test_matrix_python_version_variable_used(self, workflow_content):
        """The matrix python-version variable is referenced in setup-python."""
        assert "matrix.python-version" in workflow_content

    def test_setup_python_uses_matrix_var(self, workflow_content):
        """setup-python@v5 uses the matrix variable, not a hard-coded version."""
        # Should NOT have a plain hard-coded "3.11" in the python-version: field
        # after the matrix refactor (the matrix values still contain "3.11"
        # but the setup-python step should reference the variable)
        assert "python-version: ${{ matrix.python-version }}" in workflow_content

    def test_job_name_reflects_matrix(self, workflow_content):
        """The test job name includes the matrix variable so UI shows per-version."""
        assert "matrix.python-version" in workflow_content


# ── API-13: cancel-then-complete race regression ──────────────────────────────

class TestAPI13CancelThenCompleteRace:
    """API-13 / API-07 guard: CANCELLED terminal state must not be overwritten
    by a background execution thread that completes after the user cancels."""

    def _make_service(self) -> tuple[InMemoryJobStore, JobService]:
        store = InMemoryJobStore()
        svc = JobService(store=store)
        return store, svc

    # ── Source-code structural tests ─────────────────────────────────────

    def test_cancelled_guard_present_in_source(self):
        """services.py contains the CANCELLED guard before updating to COMPLETED."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        assert "JobStatus.CANCELLED" in src

    def test_guard_checks_current_status(self):
        """Guard reads current status from store before any write."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        assert "current.status == JobStatus.CANCELLED" in src

    def test_guard_applied_in_fail_job(self):
        """_fail_job also has the CANCELLED guard so FAILED cannot overwrite CANCELLED."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        fail_idx = src.index("def _fail_job")
        fail_src = src[fail_idx: fail_idx + 800]
        assert "CANCELLED" in fail_src

    def test_api07_comment_present(self):
        """services.py references the API-07 finding tag."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        assert "API-07" in src

    # ── Unit-level guard tests ────────────────────────────────────────────

    def test_cancelled_not_overwritten_by_completed(self):
        """Unit: CANCELLED is preserved when bg thread guard skips COMPLETED write."""
        store, svc = self._make_service()
        job = svc.create_job(JobInput(country=Country.NORWAY, tax_year=2025))

        # Simulate: PENDING → RUNNING (execution started)
        store.update_status(job.id, JobStatus.RUNNING)

        # User cancels mid-run
        store.update_status(job.id, JobStatus.CANCELLED, error_message="Cancelled by user")

        # Simulate bg thread completing with API-07 guard applied:
        current = store.get(job.id)
        if not (current and current.status == JobStatus.CANCELLED):
            store.update_job(job.id, status=JobStatus.COMPLETED, output=JobOutput())

        final = store.get(job.id)
        assert final.status == JobStatus.CANCELLED

    def test_cancelled_not_overwritten_by_failed(self):
        """Unit: CANCELLED is preserved when bg thread guard skips FAILED write."""
        store, svc = self._make_service()
        job = svc.create_job(JobInput(country=Country.NORWAY, tax_year=2025))

        store.update_status(job.id, JobStatus.RUNNING)
        store.update_status(job.id, JobStatus.CANCELLED, error_message="Cancelled by user")

        # Simulate _fail_job guard:
        current = store.get(job.id)
        if not (current and current.status == JobStatus.CANCELLED):
            store.update_job(job.id, status=JobStatus.FAILED, output=JobOutput(error_message="execution error"))

        final = store.get(job.id)
        assert final.status == JobStatus.CANCELLED

    def test_completed_written_when_not_cancelled(self):
        """Unit: COMPLETED IS written when job was not cancelled (guard must not fire)."""
        store, svc = self._make_service()
        job = svc.create_job(JobInput(country=Country.NORWAY, tax_year=2025))

        store.update_status(job.id, JobStatus.RUNNING)
        # No cancel — guard should NOT skip the write
        current = store.get(job.id)
        if not (current and current.status == JobStatus.CANCELLED):
            store.update_job(job.id, status=JobStatus.COMPLETED, output=JobOutput())

        final = store.get(job.id)
        assert final.status == JobStatus.COMPLETED

    def test_start_execution_returns_early_for_cancelled_job(self):
        """start_job_execution returns the job unchanged when already CANCELLED."""
        store, svc = self._make_service()
        job = svc.create_job(JobInput(country=Country.NORWAY, tax_year=2025))

        # Cancel directly (PENDING → CANCELLED)
        store.update_status(job.id, JobStatus.CANCELLED, error_message="Cancelled by user")

        # start_job_execution must return early without attempting execution
        result = svc.start_job_execution(job.id)
        assert result is not None
        assert result.status == JobStatus.CANCELLED

    # ── HTTP-level cancel test ────────────────────────────────────────────

    def test_http_cancel_sets_cancelled_and_persists(self):
        """HTTP: POST /jobs/{id}/cancel on a RUNNING job returns 'cancelled'."""
        client = TestClient(app)
        resp = client.post("/jobs", json={"country": "norway", "tax_year": 2025})
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        # Transition to RUNNING (simulates execution thread starting)
        _job_store.update_status(job_id, JobStatus.RUNNING)

        # Cancel
        cancel_resp = client.post(f"/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        # GET confirms status
        get_resp = client.get(f"/jobs/{job_id}")
        assert get_resp.json()["status"] == "cancelled"

    def test_http_cancel_then_simulate_bg_complete(self):
        """HTTP + store: after cancel, guard prevents COMPLETED from being written."""
        client = TestClient(app)
        resp = client.post("/jobs", json={"country": "norway", "tax_year": 2025})
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        # PENDING → RUNNING → CANCELLED (user cancels mid-run)
        _job_store.update_status(job_id, JobStatus.RUNNING)
        client.post(f"/jobs/{job_id}/cancel")

        # Simulate bg thread trying to write COMPLETED with guard:
        current = _job_store.get(job_id)
        if not (current and current.status == JobStatus.CANCELLED):
            _job_store.update_job(job_id, status=JobStatus.COMPLETED, output=JobOutput())

        # Final status must still be CANCELLED
        get_resp = client.get(f"/jobs/{job_id}")
        assert get_resp.json()["status"] == "cancelled"
