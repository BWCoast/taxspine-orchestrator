"""Job store implementations (in-memory and SQLite) + WorkspaceStore."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .models import Country, CsvFileSpec, Job, JobStatus, WorkspaceConfig

_log = logging.getLogger(__name__)


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
        after_id: str | None = None,
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
        # BE-06: keyset pagination — skip everything at or after the cursor job.
        # An unknown after_id returns an empty list (mirrors SQLite NULL behaviour).
        if after_id is not None:
            cursor_job = self._jobs.get(after_id)
            if cursor_job is None:
                return []
            cursor_ts = cursor_job.created_at
            jobs = [
                j for j in jobs
                if j.created_at < cursor_ts
                or (j.created_at == cursor_ts and j.id < after_id)
            ]
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

    def count(
        self,
        *,
        status: "JobStatus | None" = None,
        country: "Country | None" = None,
        query: "str | None" = None,
    ) -> int:
        """Return total matching job count without applying limit/offset.

        API-19: used to populate the X-Total-Count response header on
        GET /jobs so that clients can implement correct pagination.
        """
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
        return len(jobs)

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

    # LC-11: Separate DDL for the deletion audit log so it can be created
    # independently of the jobs table (both run in _init_db on startup).
    _DELETION_LOG_DDL = """
        CREATE TABLE IF NOT EXISTS deletion_log (
            id            TEXT PRIMARY KEY,
            job_id        TEXT NOT NULL,
            deleted_at    TEXT NOT NULL,
            files_removed INTEGER NOT NULL DEFAULT 0
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
            # LC-11: create the deletion audit log table alongside the jobs table.
            conn.execute(self._DELETION_LOG_DDL)
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
        after_id: str | None = None,
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
        # BE-06: keyset pagination — when after_id is supplied, restrict to rows
        # that come *after* the cursor job in the newest-first sort order.
        # The sub-condition (created_at, id) < (cursor_ts, after_id) avoids
        # duplicate-timestamp gaps without requiring an extra index.
        if after_id is not None:
            conditions.append(
                "(created_at < (SELECT created_at FROM jobs WHERE id = ?)"
                " OR (created_at = (SELECT created_at FROM jobs WHERE id = ?)"
                "     AND id < ?))"
            )
            params.extend([after_id, after_id, after_id])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        sql = (
            f"SELECT data FROM jobs {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [Job.model_validate_json(r[0]) for r in rows]

    def count(
        self,
        *,
        status: "JobStatus | None" = None,
        country: "Country | None" = None,
        query: "str | None" = None,
    ) -> int:
        """Return total matching job count without limit/offset.

        API-19: used to populate the X-Total-Count response header on
        GET /jobs so that clients can implement correct pagination.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if country is not None:
            conditions.append("country = ?")
            params.append(country.value)
        if query is not None:
            escaped = (
                query
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            conditions.append("case_name LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT COUNT(*) FROM jobs {where}"
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def delete(self, job_id: str) -> bool:
        """Remove a single job.  Returns True if it existed, False otherwise."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    def log_deletion(self, job_id: str, files_removed: int = 0) -> None:
        """LC-11: Append a deletion event to the audit log.

        Called by the DELETE /jobs/{id} endpoint after a job and its files
        have been removed.  The audit log survives across server restarts
        (WAL-mode SQLite) and is accessible via GET /admin/audit.
        """
        import uuid as _uuid
        entry_id = str(_uuid.uuid4())
        deleted_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO deletion_log (id, job_id, deleted_at, files_removed) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, job_id, deleted_at, files_removed),
            )

    def list_deletions(self, limit: int = 100) -> list[dict]:
        """LC-11: Return recent deletion audit log entries (newest first).

        Each entry has ``job_id``, ``deleted_at`` (ISO-8601 UTC), and
        ``files_removed`` (count of files deleted alongside the job record).
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id, deleted_at, files_removed FROM deletion_log "
                "ORDER BY deleted_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"job_id": r[0], "deleted_at": r[1], "files_removed": r[2]}
            for r in rows
        ]

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
        # SEC-01: Remove any stale .tmp file left by a previous crash between
        # the write and the atomic rename in _save_locked().  This prevents
        # the workspace from being stuck in a partially-written state on
        # subsequent startups.
        _stale_tmp = self._path.with_suffix(".tmp")
        if _stale_tmp.exists():
            _log.warning(
                "WorkspaceStore: removing stale temp file %s left by a previous crash",
                _stale_tmp,
            )
            _stale_tmp.unlink(missing_ok=True)
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

    def add_xrpl_asset(self, spec: str) -> WorkspaceConfig:
        """Register an XRPL asset spec (no-op if already present).

        *spec* must be in 'SYMBOL.rIssuerAddress' format, e.g.
        ``'SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL'``.
        """
        with self._lock:
            cfg = self._load_locked()
            if spec not in cfg.xrpl_assets:
                cfg = cfg.model_copy(
                    update={"xrpl_assets": [*cfg.xrpl_assets, spec]}
                )
                self._save_locked(cfg)
            return cfg

    def remove_xrpl_asset(self, spec: str) -> WorkspaceConfig:
        """Remove an XRPL asset spec."""
        with self._lock:
            cfg = self._load_locked()
            cfg = cfg.model_copy(
                update={"xrpl_assets": [a for a in cfg.xrpl_assets if a != spec]}
            )
            self._save_locked(cfg)
            return cfg

    def clear(self) -> WorkspaceConfig:
        """Reset the workspace to an empty state (removes all accounts, CSV files, and XRPL assets)."""
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
