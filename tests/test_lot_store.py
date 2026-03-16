"""Tests for lot store integration in the orchestrator.

Covers:
- Settings.LOT_STORE_DB exists and is within DATA_DIR by default.
- _build_xrpl_command includes --lot-store PATH.
- _build_csv_command includes --lot-store PATH for Norway, not for UK.
- _build_nor_multi_command includes --lot-store PATH.
- Dry-run XRPL job: [would run] log includes --lot-store.
- Dry-run CSV-only Norway per-file: [would run] log includes --lot-store.
- Dry-run CSV-only Norway nor_multi: [would run] log includes --lot-store.
- Dry-run CSV-only UK: [would run] log does NOT include --lot-store.
- LOT_STORE_DB can be overridden via env / Settings constructor.
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

    def test_lot_store_value_is_path_string(self, tmp_path):
        cmd = self._cmd(tmp_path / "out.html")
        idx = cmd.index("--lot-store")
        lot_path = cmd[idx + 1]
        assert lot_path.endswith("lots.db")

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

    def test_norway_csv_includes_lot_store(self, tmp_path):
        spec = CsvFileSpec(path="/data/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        cmd = self._cmd_norway(tmp_path / "out.html", spec)
        assert "--lot-store" in cmd

    def test_norway_csv_lot_store_value(self, tmp_path):
        spec = CsvFileSpec(path="/data/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        cmd = self._cmd_norway(tmp_path / "out.html", spec)
        idx = cmd.index("--lot-store")
        assert cmd[idx + 1] == str(settings.LOT_STORE_DB)

    def test_uk_csv_does_not_include_lot_store(self, tmp_path):
        spec = CsvFileSpec(path="/data/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        cmd = self._cmd_uk(tmp_path / "out.html", spec)
        assert "--lot-store" not in cmd


# ── TestBuildNorMultiCommandLotStore ──────────────────────────────────────────


class TestBuildNorMultiCommandLotStore:
    def _cmd(self, html_path: Path) -> list[str]:
        ji = _make_ji_norway()
        specs = _specs((CsvSourceType.GENERIC_EVENTS, "/data/events.csv"))
        return JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=html_path)

    def test_lot_store_flag_present(self, tmp_path):
        cmd = self._cmd(tmp_path / "out.html")
        assert "--lot-store" in cmd

    def test_lot_store_value(self, tmp_path):
        cmd = self._cmd(tmp_path / "out.html")
        idx = cmd.index("--lot-store")
        assert cmd[idx + 1] == str(settings.LOT_STORE_DB)


# ── TestDryRunLotStore ────────────────────────────────────────────────────────


class TestDryRunLotStore:
    def test_xrpl_dry_run_log_contains_lot_store(self, client, tmp_path):
        with patch("taxspine_orchestrator.services.Path.is_file", return_value=True):
            resp = client.post("/jobs", json={
                "xrpl_accounts": ["rN7n3473SaZBCG4dFL83w7PB5FBBfvXMUT"],
                "tax_year": 2025,
                "country": "norway",
                "dry_run": True,
            })
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" in log

    def test_csv_norway_dry_run_log_contains_lot_store(self, client, tmp_path):
        csv_file = tmp_path / "events.csv"
        csv_file.write_text("header\nrow\n", encoding="utf-8")
        with patch("taxspine_orchestrator.services.Path.is_file", return_value=True):
            resp = client.post("/jobs", json={
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "tax_year": 2025,
                "country": "norway",
                "dry_run": True,
            })
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" in log

    def test_csv_norway_nor_multi_dry_run_log_contains_lot_store(self, client, tmp_path):
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
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" in log

    def test_csv_uk_dry_run_log_does_not_contain_lot_store(self, client, tmp_path):
        csv_file = tmp_path / "events.csv"
        csv_file.write_text("header\nrow\n", encoding="utf-8")
        with patch("taxspine_orchestrator.services.Path.is_file", return_value=True):
            resp = client.post("/jobs", json={
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "tax_year": 2025,
                "country": "uk",
                "dry_run": True,
            })
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = start_and_wait(client, job_id)
        log = Path(job["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--lot-store" not in log
