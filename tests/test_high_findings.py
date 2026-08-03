"""test_high_findings.py — Tests for high-priority audit findings.

Covers:
- BE-02: _build_csv_command passes --review-json PATH (Norway only)
- TL-05: pipeline_mode_used field in JobOutput
- INF-03: /maintenance/disk-usage and /maintenance/cleanup endpoints
- Auth gate on maintenance endpoints when ORCHESTRATOR_KEY is set
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _make_client() -> TestClient:
    from taxspine_orchestrator.main import app
    return TestClient(app)


def _make_norway_job_input():
    from taxspine_orchestrator.models import (
        CsvFileSpec,
        CsvSourceType,
        Country,
        JobInput,
        PipelineMode,
        ValuationMode,
    )
    return JobInput(
        xrpl_accounts=[],
        csv_files=[CsvFileSpec(path="/tmp/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)],
        tax_year=2025,
        country=Country.NORWAY,
        pipeline_mode=PipelineMode.PER_FILE,
        valuation_mode=ValuationMode.DUMMY,
    )


def _make_uk_job_input():
    from taxspine_orchestrator.models import (
        CsvFileSpec,
        CsvSourceType,
        Country,
        JobInput,
        PipelineMode,
        ValuationMode,
    )
    return JobInput(
        xrpl_accounts=[],
        csv_files=[CsvFileSpec(path="/tmp/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)],
        tax_year=2025,
        country=Country.UK,
        pipeline_mode=PipelineMode.PER_FILE,
        valuation_mode=ValuationMode.DUMMY,
    )


# ── TestBuildCsvCommandReviewJson (BE-02) ─────────────────────────────────────


class TestBuildCsvCommandReviewJson:
    """BE-02: _build_csv_command passes --review-json PATH for Norway jobs."""

    def test_csv_command_includes_review_json_when_provided(self):
        from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType
        from taxspine_orchestrator.services import JobService

        job_input = _make_norway_job_input()
        csv_spec = CsvFileSpec(path="/tmp/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        review_path = Path("/tmp/review.json")
        cmd = JobService._build_csv_command(
            job_input,
            csv_spec=csv_spec,
            html_path=Path("/tmp/report.html"),
            review_json_path=review_path,
        )
        assert "--review-json" in cmd
        assert str(review_path) in cmd

    def test_csv_command_omits_review_json_when_none(self):
        from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType
        from taxspine_orchestrator.services import JobService

        job_input = _make_norway_job_input()
        csv_spec = CsvFileSpec(path="/tmp/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        cmd = JobService._build_csv_command(
            job_input,
            csv_spec=csv_spec,
            html_path=Path("/tmp/report.html"),
            review_json_path=None,
        )
        assert "--review-json" not in cmd

    def test_csv_command_includes_rf1159_json(self):
        from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType
        from taxspine_orchestrator.services import JobService

        job_input = _make_norway_job_input()
        csv_spec = CsvFileSpec(path="/tmp/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        rf1159_path = Path("/tmp/rf.json")
        cmd = JobService._build_csv_command(
            job_input,
            csv_spec=csv_spec,
            html_path=Path("/tmp/report.html"),
            rf1159_json_path=rf1159_path,
        )
        assert "--rf1159-json" in cmd
        assert str(rf1159_path) in cmd

    def test_csv_command_review_json_uk_not_included(self):
        """--review-json is a Norway-only flag; UK commands must not include it."""
        from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType
        from taxspine_orchestrator.services import JobService

        job_input = _make_uk_job_input()
        csv_spec = CsvFileSpec(path="/tmp/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        cmd = JobService._build_csv_command(
            job_input,
            csv_spec=csv_spec,
            html_path=Path("/tmp/report.html"),
            review_json_path=Path("/tmp/review.json"),
        )
        assert "--review-json" not in cmd


# ── TestJobOutputPipelineModeUsed (TL-05) ─────────────────────────────────────


class TestJobOutputPipelineModeUsed:
    """TL-05: pipeline_mode_used is present in JobOutput and defaults to None."""

    def test_job_output_has_pipeline_mode_used_field(self):
        from taxspine_orchestrator.models import JobOutput

        assert hasattr(JobOutput(), "pipeline_mode_used")

    def test_job_output_pipeline_mode_used_defaults_none(self):
        from taxspine_orchestrator.models import JobOutput

        output = JobOutput()
        assert output.pipeline_mode_used is None

    def test_job_output_pipeline_mode_used_accepts_string(self):
        from taxspine_orchestrator.models import JobOutput

        output = JobOutput(pipeline_mode_used="per_file")
        assert output.pipeline_mode_used == "per_file"


# ── TestDiskUsageEndpoint (INF-03) ────────────────────────────────────────────


class TestDiskUsageEndpoint:
    """INF-03: GET /maintenance/disk-usage returns 200 with expected shape."""

    def test_disk_usage_returns_200(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.OUTPUT_DIR = td_path / "output"
                mock_s.UPLOAD_DIR = td_path / "uploads"
                mock_s.PRICES_DIR = td_path / "prices"
                mock_s.ORCHESTRATOR_KEY = ""
                r = client.get("/maintenance/disk-usage")
        assert r.status_code == 200

    def test_disk_usage_has_expected_keys(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.OUTPUT_DIR = td_path / "output"
                mock_s.UPLOAD_DIR = td_path / "uploads"
                mock_s.PRICES_DIR = td_path / "prices"
                mock_s.ORCHESTRATOR_KEY = ""
                data = client.get("/maintenance/disk-usage").json()
        assert "output_dir" in data
        assert "upload_dir" in data
        assert "prices_dir" in data

    def test_disk_usage_each_dir_has_file_count(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.OUTPUT_DIR = td_path / "output"
                mock_s.UPLOAD_DIR = td_path / "uploads"
                mock_s.PRICES_DIR = td_path / "prices"
                mock_s.ORCHESTRATOR_KEY = ""
                data = client.get("/maintenance/disk-usage").json()
        for key in ("output_dir", "upload_dir", "prices_dir"):
            assert "file_count" in data[key], f"missing file_count in {key}"


# ── TestCleanupEndpoint (INF-03) ──────────────────────────────────────────────


class TestCleanupEndpoint:
    """INF-03: POST /maintenance/cleanup works correctly with dry_run semantics."""

    def test_cleanup_dry_run_default_returns_200(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.OUTPUT_DIR = td_path / "output"
                mock_s.UPLOAD_DIR = td_path / "uploads"
                mock_s.ORCHESTRATOR_KEY = ""
                r = client.post("/maintenance/cleanup")
        assert r.status_code == 200

    def test_cleanup_dry_run_true_no_deletion(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.OUTPUT_DIR = td_path / "output"
                mock_s.UPLOAD_DIR = td_path / "uploads"
                mock_s.ORCHESTRATOR_KEY = ""
                r = client.post("/maintenance/cleanup?dry_run=true&max_age_days=1")
        data = r.json()
        assert data["dry_run"] is True

    def test_cleanup_returns_files_affected_count(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.OUTPUT_DIR = td_path / "output"
                mock_s.UPLOAD_DIR = td_path / "uploads"
                mock_s.ORCHESTRATOR_KEY = ""
                r = client.post("/maintenance/cleanup")
        data = r.json()
        assert "files_affected" in data
        assert isinstance(data["files_affected"], int)

    def test_cleanup_deletes_old_files_when_dry_run_false(self):
        """With dry_run=false and max_age_days=0, old files are deleted."""
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            output_dir = td_path / "output"
            output_dir.mkdir(parents=True)
            old_file = output_dir / "old_report.html"
            old_file.write_text("<html>old</html>")
            # Set mtime to 10 days in the past.
            old_mtime = time.time() - (10 * 86400)
            import os
            os.utime(str(old_file), (old_mtime, old_mtime))

            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.OUTPUT_DIR = output_dir
                mock_s.UPLOAD_DIR = td_path / "uploads"
                mock_s.ORCHESTRATOR_KEY = ""
                r = client.post("/maintenance/cleanup?dry_run=false&max_age_days=1")

        data = r.json()
        assert data["files_affected"] >= 1
        assert not old_file.exists()

    def test_cleanup_route_registered(self):
        client = _make_client()
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/maintenance/cleanup" in paths


# ── TestMaintenanceAuth (INF-03 / auth) ───────────────────────────────────────


class TestMaintenanceAuth:
    """Maintenance endpoints require auth when ORCHESTRATOR_KEY is configured."""

    def test_maintenance_endpoints_require_auth(self):
        """Unauthenticated calls to /maintenance/disk-usage return 401 when key is set."""
        client = _make_client()
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            r = client.get("/maintenance/disk-usage")
            assert r.status_code == 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_maintenance_cleanup_requires_auth_when_key_set(self):
        """Unauthenticated POST /maintenance/cleanup returns 401 when key is set."""
        client = _make_client()
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            r = client.post("/maintenance/cleanup")
            assert r.status_code == 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_maintenance_accessible_when_key_empty(self):
        """When ORCHESTRATOR_KEY is empty, /maintenance/disk-usage is accessible."""
        client = _make_client()
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = ""  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                with patch("taxspine_orchestrator.main.settings") as mock_s:
                    mock_s.OUTPUT_DIR = td_path / "output"
                    mock_s.UPLOAD_DIR = td_path / "uploads"
                    mock_s.PRICES_DIR = td_path / "prices"
                    mock_s.ORCHESTRATOR_KEY = ""
                    r = client.get("/maintenance/disk-usage")
            assert r.status_code != 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]
