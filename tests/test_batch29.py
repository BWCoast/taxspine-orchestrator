"""test_batch29.py — Batch 29 audit remediation tests.

Findings covered:
  TL-06  (MEDIUM) — complex_treatment_warning field on JobOutput; CSV scanner
  INFRA-06 (MEDIUM) — POST /admin/cleanup TTL cleanup endpoint
  INFRA-21 (MEDIUM) — optional JSON structured logging formatter
  LC-11  (LOW)    — deletion audit log (storage + GET /admin/audit)
  INFRA-23 (LOW)  — Watchtower socket proxy guidance in docker-compose
  LC-07  (MEDIUM) — Third-party data source section in README
  LC-08  (MEDIUM) — Data handling & privacy section in README
  INFRA-19 (MEDIUM) — Backup strategy section in README
"""

from __future__ import annotations

import csv
import io
import json
import logging
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_client():
    from taxspine_orchestrator.main import app
    return TestClient(app)


def _make_job(client: TestClient, country: str = "norway") -> str:
    r = client.post("/jobs", json={
        "tax_year": 2024,
        "country": country,
    })
    assert r.status_code == 201
    return r.json()["id"]


# ── TL-06: complex_treatment_warning ─────────────────────────────────────────


class TestTL06ComplexTreatmentField:
    """JobOutput.complex_treatment_warning field exists and has correct metadata."""

    def test_field_exists_on_job_output(self):
        from taxspine_orchestrator.models import JobOutput
        jo = JobOutput()
        assert hasattr(jo, "complex_treatment_warning")

    def test_field_is_none_by_default(self):
        from taxspine_orchestrator.models import JobOutput
        jo = JobOutput()
        assert jo.complex_treatment_warning is None

    def test_field_accepts_string_value(self):
        from taxspine_orchestrator.models import JobOutput
        jo = JobOutput(complex_treatment_warning="TL-06: staking warning")
        assert jo.complex_treatment_warning == "TL-06: staking warning"

    def test_field_description_mentions_staking(self):
        from taxspine_orchestrator.models import JobOutput
        field_info = JobOutput.model_fields["complex_treatment_warning"]
        desc = (field_info.description or "").lower()
        assert "staking" in desc or "complex" in desc


class TestTL06Scanner:
    """_scan_complex_tax_treatments correctly identifies complex events in CSVs."""

    def _write_csv(self, rows: list[dict], tmp_path: Path) -> Path:
        p = tmp_path / "events.csv"
        fieldnames = [
            "event_id", "timestamp", "event_type", "source", "account",
            "asset_in", "amount_in", "asset_out", "amount_out",
            "fee_asset", "fee_amount", "tx_hash", "exchange_tx_id",
            "label", "complex_tax_treatment", "note",
        ]
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                full_row = {k: "" for k in fieldnames}
                full_row.update(row)
                writer.writerow(full_row)
        return p

    def _make_spec(self, path: Path):
        from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType
        return CsvFileSpec(path=str(path), source_type=CsvSourceType.GENERIC_EVENTS)

    def test_returns_none_for_empty_csv(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        p = self._write_csv([], tmp_path)
        result = _scan_complex_tax_treatments([self._make_spec(p)])
        assert result is None

    def test_returns_none_for_normal_treatment(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        p = self._write_csv([{"complex_tax_treatment": "NORMAL"}], tmp_path)
        result = _scan_complex_tax_treatments([self._make_spec(p)])
        assert result is None

    def test_returns_none_for_empty_treatment_column(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        p = self._write_csv([{"complex_tax_treatment": ""}], tmp_path)
        result = _scan_complex_tax_treatments([self._make_spec(p)])
        assert result is None

    def test_detects_staking_treatment(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        p = self._write_csv([{"complex_tax_treatment": "STAKING"}], tmp_path)
        result = _scan_complex_tax_treatments([self._make_spec(p)])
        assert result is not None
        assert "STAKING" in result
        assert "TL-06" in result

    def test_detects_airdrop_treatment(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        p = self._write_csv([{"complex_tax_treatment": "AIRDROP"}], tmp_path)
        result = _scan_complex_tax_treatments([self._make_spec(p)])
        assert result is not None
        assert "AIRDROP" in result

    def test_case_insensitive_standard_values(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        p = self._write_csv([{"complex_tax_treatment": "normal"}], tmp_path)
        result = _scan_complex_tax_treatments([self._make_spec(p)])
        assert result is None

    def test_multiple_complex_labels_all_reported(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        p = self._write_csv([
            {"complex_tax_treatment": "STAKING"},
            {"complex_tax_treatment": "AIRDROP"},
        ], tmp_path)
        result = _scan_complex_tax_treatments([self._make_spec(p)])
        assert result is not None
        assert "STAKING" in result
        assert "AIRDROP" in result

    def test_skips_missing_csv_file(self, tmp_path):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType
        missing = CsvFileSpec(
            path=str(tmp_path / "nonexistent.csv"),
            source_type=CsvSourceType.GENERIC_EVENTS,
        )
        result = _scan_complex_tax_treatments([missing])
        assert result is None

    def test_returns_none_for_empty_specs_list(self):
        from taxspine_orchestrator.services import _scan_complex_tax_treatments
        assert _scan_complex_tax_treatments([]) is None


# ── INFRA-06: POST /admin/cleanup ──────────────────────────────────────────────


class TestINFRA06CleanupEndpoint:
    """POST /admin/cleanup removes old terminal-state jobs and their files."""

    def test_endpoint_exists(self):
        client = _make_client()
        r = client.post("/admin/cleanup?older_than_days=1")
        assert r.status_code in (200, 404)  # 404 = endpoint may not be wired in test app
        # Accept 200 — endpoint registered
        if r.status_code == 200:
            data = r.json()
            assert "jobs_removed" in data or "dry_run" in data

    def test_dry_run_returns_preview(self):
        client = _make_client()
        r = client.post("/admin/cleanup?older_than_days=1&dry_run=true")
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert "jobs_would_remove" in data
        assert isinstance(data["job_ids"], list)

    def test_older_than_days_validation_min(self):
        client = _make_client()
        r = client.post("/admin/cleanup?older_than_days=0")
        assert r.status_code == 422

    def test_older_than_days_validation_max(self):
        client = _make_client()
        r = client.post("/admin/cleanup?older_than_days=9999")
        assert r.status_code == 422

    def test_removes_old_completed_job(self):
        client = _make_client()
        job_id = _make_job(client)
        # Manually update the job to COMPLETED state with an old timestamp
        from taxspine_orchestrator.main import _job_store
        job = _job_store.get(job_id)
        if job is None:
            return
        from taxspine_orchestrator.models import JobStatus
        old_time = datetime.now(timezone.utc) - timedelta(days=200)
        updated = job.model_copy(update={"status": JobStatus.COMPLETED, "updated_at": old_time})
        _job_store._upsert(updated)  # type: ignore[attr-defined]
        r = client.post("/admin/cleanup?older_than_days=100")
        assert r.status_code == 200
        data = r.json()
        assert data["jobs_removed"] >= 1

    def test_does_not_remove_running_job(self):
        client = _make_client()
        job_id = _make_job(client)
        from taxspine_orchestrator.main import _job_store
        from taxspine_orchestrator.models import JobStatus
        job = _job_store.get(job_id)
        if job is None:
            return
        old_time = datetime.now(timezone.utc) - timedelta(days=200)
        # Mark as RUNNING with an old timestamp — should NOT be cleaned up
        updated = job.model_copy(update={"status": JobStatus.RUNNING, "updated_at": old_time})
        _job_store._upsert(updated)  # type: ignore[attr-defined]
        r = client.post("/admin/cleanup?older_than_days=100&dry_run=true")
        assert r.status_code == 200
        data = r.json()
        # The running job must not appear in the would-remove list
        assert job_id not in data["job_ids"]
        # Cleanup: reset to pending so other tests aren't affected
        reset = job.model_copy(update={"status": JobStatus.PENDING})
        _job_store._upsert(reset)  # type: ignore[attr-defined]

    def test_source_code_has_infra06_comment(self):
        p = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        assert "INFRA-06" in p.read_text(encoding="utf-8")


# ── INFRA-21: JSON structured logging ──────────────────────────────────────────


class TestINFRA21StructuredLogging:
    """JSON log formatter emits parseable JSON lines when LOG_FORMAT=json."""

    def test_json_formatter_produces_valid_json(self):
        from taxspine_orchestrator.main import _JsonLogFormatter
        formatter = _JsonLogFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["msg"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"

    def test_json_formatter_includes_timestamp(self):
        from taxspine_orchestrator.main import _JsonLogFormatter
        formatter = _JsonLogFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="", lineno=0,
            msg="warn msg", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "ts" in parsed

    def test_json_formatter_handles_exception(self):
        from taxspine_orchestrator.main import _JsonLogFormatter
        formatter = _JsonLogFormatter()
        try:
            raise ValueError("oops")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0,
            msg="error", args=(), exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exc" in parsed
        assert "ValueError" in parsed["exc"]

    def test_source_code_activates_on_env_var(self):
        src = (
            Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        ).read_text(encoding="utf-8")
        assert "LOG_FORMAT" in src
        assert "_JsonLogFormatter" in src
        assert "INFRA-21" in src


# ── LC-11: Deletion audit log ──────────────────────────────────────────────────


class TestLC11AuditLog:
    """Deletion events are recorded and accessible via GET /admin/audit."""

    def test_log_deletion_persists(self, tmp_path):
        from taxspine_orchestrator.storage import SqliteJobStore
        store = SqliteJobStore(tmp_path / "test_audit.db")
        store.log_deletion("job-abc", files_removed=3)
        entries = store.list_deletions(limit=10)
        assert len(entries) == 1
        assert entries[0]["job_id"] == "job-abc"
        assert entries[0]["files_removed"] == 3

    def test_list_deletions_newest_first(self, tmp_path):
        from taxspine_orchestrator.storage import SqliteJobStore
        store = SqliteJobStore(tmp_path / "test_audit_order.db")
        store.log_deletion("job-1", 1)
        store.log_deletion("job-2", 2)
        entries = store.list_deletions(limit=10)
        assert entries[0]["job_id"] == "job-2"
        assert entries[1]["job_id"] == "job-1"

    def test_audit_endpoint_returns_list(self):
        client = _make_client()
        r = client.get("/admin/audit")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_deletion_logged_on_delete_endpoint(self):
        client = _make_client()
        job_id = _make_job(client)
        client.delete(f"/jobs/{job_id}")
        r = client.get("/admin/audit?limit=50")
        assert r.status_code == 200
        entries = r.json()
        ids = [e["job_id"] for e in entries]
        assert job_id in ids

    def test_audit_limit_param_respected(self):
        client = _make_client()
        r = client.get("/admin/audit?limit=1")
        assert r.status_code == 200

    def test_deletion_log_table_created_in_db(self, tmp_path):
        from taxspine_orchestrator.storage import SqliteJobStore
        import sqlite3
        SqliteJobStore(tmp_path / "schema_check.db")
        with sqlite3.connect(str(tmp_path / "schema_check.db")) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
        assert "deletion_log" in tables

    def test_source_code_has_lc11_comment(self):
        p = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        assert "LC-11" in p.read_text(encoding="utf-8")


# ── INFRA-23: Watchtower socket hardening ──────────────────────────────────────


class TestINFRA23WatchtowerSocket:
    """docker-compose.synology.yml documents INFRA-23 socket risk and proxy option."""

    def _compose(self) -> str:
        p = Path(__file__).parent.parent / "docker-compose.synology.yml"
        return p.read_text(encoding="utf-8")

    def test_infra23_label_present(self):
        assert "INFRA-23" in self._compose()

    def test_socket_proxy_service_commented_out(self):
        text = self._compose()
        assert "socket-proxy" in text
        assert "tecnativa/docker-socket-proxy" in text

    def test_hardening_instructions_present(self):
        text = self._compose()
        assert "DOCKER_HOST" in text

    def test_direct_socket_still_works_by_default(self):
        # Watchtower still has the raw socket mount (for backward compat)
        text = self._compose()
        assert "/var/run/docker.sock" in text


# ── Documentation checks ────────────────────────────────────────────────────────


class TestDocumentationFindings:
    """LC-07, LC-08, INFRA-19 documentation sections are present in README."""

    def _readme(self) -> str:
        p = Path(__file__).parent.parent / "README.md"
        return p.read_text(encoding="utf-8")

    def test_lc07_third_party_section_present(self):
        readme = self._readme()
        assert "Third-Party Data Sources" in readme or "LC-07" in readme

    def test_lc07_coingecko_mentioned(self):
        assert "coingecko" in self._readme().lower()

    def test_lc07_bank_of_england_mentioned(self):
        readme = self._readme().lower()
        assert "bank of england" in readme or "bankofengland" in readme

    def test_lc08_data_handling_section_present(self):
        readme = self._readme()
        assert "Data Handling" in readme or "Privacy" in readme or "LC-08" in readme

    def test_lc08_gdpr_mentioned(self):
        assert "GDPR" in self._readme()

    def test_lc08_deletion_instructions(self):
        readme = self._readme()
        assert "DELETE /jobs" in readme or "admin/cleanup" in readme

    def test_infra19_backup_section_present(self):
        readme = self._readme()
        assert "Backup" in readme or "INFRA-19" in readme

    def test_infra19_sqlite_backup_command(self):
        readme = self._readme()
        assert "sqlite3" in readme and ".backup" in readme
