"""Tests for CSV-related job execution paths.

Covers:
- CSV-only jobs (no XRPL accounts): one taxspine-nor-report call per CSV file.
- Combined XRPL + CSV jobs: taxspine-xrpl-nor calls first, then nor-report calls.
- Missing CSV file → FAILED before any subprocess call.
- No inputs at all → FAILED immediately.

All subprocess calls are mocked — no real CLIs are needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ok(**overrides):
    m = MagicMock()
    m.returncode = overrides.get("returncode", 0)
    m.stdout = overrides.get("stdout", "")
    m.stderr = overrides.get("stderr", "")
    return m


def _make_fail(rc: int = 1, stderr: str = "something broke"):
    return _make_ok(returncode=rc, stderr=stderr)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def csv_dir(tmp_path: Path) -> Path:
    """Create a temp directory with two dummy CSV files."""
    for name in ("generic1.csv", "generic2.csv"):
        (tmp_path / name).write_text("header\nrow\n", encoding="utf-8")
    return tmp_path


# ── CSV-only Norway job ───────────────────────────────────────────────────────


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
        # log is always written; gains_csv is not produced by this pipeline.
        assert body["output"]["log_path"] is not None
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_skips_xrpl_step(self, mock_run, client, csv_dir):
        """CSV-only jobs call taxspine-nor-report directly — no xrpl-nor step."""
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

        # Only one subprocess call (the CSV report CLI), NOT two.
        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-nor-report"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_uses_input_flag(self, mock_run, client, csv_dir):
        """The --input flag carries the CSV path; no --xrpl-scenario flag."""
        mock_run.return_value = _make_ok()

        csv1 = str(csv_dir / "generic1.csv")
        csv2 = str(csv_dir / "generic2.csv")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv1, csv2],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Two CSV files → two CLI calls.
        assert mock_run.call_count == 2

        cmd0 = mock_run.call_args_list[0][0][0]
        cmd1 = mock_run.call_args_list[1][0][0]

        # No legacy flags.
        assert "--xrpl-scenario" not in cmd0
        assert "--generic-events-csv" not in cmd0

        # Correct flags present.
        assert "--input" in cmd0
        assert csv1 in cmd0
        assert "--year" in cmd0
        assert "2025" in cmd0
        assert "--html-output" in cmd0

        assert "--input" in cmd1
        assert csv2 in cmd1

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
        assert "taxspine-nor-report failed" in body["output"]["error_message"]


# ── Combined XRPL + CSV Norway job ───────────────────────────────────────────


class TestCombinedXrplCsv:
    """Job with both XRPL accounts and CSV files."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_combined_completes(self, mock_run, client, csv_dir):
        # 1 XRPL account + 1 CSV file → 2 subprocess calls.
        mock_run.side_effect = [_make_ok(), _make_ok()]

        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
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
    def test_combined_calls_xrpl_nor_then_report(self, mock_run, client, csv_dir):
        """XRPL accounts are processed first, then CSV files."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        csv_path = str(csv_dir / "generic1.csv")
        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv_path],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Exactly 2 calls: one xrpl-nor, one nor-report.
        assert mock_run.call_count == 2

        # First call: taxspine-xrpl-nor (XRPL → Norway pipeline).
        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert xrpl_cmd[0] == "taxspine-xrpl-nor"
        assert "--account" in xrpl_cmd
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" in xrpl_cmd

        # Second call: taxspine-nor-report (generic-events CSV).
        report_cmd = mock_run.call_args_list[1][0][0]
        assert report_cmd[0] == "taxspine-nor-report"
        assert "--input" in report_cmd
        assert csv_path in report_cmd


# ── Missing CSV file ──────────────────────────────────────────────────────────


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
        assert mock_run.call_count == 0

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_missing_csv_in_combined_job(self, mock_run, client, tmp_path):
        """Even with valid XRPL accounts, missing CSV → FAILED, no calls."""
        missing = str(tmp_path / "nope.csv")
        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
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


# ── No inputs at all ──────────────────────────────────────────────────────────


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


# ── CSV-only UK job ───────────────────────────────────────────────────────────


class TestCsvOnlyUK:
    """Verify CSV-only pipeline for UK country."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_csv_uses_uk_cli(self, mock_run, client, csv_dir):
        """UK CSV jobs use taxspine-uk-report (not the Norway binary)."""
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

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_csv_uses_input_flag(self, mock_run, client, csv_dir):
        """UK CLI uses the same --input flag as the Norway CLI."""
        mock_run.return_value = _make_ok()

        csv_path = str(csv_dir / "generic1.csv")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "uk",
            "csv_files": [csv_path],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert "--input" in cmd
        assert csv_path in cmd
        assert "--year" in cmd
        assert "2025" in cmd
        assert "--html-output" in cmd
        # No legacy flags.
        assert "--uk-gains-csv" not in cmd
        assert "--xrpl-scenario" not in cmd
