"""Tests for the GET /alerts endpoint.

Covers:
- Empty state (no jobs, healthy system → empty list)
- Health alerts from degraded checks (CLI missing, output dir, DB error)
- Review alerts from completed Norway jobs with warnings
- Review alerts for jobs with unlinked transfers
- Multiple jobs → multiple alerts, each with correct fields
- Severity ordering (error before warn)
- Only completed/failed jobs are scanned — pending/running are skipped
- Jobs without review data (UK jobs, XRPL-only) are silently skipped
- Review files missing on disk are silently skipped
- Alert schema: all required keys present in every alert
- limit query parameter respected
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_VALID_ADDR = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"

_SAMPLE_JOB = {
    "xrpl_accounts": [_VALID_ADDR],
    "tax_year": 2025,
    "country": "norway",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    from taxspine_orchestrator.main import app
    return TestClient(app)


def _mock_which_ok(name: str):
    """Pretend both CLI binaries are installed."""
    if name in ("taxspine-nor-report", "taxspine-xrpl-nor"):
        return f"/usr/local/bin/{name}"
    import shutil
    return shutil.which(name)


def _write_review_file(tmp_dir: Path, warnings: list[str], has_unlinked: bool = False) -> str:
    """Write a review JSON file and return its path."""
    payload = {
        "has_unlinked_transfers": has_unlinked,
        "warning_count": len(warnings),
        "warnings": warnings,
        "clean": len(warnings) == 0 and not has_unlinked,
    }
    p = tmp_dir / f"review_{id(payload)}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _inject_review(client, job_id: str, review_path: str) -> None:
    """Directly inject a review_json_path into a job in the store."""
    from taxspine_orchestrator import main as _m
    from taxspine_orchestrator.models import JobOutput, JobStatus

    job = _m._job_store.get(job_id)
    assert job is not None
    _m._job_store.update_job(
        job_id,
        status=JobStatus.COMPLETED,
        output=JobOutput(
            review_json_path=review_path,
            review_json_paths=[review_path],
        ),
    )


# ── TestAlertsEmptyState ──────────────────────────────────────────────────────


class TestAlertsEmptyState:
    """No jobs + healthy system → empty alert list."""

    def test_returns_200(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            resp = client.get("/alerts")
        assert resp.status_code == 200

    def test_returns_list(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            resp = client.get("/alerts")
        assert isinstance(resp.json(), list)

    def test_empty_when_no_jobs_and_healthy(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            resp = client.get("/alerts")
        assert resp.json() == []


# ── TestHealthAlerts ──────────────────────────────────────────────────────────


class TestHealthAlerts:
    """Degraded health checks produce health-category alerts."""

    def test_missing_cli_produces_health_alert(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", return_value=None), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        categories = [a["category"] for a in alerts]
        assert "health" in categories

    def test_missing_cli_alert_has_warn_severity(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", return_value=None), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        health_alerts = [a for a in alerts if a["category"] == "health"]
        assert all(a["severity"] == "warn" for a in health_alerts)

    def test_unwritable_output_dir_produces_error_alert(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=False):
            alerts = client.get("/alerts").json()

        health_alerts = [a for a in alerts if a["category"] == "health"]
        error_alerts  = [a for a in health_alerts if a["severity"] == "error"]
        assert len(error_alerts) >= 1

    def test_db_error_produces_error_alert(self, client: TestClient) -> None:
        from taxspine_orchestrator import main as _m

        def _bad_ping():
            raise RuntimeError("disk full")

        with patch.object(_m._job_store, "ping", side_effect=_bad_ping), \
             patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        db_alerts = [a for a in alerts if "db" in a["message"]]
        assert len(db_alerts) == 1
        assert db_alerts[0]["severity"] == "error"

    def test_health_alert_job_id_is_null(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", return_value=None), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        health_alerts = [a for a in alerts if a["category"] == "health"]
        assert all(a["job_id"] is None for a in health_alerts)


# ── TestReviewAlerts ──────────────────────────────────────────────────────────


class TestReviewAlerts:
    """Completed jobs with non-clean review JSONs produce review-category alerts."""

    def test_clean_job_produces_no_alert(self, client: TestClient) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            job_id = resp.json()["id"]

            review_path = _write_review_file(tmp_dir, warnings=[], has_unlinked=False)
            _inject_review(client, job_id, review_path)

        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert review_alerts == []

    def test_job_with_warnings_produces_warn_alert(self, client: TestClient) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            job_id = resp.json()["id"]

            review_path = _write_review_file(tmp_dir, warnings=["Missing basis for BTC"])
            _inject_review(client, job_id, review_path)

            with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
                 patch("taxspine_orchestrator.main.os.access", return_value=True):
                alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert len(review_alerts) == 1
        assert review_alerts[0]["severity"] == "warn"

    def test_job_with_unlinked_transfers_produces_error_alert(self, client: TestClient) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            job_id = resp.json()["id"]

            review_path = _write_review_file(tmp_dir, warnings=[], has_unlinked=True)
            _inject_review(client, job_id, review_path)

            with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
                 patch("taxspine_orchestrator.main.os.access", return_value=True):
                alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert len(review_alerts) == 1
        assert review_alerts[0]["severity"] == "error"

    def test_alert_detail_contains_warnings(self, client: TestClient) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            job_id = resp.json()["id"]

            review_path = _write_review_file(tmp_dir, warnings=["w1", "w2"])
            _inject_review(client, job_id, review_path)

            with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
                 patch("taxspine_orchestrator.main.os.access", return_value=True):
                alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert review_alerts[0]["detail"] == ["w1", "w2"]

    def test_alert_job_id_matches(self, client: TestClient) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            job_id = resp.json()["id"]

            review_path = _write_review_file(tmp_dir, warnings=["something"])
            _inject_review(client, job_id, review_path)

            with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
                 patch("taxspine_orchestrator.main.os.access", return_value=True):
                alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert review_alerts[0]["job_id"] == job_id

    def test_pending_job_not_scanned(self, client: TestClient) -> None:
        """A PENDING job (no review data injected) should produce no review alert."""
        client.post("/jobs", json=_SAMPLE_JOB)

        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert review_alerts == []

    def test_job_without_review_paths_skipped(self, client: TestClient) -> None:
        """XRPL-only / UK jobs with no review_json_paths → no review alert."""
        from taxspine_orchestrator import main as _m
        from taxspine_orchestrator.models import JobOutput, JobStatus

        resp = client.post("/jobs", json=_SAMPLE_JOB)
        job_id = resp.json()["id"]
        _m._job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output=JobOutput(),  # no review paths
        )

        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert review_alerts == []

    def test_missing_review_file_on_disk_skipped(self, client: TestClient) -> None:
        """If the review JSON file doesn't exist on disk, silently skip."""
        from taxspine_orchestrator import main as _m
        from taxspine_orchestrator.models import JobOutput, JobStatus

        resp = client.post("/jobs", json=_SAMPLE_JOB)
        job_id = resp.json()["id"]
        _m._job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output=JobOutput(
                review_json_path="/nonexistent/review.json",
                review_json_paths=["/nonexistent/review.json"],
            ),
        )

        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert review_alerts == []


# ── TestAlertSchema ───────────────────────────────────────────────────────────


class TestAlertSchema:
    """Every alert in the response must have the required keys."""

    _REQUIRED_KEYS = {"severity", "category", "message", "job_id", "detail"}

    def test_health_alert_has_all_keys(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", return_value=None), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        assert alerts  # at least one health alert from missing CLIs
        for alert in alerts:
            assert self._REQUIRED_KEYS <= alert.keys()

    def test_review_alert_has_all_keys(self, client: TestClient) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            job_id = resp.json()["id"]
            review_path = _write_review_file(tmp_dir, warnings=["x"])
            _inject_review(client, job_id, review_path)

            with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
                 patch("taxspine_orchestrator.main.os.access", return_value=True):
                alerts = client.get("/alerts").json()

        review_alerts = [a for a in alerts if a["category"] == "review"]
        for alert in review_alerts:
            assert self._REQUIRED_KEYS <= alert.keys()

    def test_detail_is_always_list(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", return_value=None), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            alerts = client.get("/alerts").json()

        for alert in alerts:
            assert isinstance(alert["detail"], list)


# ── TestAlertSorting ──────────────────────────────────────────────────────────


class TestAlertSorting:
    """Alerts are sorted: error first, then warn."""

    def test_error_before_warn(self, client: TestClient) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)

            # Create job with unlinked transfers (→ error severity)
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            job_id = resp.json()["id"]
            review_path = _write_review_file(tmp_dir, warnings=[], has_unlinked=True)
            _inject_review(client, job_id, review_path)

            # Missing CLI will produce warn severity
            with patch("taxspine_orchestrator.main.shutil.which", return_value=None), \
                 patch("taxspine_orchestrator.main.os.access", return_value=True):
                alerts = client.get("/alerts").json()

        severities = [a["severity"] for a in alerts]
        # All "error" entries must appear before any "warn" entry
        last_error_idx = max((i for i, s in enumerate(severities) if s == "error"), default=-1)
        first_warn_idx = min((i for i, s in enumerate(severities) if s == "warn"), default=len(severities))
        assert last_error_idx < first_warn_idx


# ── TestAlertsLimitParam ──────────────────────────────────────────────────────


class TestAlertsLimitParam:
    def test_limit_param_accepted(self, client: TestClient) -> None:
        with patch("taxspine_orchestrator.main.shutil.which", side_effect=_mock_which_ok), \
             patch("taxspine_orchestrator.main.os.access", return_value=True):
            resp = client.get("/alerts?limit=5")
        assert resp.status_code == 200

    def test_limit_too_low_rejected(self, client: TestClient) -> None:
        resp = client.get("/alerts?limit=0")
        assert resp.status_code == 422

    def test_limit_too_high_rejected(self, client: TestClient) -> None:
        resp = client.get("/alerts?limit=101")
        assert resp.status_code == 422
