"""Tests for the job execution pipeline (services.py).

All subprocess calls are mocked — no real CLIs are needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app

# ── Helpers ──────────────────────────────────────────────────────────────────

_NORWAY_INPUT = {
    "xrpl_accounts": ["rAccount1", "rAccount2"],
    "tax_year": 2025,
    "country": "norway",
}

_UK_INPUT = {
    "xrpl_accounts": ["rUkAccount"],
    "tax_year": 2025,
    "country": "uk",
}


def _make_ok(**overrides):
    """Return a fake CompletedProcess with rc=0."""
    m = MagicMock()
    m.returncode = overrides.get("returncode", 0)
    m.stdout = overrides.get("stdout", "")
    m.stderr = overrides.get("stderr", "")
    return m


def _make_fail(rc: int = 1, stderr: str = "something broke"):
    return _make_ok(returncode=rc, stderr=stderr)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m

    _m._job_store._jobs.clear()


@pytest.fixture()
def client():
    return TestClient(app)


# ── Successful execution ─────────────────────────────────────────────────────


class TestSuccessNorway:
    """Full happy path for a Norway XRPL job."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_completed_with_outputs(self, mock_run, client):
        # Both CLI calls succeed.
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert resp.status_code == 200
        assert body["status"] == "completed"
        assert body["output"]["gains_csv_path"] is not None
        assert body["output"]["wealth_csv_path"] is not None
        assert body["output"]["summary_json_path"] is not None
        assert body["output"]["log_path"] is not None
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_calls_reader_then_nor_report(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 2

        # First call: blockchain-reader
        reader_cmd = mock_run.call_args_list[0][0][0]
        assert reader_cmd[0] == "blockchain-reader"
        assert "--mode" in reader_cmd
        assert "scenario" in reader_cmd
        assert "--xrpl-account" in reader_cmd
        assert "rAccount1" in reader_cmd
        assert "rAccount2" in reader_cmd

        # Second call: taxspine-nor-report
        report_cmd = mock_run.call_args_list[1][0][0]
        assert report_cmd[0] == "taxspine-nor-report"
        assert "--tax-year" in report_cmd
        assert "2025" in report_cmd


class TestSuccessUK:
    """Full happy path for a UK XRPL job."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_completed_with_outputs(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_UK_INPUT)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"
        assert body["output"]["gains_csv_path"] is not None
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_calls_uk_report_cli(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_UK_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        report_cmd = mock_run.call_args_list[1][0][0]
        assert report_cmd[0] == "taxspine-uk-report"
        assert "--uk-gains-csv" in report_cmd
        assert "--uk-wealth-csv" in report_cmd
        assert "--uk-summary-json" in report_cmd


# ── Failing blockchain-reader ────────────────────────────────────────────────


class TestFailReader:
    """blockchain-reader returns non-zero → job FAILED."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_reader_failure_marks_failed(self, mock_run, client):
        mock_run.side_effect = [_make_fail(rc=1, stderr="connection refused")]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "blockchain-reader failed" in body["output"]["error_message"]
        assert body["output"]["log_path"] is not None
        # No output artefacts
        assert body["output"]["gains_csv_path"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_reader_failure_does_not_call_report_cli(self, mock_run, client):
        mock_run.side_effect = [_make_fail()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Only one call (reader), not two.
        assert mock_run.call_count == 1


# ── Failing tax-report CLI ───────────────────────────────────────────────────


class TestFailReportCLI:
    """Reader succeeds but tax-report CLI fails → job FAILED."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_report_failure_marks_failed(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_fail(rc=2, stderr="bad schema")]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "tax report CLI failed" in body["output"]["error_message"]
        assert body["output"]["log_path"] is not None
        assert body["output"]["gains_csv_path"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_report_failure_both_calls_made(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_fail()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Reader succeeded, so report CLI was also invoked.
        assert mock_run.call_count == 2


# ── Idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    """Starting a job that is not PENDING returns the current state."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_start_completed_job_is_noop(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        # First start → completes
        client.post(f"/jobs/{job_id}/start")
        # Second start → returns same completed state, no extra calls
        resp2 = client.post(f"/jobs/{job_id}/start")

        assert resp2.json()["status"] == "completed"
        # Only 2 subprocess calls total (from the first start).
        assert mock_run.call_count == 2

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_start_failed_job_is_noop(self, mock_run, client):
        mock_run.side_effect = [_make_fail()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        client.post(f"/jobs/{job_id}/start")
        resp2 = client.post(f"/jobs/{job_id}/start")

        assert resp2.json()["status"] == "failed"
        assert mock_run.call_count == 1
