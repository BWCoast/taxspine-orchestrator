"""Tests for the dedup-store wiring in the orchestrator.

Covers:
- Settings.DEDUP_DIR exists and is created by ensure_dirs().
- JobService._dedup_store_path() naming convention for all source slugs.
- _build_xrpl_command includes --dedup-store pointing inside DEDUP_DIR.
- _build_csv_command (Norway) includes --dedup-store for each CSV source type.
- _build_csv_command (UK) does NOT include --dedup-store.
- _build_nor_multi_command includes --dedup-store for nor_multi slug.
- Dry-run jobs: --dedup-store flag appears in the execution log.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    JobInput,
    ValuationMode,
)
from taxspine_orchestrator.services import JobService
from tests.conftest import start_and_wait


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


_NORWAY_XRPL_BASE = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}

_UK_BASE = {
    "xrpl_accounts": ["rGWrZyax5eXbi5gs49MRZKmm2zUivkrADN"],
    "tax_year": 2025,
    "country": "uk",
}


# ── TestSettings ──────────────────────────────────────────────────────────────


class TestSettings:
    def test_dedup_dir_exists_on_settings(self):
        from taxspine_orchestrator.config import settings
        assert hasattr(settings, "DEDUP_DIR")
        assert isinstance(settings.DEDUP_DIR, Path)

    def test_ensure_dirs_creates_dedup_dir(self, tmp_path):
        from taxspine_orchestrator.config import Settings
        s = Settings(
            TEMP_DIR=tmp_path / "tmp",
            OUTPUT_DIR=tmp_path / "output",
            UPLOAD_DIR=tmp_path / "uploads",
            DATA_DIR=tmp_path / "data",
            PRICES_DIR=tmp_path / "prices",
            LOT_STORE_DB=tmp_path / "data" / "lots.db",
            DEDUP_DIR=tmp_path / "data" / "dedup",
        )
        s.ensure_dirs()
        assert (tmp_path / "data" / "dedup").is_dir()


# ── TestDedupStorePath ────────────────────────────────────────────────────────


class TestDedupStorePath:
    """_dedup_store_path naming for each supported source slug."""

    def test_xrpl_account_slug(self, tmp_path):
        account = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            result = JobService._dedup_store_path(f"xrpl_{account}")
        assert result == tmp_path / f"xrpl_{account}.db"

    def test_generic_events_slug(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            result = JobService._dedup_store_path("generic_events")
        assert result == tmp_path / "generic_events.db"

    def test_coinbase_csv_slug(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            result = JobService._dedup_store_path("coinbase_csv")
        assert result == tmp_path / "coinbase_csv.db"

    def test_firi_csv_slug(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            result = JobService._dedup_store_path("firi_csv")
        assert result == tmp_path / "firi_csv.db"

    def test_nor_multi_slug(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            result = JobService._dedup_store_path("nor_multi")
        assert result == tmp_path / "nor_multi.db"

    def test_forward_slash_sanitised(self, tmp_path):
        """Path separators in slug are replaced to stay filesystem-safe."""
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            result = JobService._dedup_store_path("a/b/c")
        assert result == tmp_path / "a_b_c.db"

    def test_backslash_sanitised(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            result = JobService._dedup_store_path("a\\b")
        assert result == tmp_path / "a_b.db"

    def test_different_accounts_get_different_files(self, tmp_path):
        account_a = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
        account_b = "rGWrZyax5eXbi5gs49MRZKmm2zUivkrADN"
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path
            path_a = JobService._dedup_store_path(f"xrpl_{account_a}")
            path_b = JobService._dedup_store_path(f"xrpl_{account_b}")
        assert path_a != path_b


# ── TestBuildXrplCommandDedup ─────────────────────────────────────────────────


class TestBuildXrplCommandDedup:
    """_build_xrpl_command includes --dedup-store for the XRPL account."""

    def _make_job_input(self, account: str = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh") -> JobInput:
        return JobInput(
            xrpl_accounts=[account],
            tax_year=2025,
            country=Country.NORWAY,
        )

    def test_dedup_store_flag_present(self, tmp_path):
        account = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
        job_input = self._make_job_input(account)
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            cmd = JobService._build_xrpl_command(
                job_input,
                account=account,
                html_path=tmp_path / "report.html",
                csv_files=[],
            )
        assert "--dedup-store" in cmd
        idx = cmd.index("--dedup-store")
        assert cmd[idx + 1] == str(tmp_path / "dedup" / f"xrpl_{account}.db")

    def test_dedup_store_path_contains_account(self, tmp_path):
        account = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
        job_input = self._make_job_input(account)
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            cmd = JobService._build_xrpl_command(
                job_input,
                account=account,
                html_path=tmp_path / "report.html",
                csv_files=[],
            )
        idx = cmd.index("--dedup-store")
        assert account in cmd[idx + 1]

    def test_different_accounts_have_different_dedup_paths(self, tmp_path):
        account_a = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
        account_b = "rGWrZyax5eXbi5gs49MRZKmm2zUivkrADN"
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            cmd_a = JobService._build_xrpl_command(
                self._make_job_input(account_a),
                account=account_a,
                html_path=tmp_path / "a.html",
                csv_files=[],
            )
            cmd_b = JobService._build_xrpl_command(
                self._make_job_input(account_b),
                account=account_b,
                html_path=tmp_path / "b.html",
                csv_files=[],
            )
        path_a = cmd_a[cmd_a.index("--dedup-store") + 1]
        path_b = cmd_b[cmd_b.index("--dedup-store") + 1]
        assert path_a != path_b


# ── TestBuildCsvCommandDedup ──────────────────────────────────────────────────


class TestBuildCsvCommandDedup:
    """_build_csv_command includes --dedup-store for Norway CSV sources."""

    def _make_csv_spec(self, source_type: CsvSourceType, path: str = "/tmp/events.csv") -> CsvFileSpec:
        return CsvFileSpec(source_type=source_type, path=path)

    def _make_norway_input(self) -> JobInput:
        return JobInput(
            xrpl_accounts=[],
            tax_year=2025,
            country=Country.NORWAY,
        )

    def _make_uk_input(self) -> JobInput:
        return JobInput(
            xrpl_accounts=[],
            tax_year=2025,
            country=Country.UK,
        )

    def test_generic_events_has_dedup_store(self, tmp_path):
        spec = self._make_csv_spec(CsvSourceType.GENERIC_EVENTS)
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            cmd = JobService._build_csv_command(
                self._make_norway_input(),
                csv_spec=spec,
                html_path=tmp_path / "report.html",
            )
        assert "--dedup-store" in cmd
        idx = cmd.index("--dedup-store")
        assert "generic_events.db" in cmd[idx + 1]

    def test_coinbase_csv_has_dedup_store(self, tmp_path):
        spec = self._make_csv_spec(CsvSourceType.COINBASE_CSV)
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            cmd = JobService._build_csv_command(
                self._make_norway_input(),
                csv_spec=spec,
                html_path=tmp_path / "report.html",
            )
        assert "--dedup-store" in cmd
        idx = cmd.index("--dedup-store")
        assert "coinbase_csv.db" in cmd[idx + 1]

    def test_firi_csv_has_dedup_store(self, tmp_path):
        spec = self._make_csv_spec(CsvSourceType.FIRI_CSV)
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            cmd = JobService._build_csv_command(
                self._make_norway_input(),
                csv_spec=spec,
                html_path=tmp_path / "report.html",
            )
        assert "--dedup-store" in cmd
        idx = cmd.index("--dedup-store")
        assert "firi_csv.db" in cmd[idx + 1]

    def test_uk_csv_does_not_have_dedup_store(self, tmp_path):
        """UK jobs do not use a dedup store (Norway only for now)."""
        spec = self._make_csv_spec(CsvSourceType.GENERIC_EVENTS)
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            cmd = JobService._build_csv_command(
                self._make_uk_input(),
                csv_spec=spec,
                html_path=tmp_path / "report.html",
            )
        assert "--dedup-store" not in cmd

    def test_dedup_store_different_per_source_type(self, tmp_path):
        """Each CSV source type maps to a distinct .db file."""
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            norway_input = self._make_norway_input()
            cmd_ge = JobService._build_csv_command(
                norway_input,
                csv_spec=self._make_csv_spec(CsvSourceType.GENERIC_EVENTS),
                html_path=tmp_path / "r1.html",
            )
            cmd_cb = JobService._build_csv_command(
                norway_input,
                csv_spec=self._make_csv_spec(CsvSourceType.COINBASE_CSV),
                html_path=tmp_path / "r2.html",
            )
        path_ge = cmd_ge[cmd_ge.index("--dedup-store") + 1]
        path_cb = cmd_cb[cmd_cb.index("--dedup-store") + 1]
        assert path_ge != path_cb


# ── TestBuildNorMultiCommandDedup ─────────────────────────────────────────────


class TestBuildNorMultiCommandDedup:
    """_build_nor_multi_command includes --dedup-store nor_multi slug."""

    def _make_norway_input(self) -> JobInput:
        return JobInput(
            xrpl_accounts=[],
            tax_year=2025,
            country=Country.NORWAY,
        )

    def test_dedup_store_present(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_MULTI_CLI = "taxspine-nor-multi"
            cmd = JobService._build_nor_multi_command(
                self._make_norway_input(),
                csv_specs=[
                    CsvFileSpec(source_type=CsvSourceType.GENERIC_EVENTS, path="/tmp/e.csv"),
                ],
                html_path=tmp_path / "report.html",
            )
        assert "--dedup-store" in cmd
        idx = cmd.index("--dedup-store")
        assert "nor_multi.db" in cmd[idx + 1]

    def test_dedup_store_inside_dedup_dir(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_MULTI_CLI = "taxspine-nor-multi"
            cmd = JobService._build_nor_multi_command(
                self._make_norway_input(),
                csv_specs=[
                    CsvFileSpec(source_type=CsvSourceType.FIRI_CSV, path="/tmp/firi.csv"),
                ],
                html_path=tmp_path / "report.html",
            )
        idx = cmd.index("--dedup-store")
        dedup_path = Path(cmd[idx + 1])
        assert dedup_path.parent == tmp_path / "dedup"


# ── TestDryRunDedupStoreLogged ────────────────────────────────────────────────


class TestDryRunDedupStoreLogged:
    """Dry-run jobs log the --dedup-store flag in the execution log."""

    def test_xrpl_dry_run_logs_dedup_store(self, client, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_s.OUTPUT_DIR = tmp_path

            resp = client.post("/jobs", json={**_NORWAY_XRPL_BASE, "dry_run": True})
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--dedup-store" in log

    def test_dry_run_dedup_store_path_in_log(self, client, tmp_path):
        account = _NORWAY_XRPL_BASE["xrpl_accounts"][0]
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_s.OUTPUT_DIR = tmp_path

            resp = client.post("/jobs", json={**_NORWAY_XRPL_BASE, "dry_run": True})
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        # The log should contain the slug for the XRPL account.
        assert account in log or "xrpl_" in log
