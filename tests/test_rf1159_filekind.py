"""Tests for RF-1159 JSON output wiring.

Covers:
- FileKind.RF1159 exists and maps to rf1159_json_path on JobOutput.
- JobOutput has rf1159_json_path and rf1159_json_paths fields.
- _build_csv_command (Norway) includes --rf1159-json when rf1159_json_path given.
- _build_csv_command (UK) does NOT include --rf1159-json.
- _build_nor_multi_command includes --rf1159-json when rf1159_json_path given.
- Dry-run NOR_MULTI job log contains --rf1159-json.
- Dry-run PER_FILE Norway job log contains --rf1159-json.
- Dry-run UK job log does NOT contain --rf1159-json.
- GET /jobs/{id}/files returns rf1159 kind when rf1159_json_path is set.
- GET /jobs/{id}/files/rf1159 returns 404 when path is None.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import FileKind, _KIND_TO_FIELD, _KIND_MEDIA_TYPE, app
from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    JobInput,
    JobOutput,
    PipelineMode,
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


def _norway_input(**kwargs) -> JobInput:
    return JobInput(tax_year=2025, country=Country.NORWAY, **kwargs)


def _uk_input(**kwargs) -> JobInput:
    return JobInput(tax_year=2025, country=Country.UK, **kwargs)


# ── TestFileKindEnum ───────────────────────────────────────────────────────────


class TestFileKindEnum:
    def test_rf1159_exists(self):
        assert FileKind.RF1159 == "rf1159"

    def test_rf1159_maps_to_job_output_field(self):
        assert _KIND_TO_FIELD[FileKind.RF1159] == "rf1159_json_path"

    def test_rf1159_media_type_is_json(self):
        assert _KIND_MEDIA_TYPE[FileKind.RF1159] == "application/json"


# ── TestJobOutputFields ────────────────────────────────────────────────────────


class TestJobOutputFields:
    def test_rf1159_json_path_default_none(self):
        out = JobOutput()
        assert out.rf1159_json_path is None

    def test_rf1159_json_paths_default_empty(self):
        out = JobOutput()
        assert out.rf1159_json_paths == []

    def test_rf1159_json_path_set(self):
        out = JobOutput(rf1159_json_path="/data/rf1159.json")
        assert out.rf1159_json_path == "/data/rf1159.json"

    def test_rf1159_json_paths_list(self):
        out = JobOutput(rf1159_json_paths=["/data/a.json", "/data/b.json"])
        assert len(out.rf1159_json_paths) == 2


# ── TestBuildCsvCommandRf1159 ──────────────────────────────────────────────────


class TestBuildCsvCommandRf1159:
    def _spec(self) -> CsvFileSpec:
        return CsvFileSpec(source_type=CsvSourceType.GENERIC_EVENTS, path="/tmp/e.csv")

    def test_norway_includes_rf1159_json(self, tmp_path):
        rf1159 = tmp_path / "rf1159.json"
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            cmd = JobService._build_csv_command(
                _norway_input(),
                csv_spec=self._spec(),
                html_path=tmp_path / "r.html",
                rf1159_json_path=rf1159,
            )
        assert "--rf1159-json" in cmd
        idx = cmd.index("--rf1159-json")
        assert cmd[idx + 1] == str(rf1159)

    def test_norway_omits_rf1159_json_when_none(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            cmd = JobService._build_csv_command(
                _norway_input(),
                csv_spec=self._spec(),
                html_path=tmp_path / "r.html",
                rf1159_json_path=None,
            )
        assert "--rf1159-json" not in cmd

    def test_uk_does_not_include_rf1159_json(self, tmp_path):
        """UK CLI has no --rf1159-json flag; never pass it."""
        rf1159 = tmp_path / "rf1159.json"
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_REPORT_CLI = "taxspine-nor-report"
            mock_s.TAXSPINE_UK_REPORT_CLI = "taxspine-uk-report"
            cmd = JobService._build_csv_command(
                _uk_input(),
                csv_spec=self._spec(),
                html_path=tmp_path / "r.html",
                rf1159_json_path=rf1159,
            )
        assert "--rf1159-json" not in cmd


# ── TestBuildNorMultiCommandRf1159 ─────────────────────────────────────────────


class TestBuildNorMultiCommandRf1159:
    def test_includes_rf1159_json(self, tmp_path):
        rf1159 = tmp_path / "rf1159.json"
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_MULTI_CLI = "taxspine-nor-multi"
            cmd = JobService._build_nor_multi_command(
                _norway_input(),
                csv_specs=[CsvFileSpec(source_type=CsvSourceType.GENERIC_EVENTS, path="/tmp/e.csv")],
                html_path=tmp_path / "r.html",
                rf1159_json_path=rf1159,
            )
        assert "--rf1159-json" in cmd
        idx = cmd.index("--rf1159-json")
        assert cmd[idx + 1] == str(rf1159)

    def test_omits_rf1159_json_when_none(self, tmp_path):
        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.DEDUP_DIR = tmp_path / "dedup"
            mock_s.LOT_STORE_DB = tmp_path / "lots.db"
            mock_s.TAXSPINE_NOR_MULTI_CLI = "taxspine-nor-multi"
            cmd = JobService._build_nor_multi_command(
                _norway_input(),
                csv_specs=[CsvFileSpec(source_type=CsvSourceType.GENERIC_EVENTS, path="/tmp/e.csv")],
                html_path=tmp_path / "r.html",
                rf1159_json_path=None,
            )
        assert "--rf1159-json" not in cmd


# ── TestDryRunRf1159Logged ────────────────────────────────────────────────────


class TestDryRunRf1159Logged:
    """Dry-run job logs should contain --rf1159-json for Norway, not for UK."""

    def test_nor_multi_dry_run_logs_rf1159(self, client, tmp_path):
        resp = client.post("/jobs", json={
            "xrpl_accounts": [],
            "csv_files": [{"path": str(tmp_path / "e.csv"), "source_type": "generic_events"}],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "dry_run": True,
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)
        assert body["status"] == "completed"
        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--rf1159-json" in log

    def test_per_file_norway_dry_run_logs_rf1159(self, client, tmp_path):
        resp = client.post("/jobs", json={
            "xrpl_accounts": [],
            "csv_files": [{"path": str(tmp_path / "e.csv"), "source_type": "generic_events"}],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "per_file",
            "dry_run": True,
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)
        assert body["status"] == "completed"
        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--rf1159-json" in log

    def test_uk_dry_run_does_not_log_rf1159(self, client, tmp_path):
        resp = client.post("/jobs", json={
            "xrpl_accounts": [],
            "csv_files": [{"path": str(tmp_path / "e.csv"), "source_type": "generic_events"}],
            "tax_year": 2025,
            "country": "uk",
            "dry_run": True,
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)
        assert body["status"] == "completed"
        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--rf1159-json" not in log

    def test_xrpl_dry_run_logs_rf1159(self, client):
        """Norway XRPL dry-run log must contain --rf1159-json (API-01 fix).

        Previously this flag was silently dropped from the XRPL command
        builder. The test name and assertion are updated to reflect the
        now-correct behaviour.
        """
        resp = client.post("/jobs", json={
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "dry_run": True,
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)
        assert body["status"] == "completed"
        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--rf1159-json" in log


# ── TestRf1159FileEndpoints ────────────────────────────────────────────────────


class TestRf1159FileEndpoints:
    """GET /jobs/{id}/files and GET /jobs/{id}/files/rf1159."""

    def test_files_endpoint_omits_rf1159_when_none(self, client, tmp_path):
        """rf1159 should not appear in files listing when path is None."""
        resp = client.post("/jobs", json={
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "dry_run": True,
        })
        job_id = resp.json()["id"]
        start_and_wait(client, job_id)
        files = client.get(f"/jobs/{job_id}/files").json()
        assert "rf1159" not in files

    def test_files_rf1159_404_when_no_path(self, client, tmp_path):
        """Download endpoint returns 404 when rf1159_json_path is None."""
        resp = client.post("/jobs", json={
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "dry_run": True,
        })
        job_id = resp.json()["id"]
        start_and_wait(client, job_id)
        r = client.get(f"/jobs/{job_id}/files/rf1159")
        assert r.status_code == 404
