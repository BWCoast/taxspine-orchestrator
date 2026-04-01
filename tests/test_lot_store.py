"""Tests for lot store settings in the orchestrator.

Covers:
- Settings.LOT_STORE_DB exists and is within DATA_DIR by default.
- LOT_STORE_DB can be overridden via env / Settings constructor.
- _build_xrpl_command passes --lot-store (NOR_MULTI and XRPL modes persist lots natively).
- _build_nor_multi_command passes --lot-store.
- _build_csv_command does NOT pass --lot-store for PER_FILE Norway or UK.
- Dry-run logs confirm the flag is present for XRPL and NOR_MULTI, absent for PER_FILE and UK.
- Carry-forward CSV files (_maybe_write_carry_forward_csv) are no longer created.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.config import Settings, settings
from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    JobInput,
    PipelineMode,
)
from taxspine_orchestrator.services import JobService
from tests.conftest import start_and_wait


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ji_norway(**kwargs) -> JobInput:
    defaults = {"tax_year": 2025, "country": Country.NORWAY}
    defaults.update(kwargs)
    return JobInput(**defaults)


def _make_ji_uk(**kwargs) -> JobInput:
    defaults = {"tax_year": 2025, "country": Country.UK}
    defaults.update(kwargs)
    return JobInput(**defaults)


def _specs(*pairs) -> list[CsvFileSpec]:
    return [CsvFileSpec(path=p, source_type=t) for t, p in pairs]


def _make_ok():
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


# ── TestSettingsLotStoreDb ────────────────────────────────────────────────────


class TestSettingsLotStoreDb:
    def test_lot_store_db_exists_on_settings(self):
        assert hasattr(settings, "LOT_STORE_DB")

    def test_lot_store_db_is_path(self):
        assert isinstance(settings.LOT_STORE_DB, Path)

    def test_lot_store_db_default_within_data_dir(self):
        s = Settings()
        assert s.LOT_STORE_DB.parent == s.DATA_DIR

    def test_lot_store_db_filename(self):
        s = Settings()
        assert s.LOT_STORE_DB.name == "lots.db"

    def test_lot_store_db_overridable(self):
        s = Settings(LOT_STORE_DB=Path("/custom/lots.db"))
        assert s.LOT_STORE_DB == Path("/custom/lots.db")


# ── TestBuildXrplCommandLotStore ─────────────────────────────────────────────


class TestBuildXrplCommandLotStore:
    """taxspine-xrpl-nor supports --lot-store; flag must appear in command."""

    def _cmd(self, html_path: Path, **kwargs) -> list[str]:
        ji = _make_ji_norway(**kwargs)
        return JobService._build_xrpl_command(
            ji,
            account="rN7n3473SaZBCG4dFL83w7PB5FBBfvXMUT",
            html_path=html_path,
            csv_files=[],
        )

    def test_lot_store_flag_present(self, tmp_path):
        cmd = self._cmd(tmp_path / "out.html")
        assert "--lot-store" in cmd

    def test_lot_store_path_matches_settings(self, tmp_path):
        cmd = self._cmd(tmp_path / "out.html")
        idx = cmd.index("--lot-store")
        assert cmd[idx + 1] == str(settings.LOT_STORE_DB)


# ── TestBuildCsvCommandLotStore ───────────────────────────────────────────────


class TestBuildCsvCommandLotStore:
    def _cmd_norway(self, html_path: Path, spec: CsvFileSpec) -> list[str]:
        ji = _make_ji_norway()
        return JobService._build_csv_command(ji, csv_spec=spec, html_path=html_path)

    def _cmd_uk(self, html_path: Path, spec: CsvFileSpec) -> list[str]:
        ji = _make_ji_uk()
        return JobService._build_csv_command(ji, csv_spec=spec, html_path=html_path)

    def test_norway_per_file_does_not_include_lot_store(self, tmp_path):
        """taxspine-nor-report (PER_FILE) does not support --lot-store."""
        spec = CsvFileSpec(path="/data/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        cmd = self._cmd_norway(tmp_path / "out.html", spec)
        assert "--lot-store" not in cmd

    def test_uk_does_not_include_lot_store(self, tmp_path):
        """UK pipeline does not support --lot-store."""
        spec = CsvFileSpec(path="/data/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        cmd = self._cmd_uk(tmp_path / "out.html", spec)
        assert "--lot-store" not in cmd


# ── TestBuildNorMultiCommandLotStore ──────────────────────────────────────────


class TestBuildNorMultiCommandLotStore:
    """taxspine-nor-multi supports --lot-store; flag must appear in command."""

    def _cmd(self, html_path: Path) -> list[str]:
        ji = _make_ji_norway()
        specs = _specs((CsvSourceType.GENERIC_EVENTS, "/data/events.csv"))
        return JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=html_path)

    def test_lot_store_flag_present(self, tmp_path):
        cmd = self._cmd(tmp_path / "out.html")
        assert "--lot-store" in cmd

    def test_lot_store_path_matches_settings(self, tmp_path):
        cmd = self._cmd(tmp_path / "out.html")
        idx = cmd.index("--lot-store")
        assert cmd[idx + 1] == str(settings.LOT_STORE_DB)


# ── TestDryRunLotStore ────────────────────────────────────────────────────────


class TestDryRunLotStore:
    def test_xrpl_dry_run_log_contains_lot_store(self, client, tmp_path):
        """taxspine-xrpl-nor supports --lot-store; must appear in dry-run log."""
        with patch("taxspine_orchestrator.services.Path.is_file", return_value=True):
            resp = client.post("/jobs", json={
                "xrpl_accounts": ["rN7n3473SaZBCG4dFL83w7PB5FBBfvXMUT"],
                "tax_year": 2025,
                "country": "norway",
                "dry_run": True,
            })
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" in log

    def test_csv_norway_dry_run_log_does_not_contain_lot_store(self, client, tmp_path):
        """taxspine-nor-report (PER_FILE) doesn't support --lot-store; must be absent from log."""
        csv_file = tmp_path / "events.csv"
        csv_file.write_text("header\nrow\n", encoding="utf-8")
        with patch("taxspine_orchestrator.services.Path.is_file", return_value=True):
            resp = client.post("/jobs", json={
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "tax_year": 2025,
                "country": "norway",
                "dry_run": True,
            })
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" not in log

    def test_csv_norway_nor_multi_dry_run_log_contains_lot_store(self, client, tmp_path):
        """taxspine-nor-multi supports --lot-store; must appear in dry-run log."""
        csv_file = tmp_path / "events.csv"
        csv_file.write_text("header\nrow\n", encoding="utf-8")
        with patch("taxspine_orchestrator.services.Path.is_file", return_value=True):
            resp = client.post("/jobs", json={
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "tax_year": 2025,
                "country": "norway",
                "pipeline_mode": "nor_multi",
                "dry_run": True,
            })
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" in log

    def test_csv_uk_dry_run_log_does_not_contain_lot_store(self, client, tmp_path):
        """UK pipeline does not support --lot-store; must be absent from log."""
        csv_file = tmp_path / "events.csv"
        csv_file.write_text("header\nrow\n", encoding="utf-8")
        with patch("taxspine_orchestrator.services.Path.is_file", return_value=True):
            resp = client.post("/jobs", json={
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "tax_year": 2025,
                "country": "uk",
                "dry_run": True,
            })
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" not in log


# ── TestCarryForwardChainIntegration ─────────────────────────────────────────


class TestCarryForwardChainIntegration:
    """Command structure tests for two-run carry-forward chain via --lot-store."""

    def test_nor_multi_first_run_command_has_lot_store(self, tmp_path):
        """First-year NOR_MULTI run: lot store path present even if store is new."""
        ji = _make_ji_norway(tax_year=2024)
        specs = _specs((CsvSourceType.GENERIC_EVENTS, "/data/events.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "out.html")
        assert "--lot-store" in cmd

    def test_nor_multi_second_run_command_same_lot_store_path(self, tmp_path):
        """Second-year run uses same LOT_STORE_DB path as first year."""
        specs = _specs((CsvSourceType.GENERIC_EVENTS, "/data/events.csv"))
        html = tmp_path / "out.html"

        cmd_2024 = JobService._build_nor_multi_command(
            _make_ji_norway(tax_year=2024), csv_specs=specs, html_path=html
        )
        cmd_2025 = JobService._build_nor_multi_command(
            _make_ji_norway(tax_year=2025), csv_specs=specs, html_path=html
        )

        idx_2024 = cmd_2024.index("--lot-store")
        idx_2025 = cmd_2025.index("--lot-store")
        assert cmd_2024[idx_2024 + 1] == cmd_2025[idx_2025 + 1] == str(settings.LOT_STORE_DB)

    def test_xrpl_command_has_lot_store(self, tmp_path):
        """XRPL pipeline command includes --lot-store."""
        ji = _make_ji_norway(tax_year=2025)
        cmd = JobService._build_xrpl_command(
            ji,
            account="rN7n3473SaZBCG4dFL83w7PB5FBBfvXMUT",
            html_path=tmp_path / "out.html",
            csv_files=[],
        )
        assert "--lot-store" in cmd
        idx = cmd.index("--lot-store")
        assert cmd[idx + 1] == str(settings.LOT_STORE_DB)


# ── TestCarryForwardCsvRemoved ────────────────────────────────────────────────


class TestCarryForwardCsvRemoved:
    """Confirm that no carry_forward_*.csv files are created during job execution."""

    def test_no_carry_forward_csv_in_output_dir(self, tmp_path):
        """Job execution must not create carry_forward_*.csv files.

        Confirms _maybe_write_carry_forward_csv is fully removed from the
        execution path: no file matching carry_forward_*.csv should exist
        in the output directory after a mocked successful run.
        """
        csv_file = tmp_path / "events.csv"
        csv_file.write_text(
            "event_id,timestamp,event_type,source,account,"
            "asset_in,amount_in,asset_out,amount_out,"
            "fee_asset,fee_amount,tx_hash,exchange_tx_id,label,"
            "complex_tax_treatment,note\n",
            encoding="utf-8",
        )
        with patch("subprocess.run", return_value=_make_ok()):
            client = TestClient(app)
            resp = client.post("/jobs", json={
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "tax_year": 2025,
                "country": "norway",
                "pipeline_mode": "nor_multi",
            })
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        # Locate the output directory from the log path
        log_path = Path(job["output"]["log_path"])
        output_dir = log_path.parent
        carry_files = list(output_dir.glob("carry_forward_*.csv"))
        assert carry_files == [], (
            f"Unexpected carry_forward CSV files found: {carry_files}"
        )
