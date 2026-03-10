"""Tests for CSV-related job execution paths.

Covers:
- CSV-only jobs (no XRPL accounts)
- Combined XRPL + CSV jobs
- Missing CSV file → FAILED
- No inputs at all → FAILED

All subprocess calls are mocked — no real CLIs are needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_ok(**overrides):
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


@pytest.fixture()
def csv_dir(tmp_path: Path) -> Path:
    """Create a temp directory with two dummy CSV files."""
    for name in ("generic1.csv", "generic2.csv"):
        (tmp_path / name).write_text("header\nrow\n", encoding="utf-8")
    return tmp_path


# ── CSV-only Norway job ──────────────────────────────────────────────────────


class TestCsvOnlyNorway:
    """CSV-only job: no XRPL accounts, only generic-events CSVs."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_completes(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [
                str(csv_dir / "generic1.csv"),
                str(csv_dir / "generic2.csv"),
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"
        assert body["output"]["gains_csv_path"] is not None
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_skips_blockchain_reader(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_dir / "generic1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Only one subprocess call (the tax CLI), NOT two.
        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-nor-report"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_no_xrpl_scenario_flag(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [
                str(csv_dir / "generic1.csv"),
                str(csv_dir / "generic2.csv"),
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        # No --xrpl-scenario flag
        assert "--xrpl-scenario" not in cmd
        # Two --generic-events-csv flags
        csv_indices = [
            i for i, arg in enumerate(cmd) if arg == "--generic-events-csv"
        ]
        assert len(csv_indices) == 2
        assert cmd[csv_indices[0] + 1] == str(csv_dir / "generic1.csv")
        assert cmd[csv_indices[1] + 1] == str(csv_dir / "generic2.csv")

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_report_failure(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_fail(rc=1)

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_dir / "generic1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "tax report CLI failed" in body["output"]["error_message"]


# ── Combined XRPL + CSV Norway job ──────────────────────────────────────────


class TestCombinedXrplCsv:
    """Job with both XRPL accounts and CSV files."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_combined_completes(self, mock_run, client, csv_dir):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        payload = {
            "xrpl_accounts": ["rEXAMPLE1"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_dir / "generic1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_combined_calls_reader_and_report(self, mock_run, client, csv_dir):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        csv_path = str(csv_dir / "generic1.csv")
        payload = {
            "xrpl_accounts": ["rEXAMPLE1"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv_path],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Two calls: blockchain-reader then tax CLI
        assert mock_run.call_count == 2

        # First: blockchain-reader
        reader_cmd = mock_run.call_args_list[0][0][0]
        assert reader_cmd[0] == "blockchain-reader"

        # Second: tax CLI with BOTH --xrpl-scenario AND --generic-events-csv
        report_cmd = mock_run.call_args_list[1][0][0]
        assert report_cmd[0] == "taxspine-nor-report"
        assert "--xrpl-scenario" in report_cmd
        assert "--generic-events-csv" in report_cmd
        csv_idx = report_cmd.index("--generic-events-csv")
        assert report_cmd[csv_idx + 1] == csv_path


# ── Missing CSV file ─────────────────────────────────────────────────────────


class TestMissingCsvFile:
    """CSV path does not exist → FAILED before any subprocess call."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_missing_csv_fails_immediately(self, mock_run, client, tmp_path):
        missing = str(tmp_path / "does" / "not" / "exist.csv")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [missing],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "CSV file not found" in body["output"]["error_message"]
        assert missing in body["output"]["error_message"]
        # No subprocess calls at all.
        assert mock_run.call_count == 0

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_missing_csv_in_combined_job(self, mock_run, client, tmp_path):
        """Even with valid XRPL accounts, missing CSV → FAILED, no calls."""
        missing = str(tmp_path / "nope.csv")
        payload = {
            "xrpl_accounts": ["rEXAMPLE1"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [missing],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "CSV file not found" in body["output"]["error_message"]
        assert mock_run.call_count == 0

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_second_csv_missing(self, mock_run, client, csv_dir, tmp_path):
        """First CSV exists, second does not → FAILED."""
        good = str(csv_dir / "generic1.csv")
        bad = str(tmp_path / "missing.csv")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [good, bad],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "missing.csv" in body["output"]["error_message"]
        assert mock_run.call_count == 0


# ── No inputs at all ─────────────────────────────────────────────────────────


class TestNoInputs:
    """Empty xrpl_accounts AND empty csv_files → FAILED immediately."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_no_inputs_fails(self, mock_run, client):
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "no inputs" in body["output"]["error_message"].lower()
        assert "no XRPL accounts" in body["output"]["error_message"]
        assert "no CSV files" in body["output"]["error_message"]
        assert mock_run.call_count == 0

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_no_inputs_has_log_path(self, mock_run, client):
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "uk",
            "csv_files": [],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["output"]["log_path"] is not None


# ── CSV-only UK job ──────────────────────────────────────────────────────────


class TestCsvOnlyUK:
    """Verify CSV-only works for UK country too."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_csv_only_uses_uk_flags(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "uk",
            "csv_files": [str(csv_dir / "generic1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-uk-report"
        assert "--uk-gains-csv" in cmd
        assert "--generic-events-csv" in cmd
        assert "--xrpl-scenario" not in cmd
