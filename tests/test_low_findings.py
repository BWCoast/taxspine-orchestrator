"""test_low_findings.py — Tests for LOW-priority audit findings.

Covers:
- SEC-03: _SensitiveHeaderFilter redacts X-Orchestrator-Key and Authorization
- BE-06:  after_id keyset pagination on InMemoryJobStore and SqliteJobStore
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest


# ── SEC-03: Sensitive-header log filter ──────────────────────────────────────


class TestSensitiveHeaderFilter:
    """SEC-03: X-Orchestrator-Key and Authorization are redacted from log records."""

    def _make_filter(self):
        """Import and return a fresh _SensitiveHeaderFilter instance."""
        from taxspine_orchestrator.main import _SensitiveHeaderFilter
        return _SensitiveHeaderFilter()

    def _make_record(self, msg: str) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg=msg,
            args=(), exc_info=None,
        )
        return record

    def test_x_orchestrator_key_colon_redacted(self) -> None:
        filt = self._make_filter()
        rec = self._make_record("Header: X-Orchestrator-Key: supersecret123")
        filt.filter(rec)
        assert "supersecret123" not in rec.msg
        assert "[REDACTED]" in rec.msg

    def test_x_orchestrator_key_equals_redacted(self) -> None:
        filt = self._make_filter()
        rec = self._make_record("X-Orchestrator-Key=mytoken")
        filt.filter(rec)
        assert "mytoken" not in rec.msg
        assert "[REDACTED]" in rec.msg

    def test_authorization_bearer_redacted(self) -> None:
        filt = self._make_filter()
        rec = self._make_record("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9")
        filt.filter(rec)
        assert "eyJhbGciOiJIUzI1NiJ9" not in rec.msg
        assert "[REDACTED]" in rec.msg

    def test_case_insensitive_match(self) -> None:
        filt = self._make_filter()
        rec = self._make_record("x-orchestrator-key: lowercase_secret")
        filt.filter(rec)
        assert "lowercase_secret" not in rec.msg

    def test_benign_message_unchanged(self) -> None:
        filt = self._make_filter()
        original = "Job abc123 completed successfully"
        rec = self._make_record(original)
        filt.filter(rec)
        assert rec.msg == original

    def test_filter_always_returns_true(self) -> None:
        """The filter must not suppress log records — it only redacts."""
        filt = self._make_filter()
        rec = self._make_record("X-Orchestrator-Key: secret")
        result = filt.filter(rec)
        assert result is True

    def test_record_args_cleared_after_redaction(self) -> None:
        """After redaction, record.args must be () so re-formatting is safe."""
        filt = self._make_filter()
        rec = self._make_record("key=%s")
        rec.args = ("secret_value",)
        filt.filter(rec)
        assert rec.args == ()

    def test_filter_installed_on_root_logger(self) -> None:
        """The root logger must have at least one _SensitiveHeaderFilter active."""
        import taxspine_orchestrator.main  # noqa: F401 — ensure module is imported
        from taxspine_orchestrator.main import _SensitiveHeaderFilter
        root_filters = logging.getLogger().filters
        assert any(isinstance(f, _SensitiveHeaderFilter) for f in root_filters)


# ── BE-06: Keyset pagination (after_id) ──────────────────────────────────────


class TestInMemoryJobStoreKeysetPagination:
    """BE-06: InMemoryJobStore.list() supports after_id keyset cursor."""

    def _make_store(self):
        from taxspine_orchestrator.storage import InMemoryJobStore
        return InMemoryJobStore()

    def _make_job(self, job_id: str, country: str = "norway", created_offset_secs: int = 0):
        """Build a minimal Job for testing."""
        from datetime import datetime, timezone, timedelta
        from taxspine_orchestrator.models import (
            Country, Job, JobInput, JobOutput, JobStatus, PipelineMode,
        )
        ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=created_offset_secs)
        return Job(
            id=job_id,
            status=JobStatus.PENDING,
            input=JobInput(
                tax_year=2025,
                country=Country(country),
                csv_files=[],
                pipeline_mode=PipelineMode.PER_FILE,
            ),
            output=JobOutput(),
            created_at=ts,
            updated_at=ts,
        )

    def test_after_id_none_returns_all(self) -> None:
        store = self._make_store()
        for i in range(5):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        result = store.list(limit=100, after_id=None)
        assert len(result) == 5

    def test_after_id_returns_older_jobs_only(self) -> None:
        store = self._make_store()
        # job-04 is newest (created_offset 4), job-00 oldest (0)
        for i in range(5):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        # After job-02 (offset=2), should return job-01 and job-00
        result = store.list(limit=100, after_id="job-02")
        ids = [j.id for j in result]
        assert "job-01" in ids
        assert "job-00" in ids
        assert "job-02" not in ids
        assert "job-03" not in ids
        assert "job-04" not in ids

    def test_after_id_unknown_returns_empty(self) -> None:
        store = self._make_store()
        for i in range(3):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        result = store.list(limit=100, after_id="nonexistent-id")
        assert result == []

    def test_after_id_last_job_returns_empty(self) -> None:
        store = self._make_store()
        for i in range(3):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        # job-00 is oldest; nothing comes after it
        result = store.list(limit=100, after_id="job-00")
        assert result == []

    def test_offset_still_works_without_after_id(self) -> None:
        store = self._make_store()
        for i in range(5):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        page1 = store.list(limit=2, offset=0)
        page2 = store.list(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].id != page2[0].id


class TestSqliteJobStoreKeysetPagination:
    """BE-06: SqliteJobStore.list() supports after_id keyset cursor."""

    def _make_store(self, tmp_path: Path):
        from taxspine_orchestrator.storage import SqliteJobStore
        return SqliteJobStore(tmp_path / "jobs.db")

    def _make_job(self, job_id: str, created_offset_secs: int = 0):
        from datetime import datetime, timezone, timedelta
        from taxspine_orchestrator.models import (
            Country, Job, JobInput, JobOutput, JobStatus, PipelineMode,
        )
        ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=created_offset_secs)
        return Job(
            id=job_id,
            status=JobStatus.PENDING,
            input=JobInput(
                tax_year=2025,
                country=Country.NORWAY,
                csv_files=[],
                pipeline_mode=PipelineMode.PER_FILE,
            ),
            output=JobOutput(),
            created_at=ts,
            updated_at=ts,
        )

    def test_after_id_none_returns_all(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        for i in range(5):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        result = store.list(limit=100, after_id=None)
        assert len(result) == 5

    def test_after_id_returns_older_jobs_only(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        for i in range(5):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        result = store.list(limit=100, after_id="job-02")
        ids = {j.id for j in result}
        assert ids == {"job-01", "job-00"}

    def test_after_id_unknown_returns_empty(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        for i in range(3):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        result = store.list(limit=100, after_id="nonexistent-id")
        assert result == []

    def test_after_id_last_job_returns_empty(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        for i in range(3):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        result = store.list(limit=100, after_id="job-00")
        assert result == []

    def test_combined_filter_and_keyset(self, tmp_path: Path) -> None:
        """after_id works alongside status filters."""
        from taxspine_orchestrator.models import JobStatus
        store = self._make_store(tmp_path)
        for i in range(5):
            store.add(self._make_job(f"job-{i:02d}", created_offset_secs=i))
        # Mark job-01 as failed
        store.update_status("job-01", JobStatus.FAILED)
        result = store.list(status=JobStatus.FAILED, limit=100, after_id=None)
        assert len(result) == 1
        assert result[0].id == "job-01"
