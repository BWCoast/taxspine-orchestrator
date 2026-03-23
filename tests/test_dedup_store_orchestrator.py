"""Tests for dedup-store settings and path naming in the orchestrator.

Covers:
- Settings.DEDUP_DIR exists and is created by ensure_dirs().
- JobService._dedup_store_path() naming convention for all source slugs.
- _build_xrpl_command does NOT include --dedup-store (CLI doesn't support it).
- _build_csv_command does NOT include --dedup-store for Norway or UK.
- _build_nor_multi_command does NOT include --dedup-store.
- Dry-run jobs: --dedup-store flag is absent from the execution log.
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
    """taxspine-xrpl-nor does not accept --dedup-store; must not appear in command."""

    def _make_job_input(self, account: str = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh") -> JobInput:
        return JobInput(
            xrpl_accounts=[account],
            tax_year=2025,
            country=Country.NORWAY,
        )

    def test_dedup_store_flag_absent(self, tmp_path):
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
        assert "--dedup-store" not in cmd


# ── TestBuildCsvCommandDedup ──────────────────────────────────────────────────


class TestBuildCsvCommandDedup:
    """taxspine-nor-report and taxspine-uk-report do not accept --dedup-store."""

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

    def test_generic_events_does_not_have_dedup_store(self, tmp_path):
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
        assert "--dedup-store" not in cmd

    def test_coinbase_csv_does_not_have_dedup_store(self, tmp_path):
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
        assert "--dedup-store" not in cmd

    def test_firi_csv_does_not_have_dedup_store(self, tmp_path):
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
        assert "--dedup-store" not in cmd

    def test_uk_csv_does_not_have_dedup_store(self, tmp_path):
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


# ── TestBuildNorMultiCommandDedup ─────────────────────────────────────────────


class TestBuildNorMultiCommandDedup:
    """taxspine-nor-multi does not accept --dedup-store; must not appear in command."""

    def _make_norway_input(self) -> JobInput:
        return JobInput(
            xrpl_accounts=[],
            tax_year=2025,
            country=Country.NORWAY,
        )

    def test_dedup_store_absent(self, tmp_path):
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
        assert "--dedup-store" not in cmd


# ── TestDryRunDedupStoreLogged ────────────────────────────────────────────────


class TestDryRunDedupStoreLogged:
    """Dry-run jobs confirm --dedup-store is absent from the execution log."""

    def test_xrpl_dry_run_does_not_log_dedup_store(self, client, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_s.OUTPUT_DIR = tmp_path

            resp = client.post("/jobs", json={**_NORWAY_XRPL_BASE, "dry_run": True, "valuation_mode": "dummy"})
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--dedup-store" not in log
