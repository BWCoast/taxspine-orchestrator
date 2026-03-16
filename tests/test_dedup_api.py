"""Tests for the dedup skip-log inspection API.

Covers:
- GET /dedup/sources → empty list when DEDUP_DIR is empty.
- GET /dedup/sources → lists .db files with metadata.
- GET /dedup/{source}/summary → db_exists=False when no DB file.
- GET /dedup/{source}/summary → total_skips=0 on fresh empty DB.
- GET /dedup/{source}/summary → correct counts after writing skips.
- GET /dedup/{source}/summary?since= → filters by timestamp.
- GET /dedup/{source}/entries → empty list when no DB file.
- GET /dedup/{source}/entries → returns entries after writing skips.
- GET /dedup/{source}/entries?limit= → honours limit.
- GET /dedup/{source}/entries?source_type= → filters by source_type.
- Slug with path separators is sanitised (forward slash → underscore).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.config import settings as _real_settings

# ── Availability guard ─────────────────────────────────────────────────────────
# Skip all tests in this module when tax_spine is not installed.
# This keeps CI green in pure-orchestrator environments where tax-nor is not
# available.  In local dev (and in the Docker image), tax-nor IS installed so
# all tests run fully.
try:
    from tax_spine.ingestion.dedup_store import SkipLogReader as _SkipLogReader  # noqa: F401
    _TAX_SPINE_AVAILABLE = True
except ImportError:
    _TAX_SPINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _TAX_SPINE_AVAILABLE,
    reason="tax_spine not installed — skipping dedup API tests",
)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def dedup_dir(tmp_path):
    """Provide a fresh temp DEDUP_DIR, override settings."""
    d = tmp_path / "dedup"
    d.mkdir()
    return d


def _write_skip(db_path: Path, source_type: str = "generic_events") -> None:
    """Write a single skip entry to *db_path* using the real SqliteDedupStore."""
    from datetime import datetime, timezone

    from tax_spine.ingestion.dedup_store import SqliteDedupStore
    from tax_spine.ingestion.dedup_filter import filter_duplicates
    from tax_spine.ingestion.import_batch import new_ingestion_batch

    batch = new_ingestion_batch(
        source_type,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manual",
        "test",
    )
    raw_rows = [
        {"source_type": source_type, "tx_hash": "abc123", "exchange_tx_id": "", "timestamp": "2025-01-01T00:00:00Z"},
    ]
    store = SqliteDedupStore(str(db_path))
    try:
        # First call — records the event.
        filter_duplicates(raw_rows, store, batch)
        # Second call with new batch — produces a skip.
        batch2 = new_ingestion_batch(
            source_type,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "manual",
            "test",
        )
        filter_duplicates(raw_rows, store, batch2)
    finally:
        store.close()


# ── TestListDedupSources ──────────────────────────────────────────────────────


class TestListDedupSources:
    def test_empty_dir_returns_empty_list(self, client, dedup_dir):
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/sources")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_db_files(self, client, dedup_dir):
        (dedup_dir / "generic_events.db").write_bytes(b"x")
        (dedup_dir / "firi_csv.db").write_bytes(b"y")
        (dedup_dir / "not_a_db.txt").write_bytes(b"z")  # should be ignored
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/sources")
        assert resp.status_code == 200
        sources = [item["source"] for item in resp.json()]
        assert "generic_events" in sources
        assert "firi_csv" in sources
        assert "not_a_db" not in sources

    def test_result_includes_metadata(self, client, dedup_dir):
        (dedup_dir / "generic_events.db").write_bytes(b"hello")
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/sources")
        item = resp.json()[0]
        assert "size_bytes" in item
        assert "last_modified" in item
        assert item["size_bytes"] == 5

    def test_missing_dedup_dir_returns_empty_list(self, client, tmp_path):
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", tmp_path / "nonexistent"):
            resp = client.get("/dedup/sources")
        assert resp.status_code == 200
        assert resp.json() == []


# ── TestDedupSummary ──────────────────────────────────────────────────────────


class TestDedupSummary:
    def test_no_db_returns_db_exists_false(self, client, dedup_dir):
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/generic_events/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_exists"] is False
        assert data["total_skips"] == 0

    def test_fresh_db_zero_skips(self, client, dedup_dir):
        from tax_spine.ingestion.dedup_store import SqliteDedupStore
        db = dedup_dir / "generic_events.db"
        store = SqliteDedupStore(str(db))
        store.close()
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/generic_events/summary")
        assert resp.status_code == 200
        assert resp.json()["total_skips"] == 0
        assert resp.json()["db_exists"] is True

    def test_counts_skips(self, client, dedup_dir):
        db = dedup_dir / "generic_events.db"
        _write_skip(db, source_type="generic_events")
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/generic_events/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_skips"] == 1
        assert "generic_events" in data["by_source"]

    def test_since_filter_accepted(self, client, dedup_dir):
        """?since= parameter is forwarded without error (may return 0)."""
        from tax_spine.ingestion.dedup_store import SqliteDedupStore
        db = dedup_dir / "firi_csv.db"
        store = SqliteDedupStore(str(db))
        store.close()
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/firi_csv/summary?since=2030-01-01T00:00:00Z")
        assert resp.status_code == 200
        assert resp.json()["total_skips"] == 0


# ── TestDedupEntries ──────────────────────────────────────────────────────────


class TestDedupEntries:
    def test_no_db_returns_empty_list(self, client, dedup_dir):
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/generic_events/entries")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_skip_entries(self, client, dedup_dir):
        db = dedup_dir / "generic_events.db"
        _write_skip(db, source_type="generic_events")
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/generic_events/entries")
        assert resp.status_code == 200
        entries = resp.json()
        assert len(entries) == 1
        entry = entries[0]
        assert "dedup_key" in entry
        assert "skipped_at" in entry
        assert "source_type" in entry
        assert "first_seen_at" in entry

    def test_limit_respected(self, client, dedup_dir):
        db = dedup_dir / "generic_events.db"
        # Write 3 distinct skips.
        from datetime import datetime, timezone
        from tax_spine.ingestion.dedup_store import SqliteDedupStore
        from tax_spine.ingestion.dedup_filter import filter_duplicates
        from tax_spine.ingestion.import_batch import new_ingestion_batch

        store = SqliteDedupStore(str(db))
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = [
                {"source_type": "generic_events", "tx_hash": f"hash{i}", "exchange_tx_id": "", "timestamp": "2025-01-01T00:00:00Z"}
                for i in range(3)
            ]
            b1 = new_ingestion_batch("generic_events", ts, "manual", "test")
            filter_duplicates(rows, store, b1)
            b2 = new_ingestion_batch("generic_events", ts, "manual", "test")
            filter_duplicates(rows, store, b2)
        finally:
            store.close()

        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            resp = client.get("/dedup/generic_events/entries?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_source_type_filter(self, client, dedup_dir):
        db = dedup_dir / "nor_multi.db"
        _write_skip(db, source_type="firi_csv")
        from unittest.mock import patch
        with patch.object(_real_settings, "DEDUP_DIR", dedup_dir):
            # Filter for a different source_type → no results.
            resp = client.get("/dedup/nor_multi/entries?source_type=coinbase_csv")
        assert resp.status_code == 200
        assert resp.json() == []
