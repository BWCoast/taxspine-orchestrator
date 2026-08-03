"""Workspace isolation regression tests.

Proves that:
- Jobs created with workspace_id='friend' are NOT returned when listing
  default-workspace jobs, and vice versa.
- FIFO lot store paths differ between workspaces.
- Dedup paths differ between workspaces.
- delete_by_workspace() removes only the target workspace's jobs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    Job,
    JobInput,
    JobOutput,
    JobStatus,
)
from taxspine_orchestrator.services import JobService
from taxspine_orchestrator.storage import InMemoryJobStore, SqliteJobStore, WorkspacePaths


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_job(workspace_id: str | None = None) -> Job:
    """Minimal Job for testing."""
    now = datetime.now(timezone.utc)
    return Job(
        id=str(uuid.uuid4()),
        status=JobStatus.PENDING,
        input=JobInput(
            xrpl_accounts=[],
            csv_files=[CsvFileSpec(path="/tmp/f.csv", source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
            workspace_id=workspace_id,
        ),
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mem_store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SqliteJobStore:
    return SqliteJobStore(tmp_path / "jobs.db")


# ── WorkspacePaths unit tests ─────────────────────────────────────────────────


class TestWorkspacePaths:
    def test_default_workspace_uses_global_paths(self, tmp_path):
        lots = tmp_path / "lots.db"
        dedup = tmp_path / "dedup"
        paths = WorkspacePaths.resolve(
            None,
            data_dir=tmp_path,
            lots_db=lots,
            dedup_dir=dedup,
        )
        assert paths.lots_db == lots
        assert paths.dedup_dir == dedup
        assert paths.workspace_json == tmp_path / "workspace.json"

    def test_named_workspace_uses_subdirectory(self, tmp_path):
        paths = WorkspacePaths.resolve(
            "friend",
            data_dir=tmp_path,
            lots_db=tmp_path / "lots.db",
            dedup_dir=tmp_path / "dedup",
        )
        expected_ws = tmp_path / "workspaces" / "friend"
        assert paths.lots_db == expected_ws / "lots.db"
        assert paths.dedup_dir == expected_ws / "dedup"
        assert paths.workspace_json == expected_ws / "workspace.json"

    def test_two_different_named_workspaces_differ(self, tmp_path):
        p1 = WorkspacePaths.resolve("alice", data_dir=tmp_path, lots_db=None, dedup_dir=tmp_path / "dedup")
        p2 = WorkspacePaths.resolve("bob",   data_dir=tmp_path, lots_db=None, dedup_dir=tmp_path / "dedup")
        assert p1.lots_db != p2.lots_db
        assert p1.dedup_dir != p2.dedup_dir


# ── InMemoryJobStore isolation ────────────────────────────────────────────────


class TestInMemoryIsolation:
    def test_list_default_excludes_named_workspace(self, mem_store):
        mem_store.add(_make_job(workspace_id=None))
        mem_store.add(_make_job(workspace_id="friend"))

        default_jobs = mem_store.list(filter_workspace=True, workspace_id=None)
        assert len(default_jobs) == 1
        assert default_jobs[0].input.workspace_id is None

    def test_list_named_excludes_default(self, mem_store):
        mem_store.add(_make_job(workspace_id=None))
        mem_store.add(_make_job(workspace_id="friend"))

        friend_jobs = mem_store.list(filter_workspace=True, workspace_id="friend")
        assert len(friend_jobs) == 1
        assert friend_jobs[0].input.workspace_id == "friend"

    def test_list_no_filter_returns_all(self, mem_store):
        mem_store.add(_make_job(workspace_id=None))
        mem_store.add(_make_job(workspace_id="friend"))

        all_jobs = mem_store.list(filter_workspace=False)
        assert len(all_jobs) == 2

    def test_count_respects_workspace_filter(self, mem_store):
        for _ in range(3):
            mem_store.add(_make_job(workspace_id=None))
        for _ in range(2):
            mem_store.add(_make_job(workspace_id="friend"))

        assert mem_store.count(filter_workspace=True, workspace_id=None) == 3
        assert mem_store.count(filter_workspace=True, workspace_id="friend") == 2
        assert mem_store.count(filter_workspace=False) == 5

    def test_delete_by_workspace_removes_only_target(self, mem_store):
        mem_store.add(_make_job(workspace_id=None))
        mem_store.add(_make_job(workspace_id=None))
        mem_store.add(_make_job(workspace_id="friend"))

        deleted = mem_store.delete_by_workspace("friend")
        assert deleted == 1
        assert mem_store.count() == 2
        remaining = mem_store.list()
        assert all(j.input.workspace_id is None for j in remaining)

    def test_delete_by_workspace_none_removes_default(self, mem_store):
        mem_store.add(_make_job(workspace_id=None))
        mem_store.add(_make_job(workspace_id="friend"))

        deleted = mem_store.delete_by_workspace(None)
        assert deleted == 1
        assert mem_store.count() == 1


# ── SqliteJobStore isolation ──────────────────────────────────────────────────


class TestSqliteIsolation:
    def test_workspace_id_persisted_and_filterable(self, sqlite_store):
        sqlite_store.add(_make_job(workspace_id="friend"))
        sqlite_store.add(_make_job(workspace_id=None))

        friend_jobs = sqlite_store.list(filter_workspace=True, workspace_id="friend")
        default_jobs = sqlite_store.list(filter_workspace=True, workspace_id=None)
        all_jobs = sqlite_store.list(filter_workspace=False)

        assert len(friend_jobs) == 1
        assert friend_jobs[0].input.workspace_id == "friend"
        assert len(default_jobs) == 1
        assert default_jobs[0].input.workspace_id is None
        assert len(all_jobs) == 2

    def test_count_by_workspace(self, sqlite_store):
        for _ in range(4):
            sqlite_store.add(_make_job(workspace_id=None))
        for _ in range(3):
            sqlite_store.add(_make_job(workspace_id="friend"))

        assert sqlite_store.count(filter_workspace=True, workspace_id=None) == 4
        assert sqlite_store.count(filter_workspace=True, workspace_id="friend") == 3
        assert sqlite_store.count(filter_workspace=False) == 7

    def test_delete_by_workspace(self, sqlite_store):
        sqlite_store.add(_make_job(workspace_id=None))
        sqlite_store.add(_make_job(workspace_id="friend"))
        sqlite_store.add(_make_job(workspace_id="friend"))

        deleted = sqlite_store.delete_by_workspace("friend")
        assert deleted == 2
        assert sqlite_store.count(filter_workspace=False) == 1

    def test_migration_safe_on_existing_db(self, tmp_path):
        """Opening the store twice is safe (migration is idempotent)."""
        db = tmp_path / "jobs.db"
        s1 = SqliteJobStore(db)
        s1.add(_make_job(workspace_id="test"))
        s2 = SqliteJobStore(db)
        jobs = s2.list(filter_workspace=True, workspace_id="test")
        assert len(jobs) == 1


# ── JobService resolve_lot_store ──────────────────────────────────────────────


class TestResolveLotStore:
    def test_default_workspace_uses_settings_lot_store_db(self, monkeypatch, tmp_path):
        from taxspine_orchestrator import services
        monkeypatch.setattr(services.settings, "LOT_STORE_DB", tmp_path / "lots.db")
        monkeypatch.setattr(services.settings, "DATA_DIR", tmp_path)

        result = JobService._resolve_lot_store(None)
        assert result == tmp_path / "lots.db"

    def test_named_workspace_uses_subdirectory(self, monkeypatch, tmp_path):
        from taxspine_orchestrator import services
        monkeypatch.setattr(services.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(services.settings, "LOT_STORE_DB", tmp_path / "lots.db")

        result = JobService._resolve_lot_store("friend")
        assert result == tmp_path / "workspaces" / "friend" / "lots.db"
        # The directory should have been created lazily.
        assert (tmp_path / "workspaces" / "friend").is_dir()

    def test_two_workspaces_have_different_lot_stores(self, monkeypatch, tmp_path):
        from taxspine_orchestrator import services
        monkeypatch.setattr(services.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(services.settings, "LOT_STORE_DB", tmp_path / "lots.db")

        alice = JobService._resolve_lot_store("alice")
        bob   = JobService._resolve_lot_store("bob")
        assert alice != bob
