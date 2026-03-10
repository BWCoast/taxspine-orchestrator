"""Tests for valuation_mode and csv_prices_path support.

All subprocess calls are mocked — no real CLIs are needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app


# ── Helpers ──────────────────────────────────────────────────────────────────

_NORWAY_BASE = {
    "xrpl_accounts": ["rAccount1"],
    "tax_year": 2025,
    "country": "norway",
}

_UK_BASE = {
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


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m

    _m._job_store._jobs.clear()


@pytest.fixture()
def client():
    return TestClient(app)


# ── Default behaviour (DUMMY) unchanged ──────────────────────────────────────


class TestDefaultDummy:
    """Jobs without valuation_mode behave exactly as before."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_no_csv_prices_flag_by_default(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_BASE)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        assert resp.json()["status"] == "completed"

        # Tax CLI command (second call) should NOT contain --csv-prices.
        report_cmd = mock_run.call_args_list[1][0][0]
        assert "--csv-prices" not in report_cmd

    def test_valuation_mode_defaults_to_dummy(self, client):
        resp = client.post("/jobs", json=_NORWAY_BASE)
        assert resp.json()["input"]["valuation_mode"] == "dummy"
        assert resp.json()["input"]["csv_prices_path"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_explicit_dummy_no_csv_prices_flag(self, mock_run, client):
        """Explicitly setting valuation_mode=dummy should also omit --csv-prices."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        client.post(f"/jobs/{job_id}/start")

        report_cmd = mock_run.call_args_list[1][0][0]
        assert "--csv-prices" not in report_cmd


# ── PRICE_TABLE with valid path ──────────────────────────────────────────────


class TestPriceTableSuccess:
    """price_table mode with an existing CSV price file."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_norway_includes_csv_prices_flag(self, mock_run, client, tmp_path):
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n2025-01-01,XRP,2.5\n")

        mock_run.side_effect = [_make_ok(), _make_ok()]

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"

        report_cmd = mock_run.call_args_list[1][0][0]
        assert "--csv-prices" in report_cmd
        idx = report_cmd.index("--csv-prices")
        assert report_cmd[idx + 1] == str(prices_file)

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_includes_csv_prices_flag(self, mock_run, client, tmp_path):
        prices_file = tmp_path / "prices-gbp.csv"
        prices_file.write_text("date,asset,price\n2025-01-01,XRP,1.8\n")

        mock_run.side_effect = [_make_ok(), _make_ok()]

        payload = {
            **_UK_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"

        report_cmd = mock_run.call_args_list[1][0][0]
        assert "--csv-prices" in report_cmd
        idx = report_cmd.index("--csv-prices")
        assert report_cmd[idx + 1] == str(prices_file)

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_completed_with_outputs(self, mock_run, client, tmp_path):
        """Full happy-path assertions still hold with price_table mode."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n")

        mock_run.side_effect = [_make_ok(), _make_ok()]

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"
        assert body["output"]["gains_csv_path"] is not None
        assert body["output"]["wealth_csv_path"] is not None
        assert body["output"]["summary_json_path"] is not None
        assert body["output"]["log_path"] is not None
        assert body["output"]["error_message"] is None


# ── PRICE_TABLE with missing csv_prices_path ─────────────────────────────────


class TestPriceTableMissingPath:
    """price_table mode without csv_prices_path → FAILED."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_fails_when_csv_prices_path_is_null(self, mock_run, client):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            # csv_prices_path omitted (defaults to None)
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "csv_prices_path" in body["output"]["error_message"]
        # No subprocess calls should have been made.
        mock_run.assert_not_called()

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_fails_when_csv_prices_path_explicit_null(self, mock_run, client):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": None,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "csv_prices_path" in body["output"]["error_message"]
        mock_run.assert_not_called()


# ── PRICE_TABLE with non-existent file ───────────────────────────────────────


class TestPriceTableFileNotFound:
    """price_table mode with a path that does not exist on disk → FAILED."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_fails_with_nonexistent_file(self, mock_run, client):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": "/does/not/exist.csv",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "failed"
        assert "CSV price table not found" in body["output"]["error_message"]
        assert "/does/not/exist.csv" in body["output"]["error_message"]
        mock_run.assert_not_called()

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_has_log_path(self, mock_run, client):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": "/nonexistent/prices.csv",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["output"]["log_path"] is not None
        assert body["output"]["gains_csv_path"] is None


# ── Dry-run + valuation_mode ─────────────────────────────────────────────────


class TestDryRunWithPriceTable:
    """dry_run=true + valuation_mode=price_table should log --csv-prices."""

    def test_dry_run_logs_csv_prices_flag(self, client, tmp_path):
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n")

        payload = {
            **_NORWAY_BASE,
            "dry_run": True,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"

        # Read the execution log and verify --csv-prices appears.
        log_path = Path(body["output"]["log_path"])
        log_content = log_path.read_text()
        assert "--csv-prices" in log_content
        assert str(prices_file) in log_content

    def test_dry_run_no_subprocess_calls(self, client, tmp_path):
        """Dry-run should not call any subprocess even with price_table."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n")

        payload = {
            **_NORWAY_BASE,
            "dry_run": True,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
            resp = client.post(f"/jobs/{job_id}/start")
            assert resp.json()["status"] == "completed"
            mock_run.assert_not_called()

    def test_dry_run_dummy_no_csv_prices_in_log(self, client):
        """Dry-run with dummy mode should NOT log --csv-prices."""
        payload = {
            **_NORWAY_BASE,
            "dry_run": True,
            # valuation_mode defaults to dummy
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        resp = client.post(f"/jobs/{job_id}/start")
        body = resp.json()

        assert body["status"] == "completed"
        log_path = Path(body["output"]["log_path"])
        log_content = log_path.read_text()
        assert "--csv-prices" not in log_content
