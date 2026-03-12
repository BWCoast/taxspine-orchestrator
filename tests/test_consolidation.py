"""Tests for the consolidated CLI invocation logic (Task P3-A).

Mixed workspace (XRPL + CSV):
  - A single ``taxspine-xrpl-nor`` invocation for the primary account
    includes ALL CSV files via ``--generic-events-csv``.
  - Additional XRPL accounts each get their own invocation (no CSV files —
    those were merged with the primary account).

CSV-only workspace:
  - ``taxspine-nor-report`` is used per CSV file (unchanged).

XRPL-only workspace:
  - ``taxspine-xrpl-nor`` is used per account (unchanged).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ok(**overrides):
    """Return a fake CompletedProcess with rc=0."""
    m = MagicMock()
    m.returncode = overrides.get("returncode", 0)
    m.stdout = overrides.get("stdout", "")
    m.stderr = overrides.get("stderr", "")
    return m


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
    """Create a temp directory with three dummy CSV files."""
    for name in ("firi1.csv", "firi2.csv", "firi3.csv"):
        (tmp_path / name).write_text("header\nrow\n", encoding="utf-8")
    return tmp_path


# ── Mixed workspace: XRPL + CSV ───────────────────────────────────────────────


class TestConsolidatedCliArgs:
    """Consolidated pipeline for mixed-workspace (XRPL + CSV) jobs."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_mixed_workspace_uses_single_xrpl_invocation(
        self, mock_run, client, csv_dir
    ):
        """Single XRPL account + one CSV → exactly ONE subprocess call."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_dir / "firi1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        resp = client.post(f"/jobs/{job_id}/start")

        assert resp.json()["status"] == "completed"
        # One consolidated invocation — not two separate ones.
        assert mock_run.call_count == 1

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_mixed_workspace_uses_xrpl_nor_cli(self, mock_run, client, csv_dir):
        """The single consolidated invocation uses taxspine-xrpl-nor."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_dir / "firi1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-xrpl-nor"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_mixed_workspace_includes_csv_in_xrpl_invocation(
        self, mock_run, client, csv_dir
    ):
        """The consolidated invocation includes the CSV via --generic-events-csv."""
        mock_run.return_value = _make_ok()

        csv_path = str(csv_dir / "firi1.csv")
        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv_path],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert "--generic-events-csv" in cmd
        assert csv_path in cmd

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_mixed_workspace_no_nor_report_call(self, mock_run, client, csv_dir):
        """taxspine-nor-report is NOT called for mixed-workspace jobs."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_dir / "firi1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert cmd[0] != "taxspine-nor-report", (
                "taxspine-nor-report must not be called in a mixed workspace job"
            )

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_multiple_csv_files_all_included(self, mock_run, client, csv_dir):
        """Multiple CSV files → all are passed as --generic-events-csv to primary account."""
        mock_run.return_value = _make_ok()

        csv1 = str(csv_dir / "firi1.csv")
        csv2 = str(csv_dir / "firi2.csv")
        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv1, csv2],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Still only ONE subprocess call.
        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]

        # Both CSV files included.
        assert "--generic-events-csv" in cmd
        assert csv1 in cmd
        assert csv2 in cmd

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_multiple_accounts_primary_gets_csv(self, mock_run, client, csv_dir):
        """Two XRPL accounts + one CSV → 2 calls; CSV attached to primary only."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        csv_path = str(csv_dir / "firi1.csv")
        payload = {
            "xrpl_accounts": [
                "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe",
            ],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv_path],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Two accounts → two xrpl-nor calls.
        assert mock_run.call_count == 2

        # Primary account (first) carries the CSV.
        cmd0 = mock_run.call_args_list[0][0][0]
        assert cmd0[0] == "taxspine-xrpl-nor"
        assert "--generic-events-csv" in cmd0
        assert csv_path in cmd0

        # Secondary account (second) does NOT carry the CSV.
        cmd1 = mock_run.call_args_list[1][0][0]
        assert cmd1[0] == "taxspine-xrpl-nor"
        assert "--generic-events-csv" not in cmd1
        assert csv_path not in cmd1

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_multiple_csv_files_all_in_primary_account(
        self, mock_run, client, csv_dir
    ):
        """Three CSV files + two XRPL accounts → 2 calls; all CSVs in primary."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        csv1 = str(csv_dir / "firi1.csv")
        csv2 = str(csv_dir / "firi2.csv")
        csv3 = str(csv_dir / "firi3.csv")
        payload = {
            "xrpl_accounts": [
                "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe",
            ],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv1, csv2, csv3],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 2

        # Primary: all three CSVs present.
        cmd0 = mock_run.call_args_list[0][0][0]
        assert cmd0.count("--generic-events-csv") == 3
        assert csv1 in cmd0
        assert csv2 in cmd0
        assert csv3 in cmd0

        # Secondary: no CSVs.
        cmd1 = mock_run.call_args_list[1][0][0]
        assert "--generic-events-csv" not in cmd1


# ── CSV-only workspace ────────────────────────────────────────────────────────


class TestCsvOnlyWorkspace:
    """CSV-only jobs still use taxspine-nor-report (no XRPL accounts)."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_uses_nor_report(self, mock_run, client, csv_dir):
        """CSV-only job uses taxspine-nor-report, not taxspine-xrpl-nor."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_dir / "firi1.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-nor-report"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_multiple_files_separate_calls(self, mock_run, client, csv_dir):
        """Multiple CSV files in CSV-only mode → one nor-report call per file."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [
                str(csv_dir / "firi1.csv"),
                str(csv_dir / "firi2.csv"),
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Two files → two separate nor-report calls.
        assert mock_run.call_count == 2
        for call in mock_run.call_args_list:
            assert call[0][0][0] == "taxspine-nor-report"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_csv_only_no_generic_events_csv_flag(self, mock_run, client, csv_dir):
        """CSV-only nor-report calls use --input, not --generic-events-csv."""
        mock_run.return_value = _make_ok()

        csv_path = str(csv_dir / "firi1.csv")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv_path],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert "--input" in cmd
        assert csv_path in cmd
        assert "--generic-events-csv" not in cmd


# ── XRPL-only workspace ───────────────────────────────────────────────────────


class TestXrplOnlyWorkspace:
    """XRPL-only jobs use taxspine-xrpl-nor without CSV files (unchanged)."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_xrpl_only_uses_xrpl_nor(self, mock_run, client):
        """XRPL-only job uses taxspine-xrpl-nor."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-xrpl-nor"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_xrpl_only_no_generic_events_csv_flag(self, mock_run, client):
        """XRPL-only invocations do not include --generic-events-csv."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert "--generic-events-csv" not in cmd

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_xrpl_only_multiple_accounts_one_call_each(self, mock_run, client):
        """Multiple XRPL accounts → one xrpl-nor call per account."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        payload = {
            "xrpl_accounts": [
                "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe",
            ],
            "tax_year": 2025,
            "country": "norway",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 2
        for call in mock_run.call_args_list:
            assert call[0][0][0] == "taxspine-xrpl-nor"
            assert "--generic-events-csv" not in call[0][0]


# ── Dry-run: mixed workspace ───────────────────────────────────────────────────


class TestDryRunConsolidation:
    """Dry-run mode mirrors the consolidated pipeline (no subprocess calls)."""

    def test_dry_run_mixed_logs_single_xrpl_command(self, client, csv_dir):
        """Dry-run log shows one consolidated xrpl-nor command for mixed workspace."""
        csv_path = str(csv_dir / "firi1.csv")
        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv_path],
            "dry_run": True,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"
        log_path = body["output"]["log_path"]
        log_text = open(log_path, encoding="utf-8").read()

        # Exactly one [would run] line.
        would_run_lines = [ln for ln in log_text.splitlines() if "[would run]" in ln]
        assert len(would_run_lines) == 1

        # That line uses taxspine-xrpl-nor with the CSV attached.
        assert "taxspine-xrpl-nor" in would_run_lines[0]
        assert "--generic-events-csv" in would_run_lines[0]
        assert csv_path in would_run_lines[0]
        # taxspine-nor-report must NOT appear.
        assert "taxspine-nor-report" not in would_run_lines[0]

    def test_dry_run_csv_only_logs_nor_report_command(self, client, csv_dir):
        """Dry-run log shows nor-report command for CSV-only workspace."""
        csv_path = str(csv_dir / "firi1.csv")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [csv_path],
            "dry_run": True,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        log_text = open(body["output"]["log_path"], encoding="utf-8").read()
        would_run_lines = [ln for ln in log_text.splitlines() if "[would run]" in ln]
        assert len(would_run_lines) == 1
        assert "taxspine-nor-report" in would_run_lines[0]
        assert "--generic-events-csv" not in would_run_lines[0]
