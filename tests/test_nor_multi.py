"""Tests for the taxspine-nor-multi execution mode.

Covers:
- PipelineMode enum existence and values.
- JobInput accepts pipeline_mode field; default is per_file.
- _build_nor_multi_command: correct CLI binary, --source TYPE:PATH args,
  --year, --html-output, optional --csv-prices and --debug-valuation.
- CSV-only Norway job with pipeline_mode=nor_multi: single subprocess call
  to taxspine-nor-multi (not taxspine-nor-report).
- Source type mapping: generic_events, coinbase, firi.
- Failure handling: nor_multi subprocess returns non-zero → FAILED.
- Dry-run nor_multi: [would run] log entry with taxspine-nor-multi.
- UK country + nor_multi: falls back to per_file (nor_multi is Norway-only).
- XRPL + nor_multi: XRPL step runs normally; CSV step ignored (has_xrpl=True).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    JobInput,
    PipelineMode,
    ValuationMode,
)
from taxspine_orchestrator.services import JobService
from tests.conftest import start_and_wait


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ok(**overrides):
    m = MagicMock()
    m.returncode = overrides.get("returncode", 0)
    m.stdout = overrides.get("stdout", "")
    m.stderr = overrides.get("stderr", "")
    return m


def _make_fail(rc: int = 1, stderr: str = "boom"):
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
    for name in ("generic.csv", "coinbase.csv", "firi.csv"):
        (tmp_path / name).write_text("header\nrow\n", encoding="utf-8")
    return tmp_path


# ── TestPipelineModeEnum ──────────────────────────────────────────────────────


class TestPipelineModeEnum:
    def test_per_file_value(self):
        assert PipelineMode.PER_FILE == "per_file"

    def test_nor_multi_value(self):
        assert PipelineMode.NOR_MULTI == "nor_multi"

    def test_is_str_enum(self):
        assert isinstance(PipelineMode.PER_FILE, str)


# ── TestJobInputPipelineMode ──────────────────────────────────────────────────


class TestJobInputPipelineMode:
    def test_default_is_per_file(self):
        ji = JobInput(tax_year=2025, country=Country.NORWAY)
        assert ji.pipeline_mode == PipelineMode.PER_FILE

    def test_nor_multi_accepted(self):
        ji = JobInput(
            tax_year=2025,
            country=Country.NORWAY,
            pipeline_mode=PipelineMode.NOR_MULTI,
        )
        assert ji.pipeline_mode == PipelineMode.NOR_MULTI

    def test_string_coercion(self):
        ji = JobInput(
            tax_year=2025, country=Country.NORWAY, pipeline_mode="nor_multi"  # type: ignore[arg-type]
        )
        assert ji.pipeline_mode == PipelineMode.NOR_MULTI


# ── TestBuildNorMultiCommand ──────────────────────────────────────────────────


class TestBuildNorMultiCommand:
    def _job_input(self, **kwargs) -> JobInput:
        defaults = {"tax_year": 2025, "country": Country.NORWAY}
        defaults.update(kwargs)
        return JobInput(**defaults)

    def _specs(self, *pairs) -> list[CsvFileSpec]:
        return [CsvFileSpec(path=p, source_type=t) for t, p in pairs]

    def test_binary_is_taxspine_nor_multi(self, tmp_path):
        ji = self._job_input()
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/data/events.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "out.html")
        assert cmd[0] == "taxspine-nor-multi"

    def test_year_flag(self, tmp_path):
        ji = self._job_input(tax_year=2024)
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/data/e.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "out.html")
        assert "--year" in cmd
        year_idx = cmd.index("--year")
        assert cmd[year_idx + 1] == "2024"

    def test_html_output_flag(self, tmp_path):
        ji = self._job_input()
        html = tmp_path / "report.html"
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/data/e.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=html)
        assert "--html-output" in cmd
        idx = cmd.index("--html-output")
        assert cmd[idx + 1] == str(html)

    def test_generic_events_source_type(self, tmp_path):
        ji = self._job_input()
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/data/events.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        assert "--source" in cmd
        idx = cmd.index("--source")
        assert cmd[idx + 1] == "generic_events:/data/events.csv"

    def test_coinbase_source_type(self, tmp_path):
        ji = self._job_input()
        specs = self._specs((CsvSourceType.COINBASE_CSV, "/data/cb.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        idx = cmd.index("--source")
        assert cmd[idx + 1] == "coinbase:/data/cb.csv"

    def test_firi_source_type(self, tmp_path):
        ji = self._job_input()
        specs = self._specs((CsvSourceType.FIRI_CSV, "/data/firi.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        idx = cmd.index("--source")
        assert cmd[idx + 1] == "firi:/data/firi.csv"

    def test_multiple_sources_all_present(self, tmp_path):
        ji = self._job_input()
        specs = self._specs(
            (CsvSourceType.GENERIC_EVENTS, "/a.csv"),
            (CsvSourceType.COINBASE_CSV, "/b.csv"),
            (CsvSourceType.FIRI_CSV, "/c.csv"),
        )
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        source_args = [
            cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--source"
        ]
        assert len(source_args) == 3
        assert "generic_events:/a.csv" in source_args
        assert "coinbase:/b.csv" in source_args
        assert "firi:/c.csv" in source_args

    def test_source_order_preserved(self, tmp_path):
        ji = self._job_input()
        specs = self._specs(
            (CsvSourceType.GENERIC_EVENTS, "/first.csv"),
            (CsvSourceType.FIRI_CSV, "/second.csv"),
        )
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        source_args = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--source"]
        assert source_args[0].endswith("first.csv")
        assert source_args[1].endswith("second.csv")

    def test_no_csv_prices_by_default(self, tmp_path):
        ji = self._job_input()
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/e.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        assert "--csv-prices" not in cmd

    def test_csv_prices_added_when_price_table_mode(self, tmp_path):
        ji = self._job_input(
            valuation_mode=ValuationMode.PRICE_TABLE,
            csv_prices_path="/prices/prices.csv",
        )
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/e.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        assert "--csv-prices" in cmd
        idx = cmd.index("--csv-prices")
        assert cmd[idx + 1] == "/prices/prices.csv"

    def test_debug_valuation_flag(self, tmp_path):
        ji = self._job_input(debug_valuation=True)
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/e.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        assert "--debug-valuation" in cmd

    def test_no_debug_valuation_by_default(self, tmp_path):
        ji = self._job_input()
        specs = self._specs((CsvSourceType.GENERIC_EVENTS, "/e.csv"))
        cmd = JobService._build_nor_multi_command(ji, csv_specs=specs, html_path=tmp_path / "r.html")
        assert "--debug-valuation" not in cmd

    def test_empty_csv_specs_produces_valid_command(self, tmp_path):
        ji = self._job_input()
        cmd = JobService._build_nor_multi_command(ji, csv_specs=[], html_path=tmp_path / "r.html")
        assert cmd[0] == "taxspine-nor-multi"
        assert "--source" not in cmd


# ── TestNorMultiExecution ─────────────────────────────────────────────────────


class TestNorMultiExecution:
    """CSV-only Norway jobs with pipeline_mode=nor_multi."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_calls_single_subprocess(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [
                {"path": str(csv_dir / "generic.csv"), "source_type": "generic_events"},
                {"path": str(csv_dir / "coinbase.csv"), "source_type": "coinbase_csv"},
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        start_and_wait(client, job_id)

        # Only one subprocess call total.
        assert mock_run.call_count == 1

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_uses_correct_binary(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [str(csv_dir / "generic.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-nor-multi"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_not_taxspine_nor_report(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [
                str(csv_dir / "generic.csv"),
                str(csv_dir / "coinbase.csv"),
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        for call in mock_run.call_args_list:
            assert call[0][0][0] != "taxspine-nor-report"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_source_args_in_command(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        generic_path = str(csv_dir / "generic.csv")
        coinbase_path = str(csv_dir / "coinbase.csv")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [
                {"path": generic_path, "source_type": "generic_events"},
                {"path": coinbase_path, "source_type": "coinbase_csv"},
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        source_args = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--source"]
        assert any("generic_events:" in s and generic_path in s for s in source_args)
        assert any("coinbase:" in s and coinbase_path in s for s in source_args)

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_completes_successfully(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [str(csv_dir / "generic.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_failure_marks_job_failed(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_fail(rc=2)

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [str(csv_dir / "generic.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "taxspine-nor-multi failed" in body["output"]["error_message"]

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_failure_includes_rc(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_fail(rc=3)

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [str(csv_dir / "generic.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert "rc=3" in body["output"]["error_message"]

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_year_in_command(self, mock_run, client, csv_dir):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2024,
            "country": "norway",
            "pipeline_mode": "nor_multi",
            "csv_files": [str(csv_dir / "generic.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        year_idx = cmd.index("--year")
        assert cmd[year_idx + 1] == "2024"


# ── TestNorMultiDryRun ────────────────────────────────────────────────────────


class TestNorMultiDryRun:
    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_nor_multi_logs_command(self, mock_run, client, csv_dir):
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "dry_run": True,
            "pipeline_mode": "nor_multi",
            "csv_files": [str(csv_dir / "generic.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert mock_run.call_count == 0  # no real subprocess

        log_path = body["output"]["log_path"]
        log_text = Path(log_path).read_text(encoding="utf-8")
        assert "taxspine-nor-multi" in log_text
        assert "[would run]" in log_text

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_nor_multi_single_log_entry(self, mock_run, client, csv_dir):
        """Nor-multi dry run emits exactly one [would run] entry."""
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "dry_run": True,
            "pipeline_mode": "nor_multi",
            "csv_files": [
                str(csv_dir / "generic.csv"),
                str(csv_dir / "coinbase.csv"),
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        log_text = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        would_run_lines = [ln for ln in log_text.splitlines() if "[would run]" in ln]
        assert len(would_run_lines) == 1


# ── TestNorMultiCountryFallback ───────────────────────────────────────────────


class TestNorMultiCountryFallback:
    """nor_multi is Norway-only; UK falls back to per_file."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_nor_multi_falls_back_to_per_file(self, mock_run, client, csv_dir):
        """UK + nor_multi → still uses taxspine-uk-report (per-file fallback)."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "uk",
            "pipeline_mode": "nor_multi",
            "csv_files": [str(csv_dir / "generic.csv")],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-uk-report"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_nor_multi_two_files_two_calls(self, mock_run, client, csv_dir):
        """UK + nor_multi → per-file loop → two subprocess calls."""
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "uk",
            "pipeline_mode": "nor_multi",
            "csv_files": [
                str(csv_dir / "generic.csv"),
                str(csv_dir / "coinbase.csv"),
            ],
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 2


# ── TestNorMultiXrplInteraction ───────────────────────────────────────────────


class TestNorMultiXrplInteraction:
    """XRPL jobs ignore pipeline_mode (CSV step is skipped when has_xrpl)."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_xrpl_only_nor_multi_ignored(self, mock_run, client):
        mock_run.return_value = _make_ok()

        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "pipeline_mode": "nor_multi",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-xrpl-nor"
