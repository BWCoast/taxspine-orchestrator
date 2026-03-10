"""Job store implementations (in-memory and SQLite) + WorkspaceStore."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .models import Country, Job, JobStatus, WorkspaceConfig


# ── In-memory store (development / testing) ──────────────────────────────────


class InMemoryJobStore:
    """Thread-unsafe, dict-backed store — good enough for ephemeral use.

    Kept so existing tests and unit-test fixtures continue to work without
    needing a real database.  The production server uses ``SqliteJobStore``.
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    def add(self, job: Job) -> Job:
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(
        self,
        *,
        status: JobStatus | None = None,
        country: Country | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Job]:
        jobs: list[Job] = list(self._jobs.values())
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        if country is not None:
            jobs = [j for j in jobs if j.input.country == country]
        if query is not None:
            q = query.lower()
            jobs = [
                j for j in jobs
                if j.input.case_name is not None and q in j.input.case_name.lower()
            ]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[offset: offset + limit]

    def update_status(self, job_id: str, status: JobStatus) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc)}
        )
        self._jobs[job_id] = updated
        return updated

    def update_job(self, job_id: str, **fields: Any) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        fields.setdefault("updated_at", datetime.now(timezone.utc))
        updated = job.model_copy(update=fields)
        self._jobs[job_id] = updated
        return updated

    def clear(self) -> None:
        """Delete all jobs — used by tests to reset state between runs."""
        self._jobs.clear()


# ── SQLite-backed store (production) ─────────────────────────────────────────


class SqliteJobStore:
    """SQLite-backed, thread-safe job store — survives server restarts.

    Each job is stored as its full Pydantic JSON payload so the schema
    is schema-free: adding new fields to ``Job`` requires no migration.
    Index columns (status, country, case_name, created_at) are maintained
    alongside the blob for efficient filtering.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT    PRIMARY KEY,
            status      TEXT    NOT NULL,
            country     TEXT,
            case_name   TEXT,
            created_at  TEXT    NOT NULL,
            data        TEXT    NOT NULL
        )
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(self._DDL)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), check_same_thread=False)

    # ── Write operations ──────────────────────────────────────────────────

    def add(self, job: Job) -> Job:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs "
                "(id, status, country, case_name, created_at, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    job.status.value,
                    job.input.country.value,
                    job.input.case_name,
                    job.created_at.isoformat(),
                    job.model_dump_json(),
                ),
            )
        return job

    def _upsert(self, job: Job) -> Job:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, data = ? WHERE id = ?",
                (job.status.value, job.model_dump_json(), job.id),
            )
        return job

    def update_status(self, job_id: str, status: JobStatus) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc)}
        )
        return self._upsert(updated)

    def update_job(self, job_id: str, **fields: Any) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        fields.setdefault("updated_at", datetime.now(timezone.utc))
        return self._upsert(job.model_copy(update=fields))

    # ── Read operations ───────────────────────────────────────────────────

    def get(self, job_id: str) -> Job | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return Job.model_validate_json(row[0]) if row else None

    def list(
        self,
        *,
        status: JobStatus | None = None,
        country: Country | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Job]:
        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if country is not None:
            conditions.append("country = ?")
            params.append(country.value)
        if query is not None:
            conditions.append("case_name LIKE ?")
            params.append(f"%{query}%")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        sql = (
            f"SELECT data FROM jobs {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [Job.model_validate_json(r[0]) for r in rows]

    def clear(self) -> None:
        """Delete all jobs — used by tests to reset state between runs."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM jobs")


# ── Workspace store ───────────────────────────────────────────────────────────


class WorkspaceStore:
    """Thread-safe, JSON-file-backed workspace configuration store.

    Persists the XRPL accounts and CSV file paths registered for
    continuous year-over-year tracking.  The file is created with an
    empty config if it does not yet exist.

    All public methods are safe to call from multiple threads (FastAPI
    worker threads share a single ``WorkspaceStore`` instance).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        # Initialise with empty config if file absent.
        if not self._path.exists():
            self._save_locked(WorkspaceConfig())

    # ── Internal helpers ──────────────────────────────────────────────────

    def _load_locked(self) -> WorkspaceConfig:
        return WorkspaceConfig.model_validate_json(
            self._path.read_text(encoding="utf-8")
        )

    def _save_locked(self, cfg: WorkspaceConfig) -> None:
        self._path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")

    # ── Public API ────────────────────────────────────────────────────────

    def load(self) -> WorkspaceConfig:
        """Return the current workspace config."""
        with self._lock:
            return self._load_locked()

    def add_account(self, account: str) -> WorkspaceConfig:
        """Register an XRPL account address (no-op if already present)."""
        with self._lock:
            cfg = self._load_locked()
            if account not in cfg.xrpl_accounts:
                cfg = cfg.model_copy(
                    update={"xrpl_accounts": [*cfg.xrpl_accounts, account]}
                )
                self._save_locked(cfg)
            return cfg

    def remove_account(self, account: str) -> WorkspaceConfig:
        """Remove an XRPL account address."""
        with self._lock:
            cfg = self._load_locked()
            cfg = cfg.model_copy(
                update={"xrpl_accounts": [a for a in cfg.xrpl_accounts if a != account]}
            )
            self._save_locked(cfg)
            return cfg

    def add_csv(self, path: str) -> WorkspaceConfig:
        """Register a CSV file path (no-op if already present)."""
        with self._lock:
            cfg = self._load_locked()
            if path not in cfg.csv_files:
                cfg = cfg.model_copy(
                    update={"csv_files": [*cfg.csv_files, path]}
                )
                self._save_locked(cfg)
            return cfg

    def remove_csv(self, path: str) -> WorkspaceConfig:
        """Remove a CSV file path."""
        with self._lock:
            cfg = self._load_locked()
            cfg = cfg.model_copy(
                update={"csv_files": [p for p in cfg.csv_files if p != path]}
            )
            self._save_locked(cfg)
            return cfg
