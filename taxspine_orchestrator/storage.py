"""Job store implementations (in-memory and SQLite) + WorkspaceStore."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .models import Country, CsvFileSpec, Job, JobStatus, WorkspaceConfig


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

    def update_status(self, job_id: str, status: JobStatus, *, error_message: str | None = None) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        fields: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if status == JobStatus.RUNNING:
            fields["started_at"] = datetime.now(timezone.utc)
        if error_message is not None:
            from .models import JobOutput
            fields["output"] = job.output.model_copy(update={"error_message": error_message})
        updated = job.model_copy(update=fields)
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

    def transition_status(
        self,
        job_id: str,
        from_status: JobStatus,
        to_status: JobStatus,
    ) -> "Job | None":
        """Atomically transition a job from ``from_status`` to ``to_status`` (CAS).

        API-04 / API-07: eliminates the read-check-spawn race window.

        Returns the updated ``Job`` on success, or ``None`` when:
          - the job does not exist, OR
          - the current status is not ``from_status`` (CAS failed).
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status != from_status:
            return None  # CAS failed
        upd_fields: dict = {
            "status": to_status,
            "updated_at": datetime.now(timezone.utc),
        }
        if to_status == JobStatus.RUNNING:
            upd_fields["started_at"] = datetime.now(timezone.utc)
        updated = job.model_copy(update=upd_fields)
        self._jobs[job_id] = updated
        return updated

    def delete(self, job_id: str) -> bool:
        """Remove a single job.  Returns True if it existed, False otherwise."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False

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
        recovered = self._recover_interrupted_jobs()
        if recovered:
            print(f"[SqliteJobStore] crash-recovery: marked {recovered} RUNNING job(s) as FAILED")

    def _init_db(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            # INFRA-02: enable WAL mode so readers and writers don't block each
            # other.  NORMAL synchronous keeps durability guarantees without the
            # overhead of FULL (fsync on every commit).
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(self._DDL)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        # INFRA-02: WAL mode must be set on every new connection — it is a
        # per-connection pragma even though the mode is stored in the database
        # file.  NORMAL synchronous balances performance and durability.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _recover_interrupted_jobs(self) -> int:
        """Mark any RUNNING jobs as FAILED — they were interrupted by a previous crash.

        Called automatically during __init__ so that jobs stuck in RUNNING
        (due to a server crash or restart) are immediately visible as FAILED.
        The stored JSON blob is updated in-place via get+upsert so that the
        error_message is recorded in the Job output.
        """
        # Collect all job IDs that are currently RUNNING.
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status = ?",
                (JobStatus.RUNNING.value,),
            ).fetchall()

        count = 0
        for (job_id,) in rows:
            job = self.get(job_id)
            if job is None:
                continue
            from .models import JobOutput
            updated_output = job.output.model_copy(
                update={"error_message": "Interrupted by server restart"}
            )
            updated = job.model_copy(
                update={
                    "status": JobStatus.FAILED,
                    "output": updated_output,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._upsert(updated)
            count += 1
        return count

    def ping(self) -> None:
        """Verify the database is reachable — raises on error."""
        with self._lock, self._connect() as conn:
            conn.execute("SELECT 1 FROM jobs LIMIT 1")

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

    def update_status(self, job_id: str, status: JobStatus, *, error_message: str | None = None) -> Job | None:
        # API-16: hold the lock for the full read-modify-write to eliminate the
        # race window that existed when get() and _upsert() each acquired the
        # lock separately.
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = Job.model_validate_json(row[0])
            upd_fields: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
            if status == JobStatus.RUNNING:
                upd_fields["started_at"] = datetime.now(timezone.utc)
            if error_message is not None:
                upd_fields["output"] = job.output.model_copy(
                    update={"error_message": error_message}
                )
            updated = job.model_copy(update=upd_fields)
            conn.execute(
                "UPDATE jobs SET status = ?, data = ? WHERE id = ?",
                (updated.status.value, updated.model_dump_json(), updated.id),
            )
        return updated

    def update_job(self, job_id: str, **fields: Any) -> Job | None:
        # API-16: same single-lock pattern as update_status.
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = Job.model_validate_json(row[0])
            fields.setdefault("updated_at", datetime.now(timezone.utc))
            updated = job.model_copy(update=fields)
            conn.execute(
                "UPDATE jobs SET status = ?, data = ? WHERE id = ?",
                (updated.status.value, updated.model_dump_json(), updated.id),
            )
        return updated

    def transition_status(
        self,
        job_id: str,
        from_status: JobStatus,
        to_status: JobStatus,
    ) -> "Job | None":
        """Atomically transition a job from ``from_status`` to ``to_status`` (CAS).

        API-04 / API-07: performs the check-and-set as a single locked DB
        transaction so that two concurrent callers cannot both succeed.

        Returns the updated ``Job`` on success, or ``None`` when:
          - the job does not exist, OR
          - the current status is not ``from_status`` (CAS failed — another
            caller already transitioned it).

        Callers should treat ``None`` as a conflict (HTTP 409).
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = Job.model_validate_json(row[0])
            if job.status != from_status:
                return None  # CAS failed — status already changed by another caller
            upd_fields: dict = {
                "status": to_status,
                "updated_at": datetime.now(timezone.utc),
            }
            if to_status == JobStatus.RUNNING:
                upd_fields["started_at"] = datetime.now(timezone.utc)
            updated = job.model_copy(update=upd_fields)
            conn.execute(
                "UPDATE jobs SET status = ?, data = ? WHERE id = ?",
                (updated.status.value, updated.model_dump_json(), updated.id),
            )
        return updated

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
            # SEC-01: escape LIKE metacharacters before wrapping in wildcards so
            # that '%' and '_' in the user-supplied query are treated as literals
            # rather than SQL wildcards. The ESCAPE '\' clause tells SQLite which
            # escape character we are using.
            escaped_query = (
                query
                .replace("\\", "\\\\")   # escape the escape char first
                .replace("%", "\\%")     # escape wildcard %
                .replace("_", "\\_")     # escape wildcard _
            )
            conditions.append("case_name LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped_query}%")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        sql = (
            f"SELECT data FROM jobs {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [Job.model_validate_json(r[0]) for r in rows]

    def delete(self, job_id: str) -> bool:
        """Remove a single job.  Returns True if it existed, False otherwise."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

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
        # API-02: write atomically via a sibling temp file then replace.
        # This prevents a partial-write from corrupting the workspace file if
        # the process crashes mid-write (e.g. SIGKILL, power loss).
        content = cfg.model_dump_json(indent=2)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(self._path)

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

    def add_csv(self, spec: CsvFileSpec) -> WorkspaceConfig:
        """Register a CSV file spec (no-op if a file with the same path is already present)."""
        with self._lock:
            cfg = self._load_locked()
            existing_paths = {f.path for f in cfg.csv_files}
            if spec.path not in existing_paths:
                cfg = cfg.model_copy(
                    update={"csv_files": [*cfg.csv_files, spec]}
                )
                self._save_locked(cfg)
            return cfg

    def clear(self) -> WorkspaceConfig:
        """Reset the workspace to an empty state (removes all accounts and CSV files)."""
        with self._lock:
            empty = WorkspaceConfig()
            self._save_locked(empty)
            return empty

    def remove_csv(self, path: str) -> WorkspaceConfig:
        """Remove a CSV file by path."""
        with self._lock:
            cfg = self._load_locked()
            cfg = cfg.model_copy(
                update={"csv_files": [f for f in cfg.csv_files if f.path != path]}
            )
            self._save_locked(cfg)
            return cfg
