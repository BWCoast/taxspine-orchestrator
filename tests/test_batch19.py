"""Batch 19 — regression tests for API correctness and reproducible builds.

Findings covered
----------------
API-19  GET /jobs returns no total count — clients cannot paginate reliably
API-21  JobOutput dual path fields (singular/plural) can diverge
API-13  Cancel-then-complete race: CANCELLED state must not be overwritten (MISSING TEST)
API-23  /jobs/{id}/reports and /reports/{index} endpoints have no test coverage (MISSING TEST)
INFRA-04  requirements.lock not used in Docker — transitive deps are not pinned
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── shared helpers ────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_REPO = _HERE.parent
_DOCKERFILE_PATH = _REPO / "Dockerfile"
_LOCKFILE_PATH   = _REPO / "requirements.lock"
_SERVICES_PATH   = _REPO / "taxspine_orchestrator" / "services.py"
_MODELS_PATH     = _REPO / "taxspine_orchestrator" / "models.py"


def _dockerfile() -> str:
    return _DOCKERFILE_PATH.read_text(encoding="utf-8")


def _lockfile() -> str:
    return _LOCKFILE_PATH.read_text(encoding="utf-8")


def _services() -> str:
    return _SERVICES_PATH.read_text(encoding="utf-8")


def _models() -> str:
    return _MODELS_PATH.read_text(encoding="utf-8")


# ── API-19: X-Total-Count response header ────────────────────────────────────


class TestAPI19TotalCountHeader:
    """API-19: GET /jobs must return an X-Total-Count header containing the
    total number of matching jobs irrespective of limit/offset."""

    @pytest.fixture(autouse=True)
    def _client(self):
        from taxspine_orchestrator.main import app
        self.client = TestClient(app)

    def test_x_total_count_present_on_empty_store(self):
        """X-Total-Count must be present even when there are no jobs."""
        r = self.client.get("/jobs")
        assert r.status_code == 200
        assert "x-total-count" in {k.lower() for k in r.headers}, (
            "API-19: X-Total-Count header must be present in GET /jobs response"
        )

    def test_x_total_count_is_numeric(self):
        """X-Total-Count must be a non-negative integer string."""
        r = self.client.get("/jobs")
        total = r.headers.get("x-total-count") or r.headers.get("X-Total-Count")
        assert total is not None, "API-19: X-Total-Count header must be present"
        assert total.isdigit(), (
            f"API-19: X-Total-Count must be a non-negative integer string; got {total!r}"
        )

    def test_x_total_count_reflects_created_jobs(self, tmp_path, monkeypatch):
        """X-Total-Count must not be limited by the limit param — it is the total count."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        with TestClient(app) as c:
            # Capture baseline before creating jobs (store shared across tests)
            r_before = c.get("/jobs?limit=1")
            before = int(
                r_before.headers.get("x-total-count") or r_before.headers.get("X-Total-Count") or "0"
            )

            # Create 3 jobs
            body = {"country": "norway", "tax_year": 2025, "xrpl_accounts": [], "csv_files": []}
            for _ in range(3):
                resp = c.post("/jobs", json=body)
                assert resp.status_code == 201

            r = c.get("/jobs?limit=1")
            assert r.status_code == 200
            total = int(
                r.headers.get("x-total-count") or r.headers.get("X-Total-Count") or "0"
            )
            # limit=1 returns 1 job body, but X-Total-Count must reflect ALL (delta=3)
            assert total == before + 3, (
                f"API-19: X-Total-Count must not be capped by limit=1; "
                f"was {before}, expected {before + 3}, got {total!r}"
            )

    def test_x_total_count_respects_status_filter(self, tmp_path, monkeypatch):
        """X-Total-Count must count only jobs matching the status filter."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        with TestClient(app) as c:
            # Capture baseline counts before creating new jobs (the in-memory store
            # is shared across tests, so we use a delta assertion to stay isolated).
            r_init = c.get("/jobs?status=pending")
            initial_pending = int(
                r_init.headers.get("x-total-count") or r_init.headers.get("X-Total-Count") or "0"
            )
            r_init2 = c.get("/jobs?status=completed")
            initial_completed = int(
                r_init2.headers.get("x-total-count") or r_init2.headers.get("X-Total-Count") or "0"
            )

            body = {"country": "norway", "tax_year": 2025, "xrpl_accounts": [], "csv_files": []}
            for _ in range(2):
                c.post("/jobs", json=body)

            r = c.get("/jobs?status=pending")
            total = int(
                r.headers.get("x-total-count") or r.headers.get("X-Total-Count") or "0"
            )
            assert total == initial_pending + 2, (
                f"API-19: X-Total-Count with status=pending must increase by 2; "
                f"was {initial_pending}, got {total!r}"
            )
            # Creating PENDING jobs must not inflate the COMPLETED count
            r2 = c.get("/jobs?status=completed")
            total2 = int(
                r2.headers.get("x-total-count") or r2.headers.get("X-Total-Count") or "0"
            )
            assert total2 == initial_completed, (
                f"API-19: status=completed count must be unchanged; "
                f"was {initial_completed}, got {total2!r}"
            )

    def test_count_jobs_method_in_services(self):
        """services.py must have a count_jobs() method."""
        src = _services()
        assert "def count_jobs(" in src, (
            "API-19: services.py must define count_jobs() for header computation"
        )

    def test_count_method_in_storage(self):
        """storage.py stores must have a count() method."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        store = InMemoryJobStore()
        assert hasattr(store, "count"), "API-19: InMemoryJobStore must have count()"
        assert store.count() == 0

    def test_api19_comment_in_main(self):
        """An API-19 comment must document the header in main.py."""
        src = Path(_REPO / "taxspine_orchestrator" / "main.py").read_text(encoding="utf-8")
        assert "API-19" in src, "API-19: comment must be present in main.py"


# ── API-21: Dual path field sync ─────────────────────────────────────────────


class TestAPI21DualPathSync:
    """API-21: the singular backward-compat alias fields on JobOutput must always
    be derived from the plural list fields so they can never diverge."""

    def test_model_validator_present_in_models(self):
        """models.py JobOutput must have a model_validator syncing singular from plural."""
        src = _models()
        assert "model_validator" in src, (
            "API-21: models.py must import and use model_validator"
        )
        assert "_sync_singular_from_plural" in src or "sync_singular" in src, (
            "API-21: JobOutput must have a validator that syncs singular from plural paths"
        )

    def test_report_html_path_synced_from_paths(self):
        """report_html_path must be auto-set from report_html_paths when singular is None."""
        from taxspine_orchestrator.models import JobOutput
        out = JobOutput(report_html_paths=["/a/report.html", "/b/report.html"])
        assert out.report_html_path == "/a/report.html", (
            f"API-21: report_html_path must be synced from report_html_paths[0]; "
            f"got {out.report_html_path!r}"
        )

    def test_rf1159_path_synced_from_paths(self):
        """rf1159_json_path must be auto-set from rf1159_json_paths when singular is None."""
        from taxspine_orchestrator.models import JobOutput
        out = JobOutput(rf1159_json_paths=["/a/rf1159.json"])
        assert out.rf1159_json_path == "/a/rf1159.json", (
            f"API-21: rf1159_json_path must be synced from rf1159_json_paths[0]; "
            f"got {out.rf1159_json_path!r}"
        )

    def test_review_path_synced_from_paths(self):
        """review_json_path must be auto-set from review_json_paths when singular is None."""
        from taxspine_orchestrator.models import JobOutput
        out = JobOutput(review_json_paths=["/a/review.json"])
        assert out.review_json_path == "/a/review.json", (
            f"API-21: review_json_path must be synced from review_json_paths[0]; "
            f"got {out.review_json_path!r}"
        )

    def test_explicit_singular_not_overridden(self):
        """When both singular and plural are set, the explicit singular is preserved."""
        from taxspine_orchestrator.models import JobOutput
        out = JobOutput(
            report_html_path="/explicit/report.html",
            report_html_paths=["/list/report.html"],
        )
        # Explicit singular must not be overwritten
        assert out.report_html_path == "/explicit/report.html", (
            "API-21: model_validator must not overwrite an explicitly-set singular path"
        )

    def test_empty_lists_leave_singular_none(self):
        """Empty plural lists must not change a None singular to some non-None value."""
        from taxspine_orchestrator.models import JobOutput
        out = JobOutput()
        assert out.report_html_path is None
        assert out.rf1159_json_path is None
        assert out.review_json_path is None

    def test_api21_comment_present(self):
        """An API-21 comment must document the sync validator."""
        src = _models()
        assert "API-21" in src, "API-21: comment must be present in models.py"


# ── API-13: Cancel-then-complete race (MISSING TEST) ─────────────────────────


class TestAPI13CancelCompleteRace:
    """API-13: when a job is cancelled mid-run, the background thread completing
    must NOT overwrite the CANCELLED terminal state with COMPLETED or FAILED."""

    def test_cancel_guard_in_services_complete_path(self):
        """services.py must check for CANCELLED before writing COMPLETED."""
        src = _services()
        # Find the completion block
        assert "CANCELLED" in src, "API-13: CANCELLED state must be handled in services.py"
        # Guard must appear in both completion and failure paths
        assert src.count("JobStatus.CANCELLED") >= 2 or src.count("== JobStatus.CANCELLED") >= 1, (
            "API-13: CANCELLED guard must appear in the completion and/or failure path"
        )

    def test_cancel_guard_in_fail_job(self):
        """_fail_job must not overwrite a CANCELLED terminal state."""
        src = _services()
        fail_idx = src.find("def _fail_job(")
        assert fail_idx >= 0, "_fail_job must be present"
        fn_body = src[fail_idx:fail_idx + 600]
        assert "CANCELLED" in fn_body, (
            "API-13: _fail_job must check for CANCELLED before writing FAILED"
        )

    def test_cancel_guard_in_dry_run(self):
        """_execute_dry_run must not overwrite a CANCELLED terminal state."""
        src = _services()
        dry_run_idx = src.find("def _execute_dry_run")
        assert dry_run_idx >= 0, "_execute_dry_run must be present"
        # The function body can be long (it enumerates XRPL accounts and CSV files);
        # use 6000 chars to ensure we reach the CANCELLED guard at the end.
        fn_body = src[dry_run_idx:dry_run_idx + 6000]
        assert "CANCELLED" in fn_body, (
            "API-13: _execute_dry_run must check for CANCELLED before writing COMPLETED"
        )

    def test_cancelled_job_stays_cancelled_after_execution_completes(self, tmp_path, monkeypatch):
        """A job cancelled while running must remain CANCELLED after the background
        thread finishes, regardless of whether subprocess succeeded or failed."""
        import subprocess as _subprocess
        import time

        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        # Gate: execution thread blocks until we signal it to proceed
        proceed = threading.Event()
        _cancelled_before_complete = threading.Event()

        def _slow_run(*args, **kwargs):
            # Signal the test that execution has started, then wait for permission
            proceed.wait(timeout=10)
            return _subprocess.CompletedProcess(args=[], returncode=0)

        with patch("taxspine_orchestrator.services.subprocess.run", side_effect=_slow_run):
            with TestClient(app) as c:
                # Create a job that has at least one input (XRPL) so execution reaches subprocess
                resp = c.post("/jobs", json={
                    "country": "norway",
                    "tax_year": 2025,
                    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
                    "csv_files": [],
                })
                assert resp.status_code == 201
                job_id = resp.json()["id"]

                # Start execution (goes RUNNING, blocks in _slow_run)
                r_start = c.post(f"/jobs/{job_id}/start")
                assert r_start.status_code == 202

                # Wait briefly for the job to reach RUNNING
                for _ in range(20):
                    if c.get(f"/jobs/{job_id}").json()["status"] == "running":
                        break
                    time.sleep(0.05)

                # Cancel while RUNNING
                r_cancel = c.post(f"/jobs/{job_id}/cancel")
                assert r_cancel.status_code == 200
                assert c.get(f"/jobs/{job_id}").json()["status"] == "cancelled"

                # Now let the background thread complete (subprocess returns success)
                proceed.set()

                # Give the background thread time to finish
                time.sleep(0.3)

                # Status must still be CANCELLED — completion must not overwrite it
                final = c.get(f"/jobs/{job_id}").json()
                assert final["status"] == "cancelled", (
                    f"API-13: job status must remain 'cancelled' after background thread "
                    f"completes; got {final['status']!r}"
                )

    def test_cancelled_job_not_overwritten_by_fail(self, tmp_path, monkeypatch):
        """A CANCELLED job must not be overwritten with FAILED by the execution thread."""
        import subprocess as _subprocess
        import time

        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        proceed = threading.Event()

        def _failing_run(*args, **kwargs):
            proceed.wait(timeout=10)
            return _subprocess.CompletedProcess(args=[], returncode=1)  # non-zero → FAILED

        with patch("taxspine_orchestrator.services.subprocess.run", side_effect=_failing_run):
            with TestClient(app) as c:
                resp = c.post("/jobs", json={
                    "country": "norway",
                    "tax_year": 2025,
                    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
                    "csv_files": [],
                })
                job_id = resp.json()["id"]
                c.post(f"/jobs/{job_id}/start")

                for _ in range(20):
                    if c.get(f"/jobs/{job_id}").json()["status"] == "running":
                        break
                    time.sleep(0.05)

                c.post(f"/jobs/{job_id}/cancel")
                proceed.set()  # let the failing subprocess return

                time.sleep(0.3)
                final = c.get(f"/jobs/{job_id}").json()
                assert final["status"] == "cancelled", (
                    f"API-13: CANCELLED must survive a FAILED exit from the background thread; "
                    f"got {final['status']!r}"
                )


# ── API-23: Reports endpoints (MISSING TEST) ─────────────────────────────────


class TestAPI23ReportsEndpoints:
    """API-23: GET /jobs/{id}/reports and GET /jobs/{id}/reports/{index}
    endpoints must be exercised by tests."""

    @pytest.fixture(autouse=True)
    def _client(self, tmp_path, monkeypatch):
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()
        self.tmp_path = tmp_path
        self.client = TestClient(app)

    def _create_job(self) -> str:
        resp = self.client.post("/jobs", json={
            "country": "norway", "tax_year": 2025,
            "xrpl_accounts": [], "csv_files": [],
        })
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_list_reports_unknown_job_returns_404(self):
        """GET /jobs/{id}/reports on an unknown job must return 404."""
        r = self.client.get("/jobs/nonexistent/reports")
        assert r.status_code == 404, (
            f"API-23: /reports on unknown job must return 404, got {r.status_code}"
        )

    def test_list_reports_empty_when_no_reports(self):
        """GET /jobs/{id}/reports returns [] when the job has no report files."""
        job_id = self._create_job()
        r = self.client.get(f"/jobs/{job_id}/reports")
        assert r.status_code == 200
        assert r.json() == [], (
            f"API-23: /reports on a job with no outputs must return []; got {r.json()}"
        )

    def test_list_reports_returns_correct_schema(self, monkeypatch):
        """GET /jobs/{id}/reports returns items with index, filename, and url keys."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.models import JobOutput, Job, JobInput, JobStatus, Country
        from taxspine_orchestrator import main as _main

        # Create a report file on disk
        report_dir = self.tmp_path / "output" / "test-job-rpts"
        report_dir.mkdir(parents=True)
        report_file = report_dir / "report_abc.html"
        report_file.write_text("<html>Test report</html>", encoding="utf-8")

        # Inject a job with report paths into the store
        job_id = self._create_job()
        # Patch the job's output to include the report path
        job = _main._job_store.get(job_id)
        updated_output = job.output.model_copy(update={
            "report_html_paths": [str(report_file)],
        })
        _main._job_store.update_job(job_id, output=updated_output)

        r = self.client.get(f"/jobs/{job_id}/reports")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1, f"API-23: expected 1 report item, got {items}"
        item = items[0]
        assert item["index"] == 0, "API-23: first report must have index=0"
        assert "filename" in item, "API-23: report item must have 'filename' key"
        assert "url" in item, "API-23: report item must have 'url' key"
        assert f"/jobs/{job_id}/reports/0" in item["url"], (
            f"API-23: url must point to /reports/0; got {item['url']!r}"
        )

    def test_get_report_by_index_unknown_job_returns_404(self):
        """GET /jobs/{id}/reports/0 on an unknown job must return 404."""
        r = self.client.get("/jobs/nonexistent/reports/0")
        assert r.status_code == 404

    def test_get_report_by_index_out_of_range_returns_404(self):
        """GET /jobs/{id}/reports/99 when the job has no reports must return 404."""
        job_id = self._create_job()
        r = self.client.get(f"/jobs/{job_id}/reports/99")
        assert r.status_code == 404, (
            f"API-23: out-of-range index must return 404, got {r.status_code}"
        )

    def test_get_report_by_index_streams_file(self, monkeypatch):
        """GET /jobs/{id}/reports/0 must stream the HTML file when it exists."""
        from taxspine_orchestrator import main as _main

        report_dir = self.tmp_path / "output" / "test-job-stream"
        report_dir.mkdir(parents=True)
        report_file = report_dir / "report_xyz.html"
        html_content = "<html><body>Hello report</body></html>"
        report_file.write_text(html_content, encoding="utf-8")

        job_id = self._create_job()
        job = _main._job_store.get(job_id)
        updated_output = job.output.model_copy(update={
            "report_html_paths": [str(report_file)],
        })
        _main._job_store.update_job(job_id, output=updated_output)

        r = self.client.get(f"/jobs/{job_id}/reports/0")
        assert r.status_code == 200, (
            f"API-23: GET /reports/0 must return 200 when file exists, got {r.status_code}"
        )
        assert "html" in r.headers.get("content-type", "").lower(), (
            "API-23: report response must have text/html content-type"
        )

    def test_negative_index_returns_404(self):
        """GET /jobs/{id}/reports/-1 must return 404 (negative index is invalid)."""
        job_id = self._create_job()
        r = self.client.get(f"/jobs/{job_id}/reports/-1")
        assert r.status_code == 404, (
            f"API-23: negative index must return 404, got {r.status_code}"
        )


# ── INFRA-04: requirements.lock used in Dockerfile ───────────────────────────


class TestINFRA04RequirementsLock:
    """INFRA-04: the Dockerfile must use requirements.lock to pin transitive
    dependencies for reproducible Docker builds."""

    def test_requirements_lock_file_exists(self):
        """requirements.lock must exist in the repository root."""
        assert _LOCKFILE_PATH.exists(), (
            "INFRA-04: requirements.lock must exist for reproducible builds"
        )

    def test_requirements_lock_has_direct_deps(self):
        """requirements.lock must contain the key direct dependencies."""
        src = _lockfile()
        for dep in ("fastapi", "uvicorn", "pydantic", "starlette"):
            assert dep.lower() in src.lower(), (
                f"INFRA-04: requirements.lock must include '{dep}'"
            )

    def test_requirements_lock_has_pinned_versions(self):
        """requirements.lock must use exact == pins, not >= ranges."""
        src = _lockfile()
        # At least some lines must use == (exact pins)
        assert "==" in src, (
            "INFRA-04: requirements.lock must contain exact-version (==) pins"
        )

    def test_dockerfile_copies_requirements_lock(self):
        """Dockerfile must have a COPY requirements.lock step."""
        src = _dockerfile()
        assert "COPY requirements.lock" in src, (
            "INFRA-04: Dockerfile must COPY requirements.lock before installing"
        )

    def test_dockerfile_installs_requirements_lock(self):
        """Dockerfile must run pip install -r requirements.lock."""
        src = _dockerfile()
        assert "-r requirements.lock" in src, (
            "INFRA-04: Dockerfile must run 'pip install -r requirements.lock'"
        )

    def test_infra04_comment_in_dockerfile(self):
        """An INFRA-04 comment must document the lockfile step."""
        src = _dockerfile()
        assert "INFRA-04" in src, "INFRA-04: comment must be present in Dockerfile"

    def test_infra04_comment_in_lockfile(self):
        """An INFRA-04 comment must be present in requirements.lock."""
        src = _lockfile()
        assert "INFRA-04" in src, "INFRA-04: comment must be present in requirements.lock"
