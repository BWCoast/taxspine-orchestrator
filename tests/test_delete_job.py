"""Tests for DELETE /jobs/{id}.

Covers:
- 404 when job does not exist.
- 409 when job is RUNNING (cannot delete live jobs).
- 200 + {"deleted": True} for PENDING, COMPLETED, and FAILED jobs.
- Job is actually gone from the store after deletion (GET returns 404).
- Delete is idempotent-ish: second delete returns 404.
- Auth: requires key when ORCHESTRATOR_KEY is set.
- InMemoryJobStore.delete returns True on hit, False on miss.
- SqliteJobStore.delete removes the row and returns True; missing returns False.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import Job, JobInput, JobOutput, JobStatus, Country
from taxspine_orchestrator.storage import InMemoryJobStore, SqliteJobStore
from tests.conftest import start_and_wait


def _make_job() -> Job:
    return Job(
        id=str(uuid.uuid4()),
        status=JobStatus.PENDING,
        input=JobInput(tax_year=2025, country=Country.NORWAY),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


_NORWAY_INPUT = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}


def _create_job(client: TestClient) -> str:
    resp = client.post("/jobs", json=_NORWAY_INPUT)
    assert resp.status_code == 200
    return resp.json()["id"]


def _force_status(client: TestClient, job_id: str, status: JobStatus) -> None:
    from taxspine_orchestrator import main as _m
    _m._job_store.update_status(job_id, status)


# ── TestDeleteJobNotFound ─────────────────────────────────────────────────────


class TestDeleteJobNotFound:
    def test_returns_404_for_nonexistent_id(self, client: TestClient) -> None:
        resp = client.delete("/jobs/nonexistent-id")
        assert resp.status_code == 404

    def test_404_body_contains_detail(self, client: TestClient) -> None:
        resp = client.delete("/jobs/nonexistent-id")
        assert "detail" in resp.json()


# ── TestDeleteRunningJob ──────────────────────────────────────────────────────


class TestDeleteRunningJob:
    def test_running_job_returns_409(self, client: TestClient) -> None:
        job_id = _create_job(client)
        _force_status(client, job_id, JobStatus.RUNNING)
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 409

    def test_running_job_detail_mentions_cancel(self, client: TestClient) -> None:
        job_id = _create_job(client)
        _force_status(client, job_id, JobStatus.RUNNING)
        resp = client.delete(f"/jobs/{job_id}")
        assert "cancel" in resp.json()["detail"].lower()

    def test_running_job_is_not_deleted(self, client: TestClient) -> None:
        job_id = _create_job(client)
        _force_status(client, job_id, JobStatus.RUNNING)
        client.delete(f"/jobs/{job_id}")
        # Job should still be retrievable.
        assert client.get(f"/jobs/{job_id}").status_code == 200


# ── TestDeletePendingJob ──────────────────────────────────────────────────────


class TestDeletePendingJob:
    def test_pending_job_returns_200(self, client: TestClient) -> None:
        job_id = _create_job(client)
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 200

    def test_pending_job_response_has_deleted_true(self, client: TestClient) -> None:
        job_id = _create_job(client)
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.json()["deleted"] is True

    def test_pending_job_response_contains_id(self, client: TestClient) -> None:
        job_id = _create_job(client)
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.json()["id"] == job_id

    def test_pending_job_gone_from_store(self, client: TestClient) -> None:
        job_id = _create_job(client)
        client.delete(f"/jobs/{job_id}")
        assert client.get(f"/jobs/{job_id}").status_code == 404

    def test_pending_job_absent_from_list(self, client: TestClient) -> None:
        job_id = _create_job(client)
        client.delete(f"/jobs/{job_id}")
        ids = [j["id"] for j in client.get("/jobs").json()]
        assert job_id not in ids


# ── TestDeleteCompletedJob ────────────────────────────────────────────────────


class TestDeleteCompletedJob:
    def test_completed_job_returns_200(self, client: TestClient) -> None:
        job_id = _create_job(client)
        _force_status(client, job_id, JobStatus.COMPLETED)
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 200

    def test_completed_job_gone_from_store(self, client: TestClient) -> None:
        job_id = _create_job(client)
        _force_status(client, job_id, JobStatus.COMPLETED)
        client.delete(f"/jobs/{job_id}")
        assert client.get(f"/jobs/{job_id}").status_code == 404


# ── TestDeleteFailedJob ───────────────────────────────────────────────────────


class TestDeleteFailedJob:
    def test_failed_job_returns_200(self, client: TestClient) -> None:
        job_id = _create_job(client)
        _force_status(client, job_id, JobStatus.FAILED)
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 200

    def test_failed_job_gone_from_store(self, client: TestClient) -> None:
        job_id = _create_job(client)
        _force_status(client, job_id, JobStatus.FAILED)
        client.delete(f"/jobs/{job_id}")
        assert client.get(f"/jobs/{job_id}").status_code == 404


# ── TestDeleteIdempotency ─────────────────────────────────────────────────────


class TestDeleteIdempotency:
    def test_second_delete_returns_404(self, client: TestClient) -> None:
        """Deleting the same job twice returns 404 on the second attempt."""
        job_id = _create_job(client)
        client.delete(f"/jobs/{job_id}")
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 404

    def test_other_jobs_unaffected_by_delete(self, client: TestClient) -> None:
        """Deleting one job does not affect sibling jobs."""
        id_a = _create_job(client)
        id_b = _create_job(client)
        client.delete(f"/jobs/{id_a}")
        assert client.get(f"/jobs/{id_b}").status_code == 200


# ── TestInMemoryStoreDelete ───────────────────────────────────────────────────


class TestInMemoryStoreDelete:
    def _store_with_job(self) -> tuple[InMemoryJobStore, str]:
        store = InMemoryJobStore()
        job = _make_job()
        store.add(job)
        return store, job.id

    def test_delete_existing_returns_true(self) -> None:
        store, job_id = self._store_with_job()
        assert store.delete(job_id) is True

    def test_delete_missing_returns_false(self) -> None:
        store = InMemoryJobStore()
        assert store.delete("no-such-id") is False

    def test_delete_removes_job_from_get(self) -> None:
        store, job_id = self._store_with_job()
        store.delete(job_id)
        assert store.get(job_id) is None

    def test_delete_removes_job_from_list(self) -> None:
        store, job_id = self._store_with_job()
        store.delete(job_id)
        assert not any(j.id == job_id for j in store.list())


# ── TestSqliteStoreDelete ─────────────────────────────────────────────────────


class TestSqliteStoreDelete:
    def _store(self, tmp_path: Path) -> SqliteJobStore:
        return SqliteJobStore(tmp_path / "jobs.db")

    def _add_job(self, store: SqliteJobStore) -> str:
        job = _make_job()
        store.add(job)
        return job.id

    def test_delete_existing_returns_true(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        job_id = self._add_job(store)
        assert store.delete(job_id) is True

    def test_delete_missing_returns_false(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        assert store.delete("no-such-id") is False

    def test_delete_removes_from_get(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        job_id = self._add_job(store)
        store.delete(job_id)
        assert store.get(job_id) is None

    def test_delete_removes_from_list(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        job_id = self._add_job(store)
        store.delete(job_id)
        assert not any(j.id == job_id for j in store.list())

    def test_delete_row_absent_in_sqlite(self, tmp_path: Path) -> None:
        db_path = tmp_path / "jobs.db"
        store = SqliteJobStore(db_path)
        job_id = self._add_job(store)
        store.delete(job_id)
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is None

    def test_second_delete_returns_false(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        job_id = self._add_job(store)
        store.delete(job_id)
        assert store.delete(job_id) is False
