"""FastAPI application — HTTP entry point for the orchestrator."""

from __future__ import annotations

import asyncio
import datetime as _dt
import json as _json
import logging
import os
import re
import shutil
from enum import Enum
from pathlib import Path
from typing import Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response, Security, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import settings
from .dedup import router as dedup_router
from .lots import router as lots_router
from .models import (
    CancelledJobResponse, Country, CsvFileSpec, CsvSourceType,
    DeletedJobResponse, Job, JobInput, JobOutput, JobReviewResponse,
    JobStatus, PipelineMode, StartJobResponse, ValuationMode, WorkspaceConfig,
    _XRPL_ADDRESS_RE,
)
from .prices import router as prices_router
from .review import router as review_router
from .services import JobService
from .storage import SqliteJobStore, WorkspaceStore

# ── INFRA-21: optional structured JSON logging ────────────────────────────────
# Activated by setting LOG_FORMAT=json in the environment (e.g. in production
# Compose files).  Emits one JSON object per log line so that Synology Log
# Center and SIEM tools can filter by level, logger, and message fields.
# Left as plain text by default so local dev / test output remains readable.


class _JsonLogFormatter(logging.Formatter):
    """Emit log records as compact JSON lines with structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts":     self.formatTime(record),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return _json.dumps(entry, ensure_ascii=False)


if os.environ.get("LOG_FORMAT", "").lower() == "json":
    _json_handler = logging.StreamHandler()
    _json_handler.setFormatter(_JsonLogFormatter())
    logging.basicConfig(handlers=[_json_handler], level=logging.INFO, force=True)


# ── SEC-03: Sensitive-header log scrub ────────────────────────────────────────
# Defensive filter: if DEBUG-level HTTP middleware logging is ever enabled,
# X-Api-Key and Authorization header values are replaced with [REDACTED]
# before any handler emits the record.  Applied to the root logger so it
# covers ALL handlers (plain-text and JSON) without further configuration.

class _SensitiveHeaderFilter(logging.Filter):
    """Redact API-key and bearer-token values from every log record."""

    _PATTERNS = [
        (re.compile(r'(?i)(x-api-key\s*[:=]\s*)\S+'), r'\1[REDACTED]'),
        (re.compile(r'(?i)(authorization\s*[:=]\s*\S+\s*)\S+'), r'\1[REDACTED]'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        msg = record.getMessage()
        for pattern, replacement in self._PATTERNS:
            if pattern.search(msg):
                msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = ()
        return True


logging.getLogger().addFilter(_SensitiveHeaderFilter())


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(title="Taxspine Orchestrator")

# Allow browser access from configured origins only.
# Note: allow_credentials=True is intentionally omitted — cookies are not used
# and combining it with a wildcard origin violates the CORS spec.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Authentication ────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)


async def _require_key(key: str | None = Security(_api_key_header)) -> None:
    """Reject requests that are missing or carry the wrong key.

    When ``settings.ORCHESTRATOR_KEY`` is empty (the default) the check is
    skipped entirely so that local / dev deployments work without any
    configuration.
    """
    expected = settings.ORCHESTRATOR_KEY
    if expected and key != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Api-Key header",
        )

# ── Bootstrap ─────────────────────────────────────────────────────────────────

# Ensure all working directories exist before the first request.
settings.ensure_dirs()

# INFRA-17: Warn loudly at startup when authentication is disabled.
# An empty ORCHESTRATOR_KEY is acceptable for local/dev use but silently
# exposes the entire API on any reachable network interface in production.
_startup_logger = logging.getLogger(__name__)
_log = _startup_logger  # module-level logger used by endpoint handlers
if not settings.ORCHESTRATOR_KEY:
    if settings.REQUIRE_AUTH:
        raise RuntimeError(
            "ORCHESTRATOR_KEY is not set but REQUIRE_AUTH=true. "
            "Set ORCHESTRATOR_KEY in your environment or .env file before starting "
            "the server, or set REQUIRE_AUTH=false for local/dev use."
        )
    _startup_logger.warning(
        "ORCHESTRATOR_KEY is not set — all API endpoints are PUBLICLY ACCESSIBLE. "
        "Set ORCHESTRATOR_KEY in your environment or .env file before deploying "
        "to any network-reachable host. "
        "Set REQUIRE_AUTH=true to turn this warning into a hard startup failure."
    )

# SEC-17: Validate CLI binary names at startup.
# If a configured binary name cannot be resolved via shutil.which() the server
# still starts (CLIs may be installed later or not needed in all deployments),
# but a loud WARNING is emitted so misconfigured environments are detectable.
# This also acts as a canary: if a binary name was set to an arbitrary path via
# environment variable, `which` reveals whether that path is executable.
_CLI_NAMES: tuple[str, ...] = (
    settings.TAXSPINE_XRPL_NOR_CLI,
    settings.TAXSPINE_NOR_REPORT_CLI,
    settings.TAXSPINE_NOR_MULTI_CLI,
    settings.TAXSPINE_UK_REPORT_CLI,
    settings.BLOCKCHAIN_READER_CLI,
)
for _cli in _CLI_NAMES:
    if not shutil.which(_cli):
        _startup_logger.warning(
            "SEC-17: CLI binary %r not found in PATH — jobs requiring this "
            "binary will fail at execution time.  Ensure the correct taxspine "
            "packages are installed (or override via environment variable).",
            _cli,
        )

# Persistent SQLite job store — jobs survive server restarts.
_job_store = SqliteJobStore(settings.DATA_DIR / "jobs.db")

# Persistent workspace — accounts, CSV files, and XRPL assets survive server restarts.
_workspace_store = WorkspaceStore(settings.DATA_DIR / "workspace.json")

# JobService wired with workspace so auto-fetch includes registered XRPL assets.
_job_service = JobService(_job_store, workspace_store=_workspace_store)

# Wire workspace assets into the prices router so POST /prices/fetch also
# auto-includes tokens registered via POST /workspace/xrpl-assets.
import taxspine_orchestrator.prices as _prices_mod  # noqa: E402
_prices_mod._workspace_assets_provider  = lambda: _workspace_store.load().xrpl_assets
# Auto-discover IOU tokens from registered XRPL accounts via account_lines.
# Ensures all tokens held by the user are priced without manual registration.
_prices_mod._workspace_accounts_provider = lambda: _workspace_store.load().xrpl_accounts

# API-17: retain strong references to background tasks so the garbage collector
# cannot discard them before they complete.  Each task removes itself from the
# set via add_done_callback once finished.
_background_tasks: set[asyncio.Task] = set()

# ── Static UI ─────────────────────────────────────────────────────────────────

_UI_DIR = Path(__file__).parent.parent / "ui"

if _UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")

# ── Routers ───────────────────────────────────────────────────────────────────

# Sub-routers: all endpoints require the same key as mutating endpoints.
# When ORCHESTRATOR_KEY is empty (dev/local default) the dependency is a
# no-op and every request is accepted — behaviour is unchanged.
app.include_router(prices_router,  dependencies=[Depends(_require_key)])
app.include_router(dedup_router,   dependencies=[Depends(_require_key)])
app.include_router(lots_router,    dependencies=[Depends(_require_key)])
app.include_router(review_router,  dependencies=[Depends(_require_key)])


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirect / to the UI dashboard."""
    return RedirectResponse(url="/ui/")


# ── Helpers ───────────────────────────────────────────────────────────────────


class FileKind(str, Enum):
    """Supported output-file kinds for the file-download endpoint."""

    GAINS = "gains"
    WEALTH = "wealth"
    SUMMARY = "summary"
    REPORT = "report"     # self-contained HTML tax report
    RF1159 = "rf1159"     # RF-1159 (Altinn) JSON export — Norway jobs only
    REVIEW = "review"     # machine-readable NorwayReviewSummary JSON — Norway only
    LOG = "log"


_KIND_TO_FIELD: Dict[FileKind, str] = {
    FileKind.GAINS: "gains_csv_path",
    FileKind.WEALTH: "wealth_csv_path",
    FileKind.SUMMARY: "summary_json_path",
    FileKind.REPORT: "report_html_path",
    FileKind.RF1159: "rf1159_json_path",
    FileKind.REVIEW: "review_json_path",
    FileKind.LOG: "log_path",
}

_KIND_MEDIA_TYPE: Dict[FileKind, str] = {
    FileKind.GAINS: "text/csv",
    FileKind.WEALTH: "text/csv",
    FileKind.SUMMARY: "application/json",
    FileKind.REPORT: "text/html",
    FileKind.RF1159: "application/json",
    FileKind.REVIEW: "application/json",
    FileKind.LOG: "text/plain",
}

_KIND_EXTENSION: Dict[FileKind, str] = {
    FileKind.GAINS: "csv",
    FileKind.WEALTH: "csv",
    FileKind.SUMMARY: "json",
    FileKind.REPORT: "html",
    FileKind.RF1159: "json",
    FileKind.REVIEW: "json",
    FileKind.LOG: "txt",
}


# ── Meta ──────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["meta"])
async def health() -> JSONResponse:
    """Liveness + readiness probe — checks DB, output dir, and CLI binaries."""
    checks: dict = {}

    # DB reachable
    try:
        _job_store.ping()
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        # SEC-16: log the full exception server-side but return only an opaque
        # status to callers — raw exception text can expose DB file paths and
        # SQLite internals to unauthenticated observers.
        _log.error("Health check: DB ping failed: %s", exc)
        checks["db"] = "error"

    # Storage directories writable (B-M3: check all three dirs, not just output).
    for _dir_name, _dir_path in [
        ("output_dir", settings.OUTPUT_DIR),
        ("upload_dir", settings.UPLOAD_DIR),
        ("prices_dir", settings.PRICES_DIR),
    ]:
        checks[_dir_name] = "ok" if os.access(_dir_path, os.W_OK) else "error"

    # CLI binaries present
    for cli_name in ["taxspine-nor-report", "taxspine-xrpl-nor"]:
        checks[cli_name] = "ok" if shutil.which(cli_name) else "missing"

    overall_ok = all(v == "ok" for v in checks.values())

    # INFRA-08: return 503 when core infrastructure is unhealthy so that
    # container orchestrators (Docker HEALTHCHECK, Kubernetes readiness probes)
    # correctly detect a degraded pod and stop routing traffic to it.
    #
    # "Critical" means the process cannot meaningfully serve requests:
    #   - db == "error"      → cannot persist or retrieve jobs
    #   - output_dir != "ok" → cannot write report artefacts
    #
    # CLI binaries being absent is "degraded" (HTTP 200) — the server can
    # still respond and callers can diagnose via the response body.
    critical_ok = (
        checks.get("db") == "ok"
        and checks.get("output_dir") == "ok"
        and checks.get("upload_dir") == "ok"
        and checks.get("prices_dir") == "ok"
    )

    return JSONResponse(
        {"status": "ok" if overall_ok else "degraded", **checks},
        status_code=200 if critical_ok else 503,
    )


# ── Jobs ──────────────────────────────────────────────────────────────────────


@app.post("/jobs", response_model=Job, status_code=201, tags=["jobs"], dependencies=[Depends(_require_key)])
def create_job(job_input: JobInput) -> Job:
    """Create a new tax job (PENDING)."""
    return _job_service.create_job(job_input)


@app.get("/jobs", response_model=list[Job], tags=["jobs"], dependencies=[Depends(_require_key)])
def list_jobs(
    response: Response,  # API-19: FastAPI injects Response so we can set X-Total-Count header
    status: Optional[JobStatus] = Query(default=None, description="Filter by job status"),
    country: Optional[Country] = Query(default=None, description="Filter by country"),
    query: Optional[str] = Query(
        default=None,
        max_length=200,
        # LC-09: cap query string length to prevent disproportionate
        # server-side LIKE pattern expansion.  SQL metacharacters are already
        # escaped (SEC-01); this limit ensures the DB cannot be asked to scan
        # an arbitrarily large pattern string.
        description="Free-text search against case_name (case-insensitive substring match). Max 200 chars.",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Max jobs to return"),
    offset: int = Query(default=0, ge=0, description="Number of jobs to skip"),
    after_id: Optional[str] = Query(
        default=None,
        description=(
            "BE-06: Keyset pagination cursor — return only jobs older than the "
            "job with this ID. More efficient than offset at large scale. "
            "Use the last job ID from the previous page as the cursor."
        ),
    ),
) -> list[Job]:
    """List jobs, sorted newest-first, with filtering and paging.

    API-19: the ``X-Total-Count`` response header carries the total number
    of matching jobs ignoring ``limit``/``offset``, allowing clients to
    compute the number of pages without a separate count request.
    """
    jobs = _job_service.list_jobs(
        status=status, country=country, query=query,
        limit=limit, offset=offset, after_id=after_id,
    )
    total = _job_service.count_jobs(status=status, country=country, query=query)
    response.headers["X-Total-Count"] = str(total)
    return jobs


@app.get("/jobs/{job_id}", response_model=Job, tags=["jobs"], dependencies=[Depends(_require_key)])
def get_job(job_id: str) -> Job:
    """Retrieve a single job by ID."""
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/jobs/{job_id}/start", response_model=StartJobResponse, tags=["jobs"], dependencies=[Depends(_require_key)])
async def start_job(job_id: str) -> JSONResponse:
    """Accept a pending job for background execution and return 202 immediately.

    The job runs in a thread-pool worker via asyncio.to_thread so the HTTP
    response is returned before the subprocess completes.  Poll
    GET /jobs/{job_id} to observe the final status (RUNNING → COMPLETED/FAILED).

    API-04 / API-07: uses an atomic CAS (compare-and-swap) transition from
    PENDING → RUNNING before spawning the worker thread.  This eliminates the
    race window where two concurrent callers could both observe PENDING and both
    spawn execution threads for the same job.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        # Return current state for COMPLETED/FAILED/CANCELLED (idempotent).
        return JSONResponse(
            {"status": job.status.value, "job_id": job_id},
            status_code=200,
        )
    # API-04: atomically claim the job — only one caller wins the CAS.
    transitioned = _job_store.transition_status(job_id, JobStatus.PENDING, JobStatus.RUNNING)
    if transitioned is None:
        # CAS failed: another caller already started the job.
        raise HTTPException(status_code=409, detail="Job is already running")
    task = asyncio.create_task(asyncio.to_thread(_job_service.start_job_execution, job_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse({"status": "accepted", "job_id": job_id}, status_code=202)


@app.post("/jobs/{job_id}/cancel", response_model=CancelledJobResponse, tags=["jobs"], dependencies=[Depends(_require_key)])
async def cancel_job(job_id: str) -> dict:
    """Cancel a PENDING or RUNNING job by marking it CANCELLED.

    API-05: uses a distinct CANCELLED terminal state (not FAILED) so callers
    can distinguish user-initiated cancellation from execution errors.

    Note: if the job is already executing in a background thread the subprocess
    cannot be killed immediately.  The DB status is set to CANCELLED right away;
    the background execution thread checks for CANCELLED before overwriting the
    status with COMPLETED or FAILED (API-07), so the CANCELLED state is
    preserved in the common case.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a job with status {job.status.value}",
        )
    _job_store.update_status(job_id, JobStatus.CANCELLED, error_message="Cancelled by user")
    # B-M2: send SIGTERM to the active subprocess if one is running.
    pid = job.subprocess_pid
    if pid is not None:
        import signal as _sig  # noqa: PLC0415
        import os as _os  # noqa: PLC0415
        try:
            _os.kill(pid, _sig.SIGTERM)
            _log.info("cancel_job: sent SIGTERM to pid %d for job %s", pid, job_id)
        except (ProcessLookupError, PermissionError) as exc:
            # Process already exited or we lack permission — not an error.
            _log.warning("cancel_job: SIGTERM to pid %d failed: %s", pid, exc)
    return {"status": "cancelled", "job_id": job_id}


@app.post("/jobs/{job_id}/redact", response_model=Job, tags=["jobs"], dependencies=[Depends(_require_key)])
def redact_job(job_id: str) -> Job:
    """Remove personal data fields from a completed or failed job record.

    LC-04 — field-level erasure:
    Nulls out ``xrpl_accounts`` in the stored job input so that XRPL
    account addresses (pseudonymous personal data under GDPR) are no
    longer retained in the database after the job is no longer needed.

    Only COMPLETED and FAILED jobs may be redacted — PENDING and RUNNING
    jobs are rejected with HTTP 400 because their addresses are still
    required for execution.

    This endpoint is idempotent: redacting an already-redacted job returns
    HTTP 200 with the current (already-empty) record.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot redact a {job.status.value} job — "
                "only completed or failed jobs may be redacted."
            ),
        )
    updated_input = job.input.model_copy(update={"xrpl_accounts": []})
    updated = _job_store.update_job(job_id, input=updated_input)
    if updated is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return updated


def _collect_job_file_paths(job: Job) -> list[Path]:
    """Return all on-disk paths associated with *job* (output + input CSVs)."""
    paths: list[Path] = []
    out = job.output
    # Single-path output fields.
    for field in (
        out.gains_csv_path, out.wealth_csv_path, out.summary_json_path,
        out.report_html_path, out.rf1159_json_path, out.review_json_path,
        out.log_path,
    ):
        if field:
            paths.append(Path(field))
    # Multi-path list fields (NOR_MULTI / multi-account jobs).
    for lst in (out.report_html_paths or [], out.rf1159_json_paths or [], out.review_json_paths or []):
        paths.extend(Path(p) for p in lst)
    # Input CSV files (personal financial data).
    for spec in job.input.csv_files:
        paths.append(Path(spec.path))
    return paths


def _delete_job_files(job: Job) -> int:
    """Best-effort deletion of all files associated with *job*.

    Returns the count of files successfully removed.
    """
    removed = 0
    for path in _collect_job_file_paths(job):
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


@app.delete("/jobs/{job_id}", response_model=DeletedJobResponse, tags=["jobs"], dependencies=[Depends(_require_key)])
def delete_job(
    job_id: str,
    delete_files: bool = Query(
        default=True,
        description=(
            "Also delete all output files and input CSVs associated with this job from disk. "
            "LC-03: input CSVs and output files contain personal financial data and should "
            "be removed together with the job record unless explicitly retained."
        ),
    ),
) -> dict:
    """Permanently remove a job record from the store.

    Running jobs cannot be deleted — cancel first.

    When ``delete_files`` is True (default) all output artefacts
    (gains CSV, HTML report, RF-1159 JSON, execution log, …) and the
    job's input CSV files are deleted from disk.  Set ``delete_files=false``
    only if you need to retain the files for archival purposes.

    **Data notice (LC-03):** CSV files contain personal financial data
    (transaction histories).  Retaining them beyond the statutory tax
    retention period (7 years under Norwegian Bokføringsloven) violates
    the principle of storage limitation under GDPR Article 5(1)(e).
    """
    job = _job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a running job — cancel it first",
        )
    files_removed = _delete_job_files(job) if delete_files else 0
    # API-11: remove the job output directory after deleting individual files.
    # Individual file paths are removed by _delete_job_files(); the parent
    # OUTPUT_DIR/{job_id}/ directory is a leftover directory that accumulates
    # indefinitely without this cleanup.
    if delete_files:
        _job_output_dir = settings.OUTPUT_DIR / job_id
        if _job_output_dir.is_dir():
            try:
                shutil.rmtree(_job_output_dir)
            except OSError as _e:
                _log.debug("API-11: could not remove output dir %s: %s", _job_output_dir, _e)
    _job_store.delete(job_id)
    # LC-11: append deletion event to the audit log so that a record of which
    # jobs were removed (and when) survives across server restarts.
    if hasattr(_job_store, "log_deletion"):
        _job_store.log_deletion(job_id, files_removed)
    return {"deleted": True, "id": job_id, "files_removed": files_removed}


# ── File listing / download ───────────────────────────────────────────────────


@app.get("/jobs/{job_id}/files", tags=["files"], dependencies=[Depends(_require_key)])
def list_job_files(job_id: str) -> dict:
    """Return a JSON map of output-file kinds → paths for *job_id*.

    Only kinds whose path is non-``None`` are included.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    files: Dict[str, str] = {}
    for kind, field in _KIND_TO_FIELD.items():
        path = getattr(job.output, field)
        if path is not None:
            files[kind.value] = path
    return files


@app.get("/jobs/{job_id}/files/{kind}", tags=["files"], dependencies=[Depends(_require_key)])
def get_job_file(job_id: str, kind: FileKind) -> FileResponse:
    """Stream a single output file for *job_id*.

    Returns the file with an appropriate ``Content-Type`` and a
    ``Content-Disposition`` header so browsers offer a sensible filename.

    Raises 404 if the job, file kind, or file on disk is not found.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    field = _KIND_TO_FIELD[kind]
    path_str: str | None = getattr(job.output, field)
    if path_str is None:
        raise HTTPException(
            status_code=404,
            detail=f"No {kind.value} file recorded for this job",
        )

    resolved = Path(path_str).resolve()
    try:
        resolved.relative_to(settings.OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Access denied: file is outside output directory",
        )

    file_path = resolved
    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"File not found on disk: {path_str}",
        )

    ext = _KIND_EXTENSION[kind]
    filename = f"{kind.value}-{job_id}.{ext}"

    return FileResponse(
        path=file_path,
        media_type=_KIND_MEDIA_TYPE[kind],
        filename=filename,
    )


# ── Multi-report listing / download ──────────────────────────────────────────


@app.get("/jobs/{job_id}/reports", tags=["files"], dependencies=[Depends(_require_key)])
def list_job_reports(job_id: str) -> list[dict]:
    """Return a list of all HTML report files produced by *job_id*.

    Each item contains:
      - ``index``    — 0-based position, usable in ``GET /jobs/{id}/reports/{index}``
      - ``filename`` — the bare filename on disk (e.g. ``report_55d6caa0.html``)
      - ``url``      — relative download URL for this specific report

    Jobs with one XRPL account and two CSV files produce three items.
    Items are returned in execution order (XRPL accounts first, then CSVs).
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Prefer the new list; fall back to the legacy single-path field.
    paths: list[str] = job.output.report_html_paths or (
        [job.output.report_html_path] if job.output.report_html_path else []
    )
    return [
        {
            "index": i,
            "filename": Path(p).name,
            "url": f"/jobs/{job_id}/reports/{i}",
        }
        for i, p in enumerate(paths)
    ]


@app.get("/jobs/{job_id}/reports/{index}", tags=["files"], dependencies=[Depends(_require_key)])
def get_job_report_by_index(job_id: str, index: int) -> FileResponse:
    """Stream the HTML report at position *index* for *job_id*.

    Returns the file as ``text/html`` with a ``Content-Disposition`` header
    so browsers offer a sensible filename.

    Raises 404 if the job, index, or file on disk is not found.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    paths: list[str] = job.output.report_html_paths or (
        [job.output.report_html_path] if job.output.report_html_path else []
    )
    if index < 0 or index >= len(paths):
        raise HTTPException(
            status_code=404,
            detail=f"No report at index {index} (job has {len(paths)} report(s))",
        )

    resolved = Path(paths[index]).resolve()
    try:
        resolved.relative_to(settings.OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Access denied: file is outside output directory",
        )

    file_path = resolved
    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Report file not found on disk: {file_path}",
        )

    filename = f"report-{job_id}-{index}.html"
    return FileResponse(path=file_path, media_type="text/html", filename=filename)


# ── Review summary ────────────────────────────────────────────────────────────


@app.get("/jobs/{job_id}/review", response_model=JobReviewResponse, tags=["jobs"], dependencies=[Depends(_require_key)])
def get_job_review(job_id: str) -> dict:
    """Return an aggregated review summary for a completed Norway job.

    Reads all ``review_json_paths`` files for *job_id* and merges them into a
    single response.  Useful for surfacing warnings and transfer-link issues
    without downloading the full HTML report.

    Response fields:

    - ``has_unlinked_transfers`` — True if any invocation detected unlinked transfers
    - ``warning_count``          — total number of warnings across all invocations
    - ``warnings``               — flat list of warning strings
    - ``clean``                  — True when no warnings and no unlinked transfers
    - ``source_count``           — number of review JSON files successfully read

    Raises 404 when the job is not found or no review data is available.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    paths: list[str] = job.output.review_json_paths or (
        [job.output.review_json_path] if job.output.review_json_path else []
    )
    if not paths:
        raise HTTPException(
            status_code=404,
            detail="No review data available for this job (not a Norway job, or job not yet complete)",
        )

    all_warnings: list[str] = []
    has_unlinked = False
    loaded = 0

    for p in paths:
        try:
            data = _json.loads(Path(p).read_text(encoding="utf-8"))
            all_warnings.extend(data.get("warnings", []))
            if data.get("has_unlinked_transfers"):
                has_unlinked = True
            loaded += 1
        except (OSError, ValueError) as exc:
            # API-10: log unreadable review files so operators can diagnose
            # partial failures instead of silently losing data.
            _log.warning("API-10: could not read review file %s: %s", p, exc)

    if loaded == 0:
        raise HTTPException(
            status_code=404,
            detail="Review files not found on disk",
        )

    return {
        "has_unlinked_transfers": has_unlinked,
        "warning_count": len(all_warnings),
        "warnings": all_warnings,
        "clean": len(all_warnings) == 0 and not has_unlinked,
        "source_count": loaded,
    }


# ── CSV uploads ───────────────────────────────────────────────────────────────

# SEC-09: magic-byte signatures for binary file formats that are never valid CSVs.
# These bytes appear at the very start of the file regardless of extension or MIME type.
_BINARY_MAGIC_SIGNATURES: tuple[bytes, ...] = (
    b"\x50\x4b",            # ZIP-based formats (XLSX, DOCX, JAR, …)
    b"\x4d\x5a",            # PE executable / DLL (Windows MZ header)
    b"\x7f\x45\x4c\x46",    # ELF binary (Linux executable / shared lib)
    b"\x25\x50\x44\x46",    # %PDF
    b"\xff\xd8\xff",         # JPEG
    b"\x89\x50\x4e\x47",    # PNG
    b"\x47\x49\x46\x38",    # GIF
    b"\x42\x4d",            # BMP
    b"\xd0\xcf\x11\xe0",    # MS-CFB (legacy .xls / .doc)
)


def _is_binary_upload(header: bytes) -> bool:
    """Return True if *header* matches a known non-text binary file signature."""
    return any(header.startswith(sig) for sig in _BINARY_MAGIC_SIGNATURES)


_CSV_CONTENT_TYPES = {"text/csv", "application/vnd.ms-excel"}


@app.post("/uploads/csv", tags=["uploads"], dependencies=[Depends(_require_key)])
async def upload_csv(
    file: UploadFile = File(...),
    register: bool = Query(
        default=True,
        description="Automatically register the uploaded CSV in the workspace.",
    ),
    source_type: str = Query(
        default="generic_events",
        description=(
            "Exchange format of the uploaded CSV.  "
            "One of: generic_events, coinbase_csv, firi_csv."
        ),
    ),
) -> dict:
    """Accept a single CSV file via multipart upload.

    The file is stored under ``UPLOAD_DIR`` with a unique server-managed
    name.  When *register* is True (default), the file is also added to
    the workspace so it is included in future report runs automatically.

    The returned ``path`` is an absolute path suitable for use in
    ``JobInput.csv_files``.

    **Excel files (.xlsx / .xls) are not accepted.**  Please open your file
    in Excel or LibreOffice Calc and save it as CSV (.csv) before uploading.
    """
    original_name = file.filename or ""

    # Reject Excel files before reading any bytes.
    if Path(original_name).suffix.lower() in {".xlsx", ".xls"}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Excel files are not supported (received '{original_name}'). "
                "Please open the file in Excel or LibreOffice Calc and save it "
                "as CSV (.csv) before uploading."
            ),
        )

    content_type = file.content_type or ""
    if content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail=f"Expected a CSV file but received content-type '{content_type}'",
        )

    # Validate source_type.
    try:
        source_type_enum = CsvSourceType(source_type)
    except ValueError:
        valid = ", ".join(v.value for v in CsvSourceType)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source_type '{source_type}'. Valid values: {valid}",
        )

    upload_id = str(uuid4())
    filename = f"{upload_id}.csv"
    dest = settings.UPLOAD_DIR / filename

    # SEC-09: Read the first chunk before writing so we can inspect magic bytes.
    # A crafted file with a .csv extension but binary content (PE, ELF, ZIP, PDF…)
    # is rejected early rather than forwarded to the taxspine CLI.
    first_chunk = await file.read(8192)
    if first_chunk and _is_binary_upload(first_chunk[:16]):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Uploaded file '{original_name}' appears to be a binary file, not a CSV. "
                "Please upload a plain-text CSV (comma-separated values) file."
            ),
        )

    with dest.open("wb") as out:
        if first_chunk:
            out.write(first_chunk)
        while chunk := await file.read(8192):
            out.write(chunk)

    path_str = str(dest)
    spec = CsvFileSpec(path=path_str, source_type=source_type_enum)

    if register:
        _workspace_store.add_csv(spec)

    return {
        "id": upload_id,
        "path": path_str,
        "source_type": source_type_enum.value,
        "original_filename": original_name,
        "registered": register,
    }


# ── Upload management ─────────────────────────────────────────────────────────


@app.get("/uploads", tags=["uploads"], dependencies=[Depends(_require_key)])
def list_uploads() -> list[dict]:
    """List all uploaded CSV files with metadata (id, size, created_at).

    B-M1: callers can enumerate previously uploaded files and decide which to
    delete.  Returns an empty list when the upload directory is empty.
    """
    upload_dir = settings.UPLOAD_DIR
    results = []
    for csv_file in sorted(upload_dir.glob("*.csv")):
        stem = csv_file.stem  # upload_id (UUID)
        stat = csv_file.stat()
        results.append({
            "id": stem,
            "filename": csv_file.name,
            "size_bytes": stat.st_size,
            "created_at": stat.st_ctime,
        })
    return results


@app.delete("/uploads/{upload_id}", status_code=204, tags=["uploads"], dependencies=[Depends(_require_key)])
def delete_upload(upload_id: str) -> None:
    """Delete an uploaded CSV file by its upload ID.

    B-M1: prevents unbounded disk growth by allowing callers to remove
    files that are no longer needed.

    The ``upload_id`` must be a UUID produced by ``POST /uploads/csv``.
    Any path traversal attempt (``..``, ``/``) is rejected with 400.
    Returns 204 on success; 404 when the file does not exist.
    """
    # SEC: reject any upload_id containing path separators or dots.
    if not upload_id or "/" in upload_id or "\\" in upload_id or ".." in upload_id or "." in upload_id:
        raise HTTPException(status_code=400, detail="Invalid upload_id format")
    dest = settings.UPLOAD_DIR / f"{upload_id}.csv"
    # Verify the resolved path is still inside UPLOAD_DIR.
    try:
        dest.resolve().relative_to(settings.UPLOAD_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid upload_id format")
    if not dest.exists():
        raise HTTPException(status_code=404, detail="Upload not found")
    dest.unlink()


# ── Attach CSVs to a job ──────────────────────────────────────────────────────


class AttachCsvRequest(BaseModel):
    """Request body for attaching uploaded CSV files to an existing job."""

    csv_files: List[CsvFileSpec]


@app.post("/jobs/{job_id}/attach-csv", response_model=Job, tags=["jobs"], dependencies=[Depends(_require_key)])
def attach_csv_to_job(job_id: str, body: AttachCsvRequest) -> Job:
    """Append CSV file specs to a PENDING job's ``csv_files`` list."""
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail="Cannot attach CSV files to a non-pending job",
        )

    upload_dir = settings.UPLOAD_DIR.resolve()
    for spec in body.csv_files:
        # SEC-13: assert path is inside UPLOAD_DIR before accepting it.
        # This prevents an authenticated caller from attaching arbitrary
        # filesystem paths (e.g. /etc/passwd) to a job.
        try:
            Path(spec.path).resolve().relative_to(upload_dir)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"CSV path must be inside the upload directory "
                    f"({upload_dir}): {spec.path}"
                ),
            )
        if not Path(spec.path).is_file():
            raise HTTPException(
                status_code=400,
                detail=f"CSV file not found: {spec.path}",
            )

    existing_paths = {f.path for f in job.input.csv_files}
    new_csv_files = list(job.input.csv_files)
    for spec in body.csv_files:
        if spec.path not in existing_paths:
            new_csv_files.append(spec)
            existing_paths.add(spec.path)

    updated_input = job.input.model_copy(update={"csv_files": new_csv_files})
    updated = _job_store.update_job(job_id, input=updated_input)
    return updated  # type: ignore[return-value]


# ── Workspace ─────────────────────────────────────────────────────────────────


@app.get("/workspace", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def get_workspace() -> WorkspaceConfig:
    """Return the current persistent workspace configuration.

    **Data notice (LC-02):** ``workspace.json`` stores XRPL account addresses
    (pseudonymous personal data under GDPR).  The data directory containing
    this file **must** be encrypted at the OS level (e.g. LUKS, FileVault,
    BitLocker, or ZFS native encryption).  Do **not** store ``workspace.json``
    on an unencrypted volume.  Use ``DELETE /workspace`` to erase all stored
    addresses when they are no longer required.
    """
    return _workspace_store.load()


@app.delete("/workspace", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def purge_workspace(
    delete_files: bool = Query(
        default=False,
        description=(
            "Also delete uploaded CSV files registered in the workspace from disk. "
            "Set to true to fully erase personal financial data from this server."
        ),
    ),
) -> WorkspaceConfig:
    """Erase all registered XRPL accounts and CSV files from the workspace.

    LC-01 — right-to-erasure / data retention:
    Removes all XRPL account addresses and CSV file registrations from the
    persistent workspace.  When ``delete_files`` is True, the actual CSV
    files are also deleted from disk.

    **Recommended retention schedule:**

    - Tax records: retain for **7 years** from the end of the income year
      (Norwegian Bokføringsloven § 13).
    - After the retention period, call this endpoint with ``delete_files=true``
      to satisfy GDPR Article 17 (right to erasure).

    This endpoint does **not** delete individual job records — use
    ``DELETE /jobs/{id}`` to remove job records and their associated output files.
    """
    ws = _workspace_store.load()
    files_removed = 0
    if delete_files:
        for spec in ws.csv_files:
            try:
                p = Path(spec.path)
                if p.is_file():
                    p.unlink()
                    files_removed += 1
            except OSError:
                pass
    cleared = _workspace_store.clear()
    return cleared


class AddAccountRequest(BaseModel):
    account: str

    @field_validator("account")
    @classmethod
    def validate_xrpl_address(cls, v: str) -> str:
        v = v.strip()
        if not _XRPL_ADDRESS_RE.match(v):
            raise ValueError(f"Invalid XRPL address: {v}")
        return v


@app.post("/workspace/accounts", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def add_workspace_account(body: AddAccountRequest) -> WorkspaceConfig:
    """Register an XRPL account address in the workspace."""
    account = body.account  # already validated and stripped by the validator
    return _workspace_store.add_account(account)


@app.delete("/workspace/accounts/{account}", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def remove_workspace_account(account: str) -> WorkspaceConfig:
    """Remove an XRPL account address from the workspace."""
    return _workspace_store.remove_account(account)


# ── Workspace XRPL assets ─────────────────────────────────────────────────────

_XRPL_ASSET_SPEC_RE = re.compile(
    r'^[A-Za-z0-9]{1,20}\.[rR][1-9A-HJ-NP-Za-km-z]{24,33}$'
)


class AddXrplAssetRequest(BaseModel):
    """Request body for POST /workspace/xrpl-assets."""

    spec: str = Field(
        description=(
            "XRPL token asset in 'SYMBOL.rIssuerAddress' format, "
            "e.g. 'SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL'. "
            "Used to auto-include this token's NOK price in every price fetch."
        )
    )

    @field_validator("spec")
    @classmethod
    def validate_spec(cls, v: str) -> str:
        v = v.strip()
        if not _XRPL_ASSET_SPEC_RE.match(v):
            raise ValueError(
                f"Invalid XRPL asset spec {v!r}. "
                "Must be 'SYMBOL.rIssuerAddress' (e.g. 'SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL')."
            )
        return v


@app.post("/workspace/xrpl-assets", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def add_workspace_xrpl_asset(body: AddXrplAssetRequest) -> WorkspaceConfig:
    """Register an XRPL token asset for automatic NOK price tracking.

    The spec is added to ``workspace.xrpl_assets`` and will be passed to every
    subsequent price fetch (both explicit ``POST /prices/fetch`` calls and the
    inline auto-fetch that fires when a job starts without a cached price table).

    Idempotent — registering the same spec twice has no effect.
    """
    return _workspace_store.add_xrpl_asset(body.spec)


@app.delete("/workspace/xrpl-assets/{spec:path}", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def remove_workspace_xrpl_asset(spec: str) -> WorkspaceConfig:
    """Remove an XRPL token asset spec from the workspace."""
    return _workspace_store.remove_xrpl_asset(spec)


class AddCsvRequest(BaseModel):
    path: str
    source_type: str = "generic_events"


@app.post("/workspace/csv", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def add_workspace_csv(body: AddCsvRequest) -> WorkspaceConfig:
    """Register a CSV file path in the workspace."""
    csv_path = Path(body.path).resolve()
    try:
        csv_path.relative_to(settings.UPLOAD_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="CSV path must be inside the upload directory",
        )
    if not csv_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"File not found on disk: {body.path}",
        )
    try:
        source_type_enum = CsvSourceType(body.source_type)
    except ValueError:
        valid = ", ".join(v.value for v in CsvSourceType)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source_type '{body.source_type}'. Valid values: {valid}",
        )
    return _workspace_store.add_csv(CsvFileSpec(path=str(csv_path), source_type=source_type_enum))


@app.delete("/workspace/csv", response_model=WorkspaceConfig, tags=["workspace"], dependencies=[Depends(_require_key)])
def remove_workspace_csv(body: AddCsvRequest) -> WorkspaceConfig:
    """Remove a CSV file path from the workspace."""
    return _workspace_store.remove_csv(body.path)


class WorkspaceRunRequest(BaseModel):
    """Parameters for a workspace-wide report run."""

    tax_year: int = Field(
        ...,
        ge=2009,
        le=2100,
        description=(
            "Tax year to report (e.g. 2025).  Must be 2009 or later "
            "and no later than 2100."
        ),
    )
    country: Country = Country.NORWAY
    case_name: Optional[str] = None
    pipeline_mode: PipelineMode = PipelineMode.PER_FILE
    valuation_mode: ValuationMode = ValuationMode.PRICE_TABLE
    csv_prices_path: Optional[str] = None
    include_trades: bool = False
    debug_valuation: bool = False
    dry_run: bool = False
    unlinked_transfer_out_policy: Literal["skip", "dispose"] = "skip"


@app.post("/workspace/run", response_model=Job, tags=["workspace"], dependencies=[Depends(_require_key)])
async def run_workspace_report(body: WorkspaceRunRequest, background_tasks: BackgroundTasks) -> Job:
    """Create a job using all workspace accounts and CSVs, start it asynchronously.

    This is the primary year-over-year entry point:
    - All registered XRPL accounts are included automatically.
    - All registered CSV files are included automatically.
    - Change only ``tax_year`` from one year to the next.

    API-03: execution is dispatched to a background task so the HTTP response
    is returned immediately with the created job (status=PENDING). The caller
    should poll ``GET /jobs/{id}`` until the status is COMPLETED or FAILED.
    This prevents HTTP timeouts on large datasets.
    """
    ws = _workspace_store.load()
    if not ws.xrpl_accounts and not ws.csv_files:
        raise HTTPException(
            status_code=400,
            detail=(
                "Workspace has no XRPL accounts or CSV files configured. "
                "Add at least one account or upload a CSV file first."
            ),
        )

    label = body.case_name or (
        f"{body.country.value.capitalize()} {body.tax_year}"
    )
    job_input = JobInput(
        xrpl_accounts=list(ws.xrpl_accounts),
        csv_files=list(ws.csv_files),
        tax_year=body.tax_year,
        country=body.country,
        case_name=label,
        pipeline_mode=body.pipeline_mode,
        valuation_mode=body.valuation_mode,
        csv_prices_path=body.csv_prices_path,
        include_trades=body.include_trades,
        debug_valuation=body.debug_valuation,
        dry_run=body.dry_run,
        unlinked_transfer_out_policy=body.unlinked_transfer_out_policy,
    )

    job = _job_service.create_job(job_input)
    # API-03: dispatch execution to the background so we return immediately.
    # BackgroundTasks runs sync callables in the thread pool after the response
    # is sent, so the ASGI worker is not blocked.
    background_tasks.add_task(_job_service.start_job_execution, job.id)
    return job


# ── Alerts ────────────────────────────────────────────────────────────────────


_ALERTS_SCAN_LIMIT = 20   # how many recent jobs to inspect for review alerts


@app.get("/alerts", tags=["meta"], dependencies=[Depends(_require_key)])
async def get_alerts(
    limit: int = Query(
        default=_ALERTS_SCAN_LIMIT,
        ge=1,
        le=100,
        description="Number of recent completed/failed jobs to scan for review alerts.",
    ),
) -> list[dict]:
    """Return a list of actionable alerts across the system.

    Aggregates alerts from two sources:

    1. **Review alerts** — completed Norway jobs whose review JSON indicates
       warnings or unlinked transfers.  Each non-clean job produces one alert.
    2. **Health alerts** — checks from ``GET /health`` that are not ``"ok"``
       (missing CLIs, unwritable output dir, DB errors).

    Each alert item has:

    - ``severity``  — ``"error"`` | ``"warn"`` | ``"info"``
    - ``category``  — ``"review"`` | ``"health"``
    - ``message``   — short human-readable summary
    - ``job_id``    — present for review alerts; ``null`` for health alerts
    - ``detail``    — list of warning strings (review) or empty list (health)

    The response is sorted: ``"error"`` first, then ``"warn"``, then ``"info"``.
    Returns HTTP 200 with an empty list when no alerts are present.
    """
    import datetime as _dt_alerts
    _raised_at = _dt_alerts.datetime.now(_dt_alerts.timezone.utc).isoformat()

    alerts: list[dict] = []

    # ── 1. Health alerts ────────────────────────────────────────────────────
    health_checks: dict = {}

    try:
        _job_store.ping()
        health_checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        # SEC-16: log full detail server-side; return opaque status to caller.
        _log.error("Alerts: DB ping failed: %s", exc)
        health_checks["db"] = "error"

    out_ok = os.access(settings.OUTPUT_DIR, os.W_OK)
    health_checks["output_dir"] = "ok" if out_ok else "error"

    for cli_name in ["taxspine-nor-report", "taxspine-xrpl-nor"]:
        health_checks[cli_name] = "ok" if shutil.which(cli_name) else "missing"

    for check_name, check_val in health_checks.items():
        if check_val != "ok":
            severity = "error" if check_val == "error" else "warn"
            alerts.append({
                "severity":  severity,
                "category":  "health",
                "message":   f"{check_name}: {check_val}",
                "job_id":    None,
                "detail":    [],
                "raised_at": _raised_at,
            })

    # ── 2. Review alerts (recent completed + failed jobs) ───────────────────
    recent_jobs = _job_store.list(limit=limit)

    for job in recent_jobs:
        if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
            continue

        paths: list[str] = job.output.review_json_paths or (
            [job.output.review_json_path] if job.output.review_json_path else []
        )
        if not paths:
            continue

        all_warnings: list[str] = []
        has_unlinked = False
        loaded = 0

        for p in paths:
            try:
                # API-20: use asyncio.to_thread so the event loop is not blocked
                # by synchronous file I/O inside this async handler.
                raw = await asyncio.to_thread(Path(p).read_text, encoding="utf-8")
                data = _json.loads(raw)
                all_warnings.extend(data.get("warnings", []))
                if data.get("has_unlinked_transfers"):
                    has_unlinked = True
                loaded += 1
            except (OSError, ValueError):
                pass

        if loaded == 0:
            continue

        if all_warnings or has_unlinked:
            label = job.input.case_name or job.id
            if has_unlinked and all_warnings:
                msg = f"Job '{label}' - {len(all_warnings)} warning(s) + unlinked transfers"
            elif has_unlinked:
                msg = f"Job '{label}' - unlinked transfers detected"
            else:
                msg = f"Job '{label}' - {len(all_warnings)} warning(s)"

            severity = "error" if has_unlinked else "warn"
            alerts.append({
                "severity":  severity,
                "category":  "review",
                "message":   msg,
                "job_id":    job.id,
                "detail":    all_warnings,
                "raised_at": _raised_at,
            })

    # ── 3. Lot quality alerts (TL-18) ───────────────────────────────────────
    # Scan the FIFO carry-forward lot store for lots whose cost basis could not
    # be resolved (basis_status != "known").  These lots indicate missing-basis
    # events that will produce incorrect gain/loss figures if not resolved before
    # filing.  Import is lazy so the orchestrator still works when the tax_spine
    # package is absent (e.g. in a stripped Docker environment without CLIs).
    import datetime as _dt
    try:
        from tax_spine.pipeline import LotPersistenceStore  # type: ignore[import]

        _lot_db = settings.DATA_DIR / "lots.db"
        if _lot_db.is_file():
            _current_year = _dt.date.today().year
            with LotPersistenceStore(_lot_db) as _lot_store:
                for _yr in range(_current_year - 2, _current_year + 1):
                    _lots = await asyncio.to_thread(_lot_store.load_all_lots, _yr)
                    _non_known = [
                        _l for _l in _lots
                        if str(getattr(_l, "basis_status", "known")).lower() not in ("known",)
                    ]
                    if _non_known:
                        _detail = [
                            f"{getattr(_l, 'asset_symbol', '?')}: {getattr(_l, 'basis_status', '?')}"
                            for _l in _non_known[:10]
                        ]
                        alerts.append({
                            "severity":  "warn",
                            "category":  "lot_quality",
                            "message":   (
                                f"{len(_non_known)} lot(s) with unresolved cost basis "
                                f"in {_yr} — review before filing"
                            ),
                            "job_id":    None,
                            "detail":    _detail,
                            "raised_at": _raised_at,
                        })
    except ImportError:
        pass  # TL-18: tax_spine not installed — lot quality check skipped
    except Exception as _exc:  # noqa: BLE001
        _log.debug("TL-18: lot quality check skipped: %s", _exc)

    # ── Sort: error → warn → info ───────────────────────────────────────────
    _order = {"error": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: _order.get(a["severity"], 9))

    return alerts


# ── Admin — cleanup and audit ─────────────────────────────────────────────────


@app.post("/admin/cleanup", tags=["admin"], dependencies=[Depends(_require_key)])
def cleanup_old_jobs(
    older_than_days: int = Query(
        default=90,
        ge=1,
        le=3650,
        description=(
            "INFRA-06: Remove COMPLETED / FAILED / CANCELLED jobs (and their output "
            "files) last updated more than this many days ago.  RUNNING and PENDING "
            "jobs are never touched."
        ),
    ),
    dry_run: bool = Query(
        default=False,
        description=(
            "When True, return what *would* be removed without deleting anything. "
            "Use this to preview before committing to a cleanup run."
        ),
    ),
) -> dict:
    """TTL-based job cleanup — prevents unbounded disk growth on a NAS.

    INFRA-06: Scans all terminal-state jobs and removes any that were last
    updated more than ``older_than_days`` days ago.  For each eligible job
    the endpoint:

    1. Deletes the individual output files via ``_delete_job_files``.
    2. Removes the ``OUTPUT_DIR/{job_id}/`` directory.
    3. Deletes the job record from the database.

    Returns a summary of what was (or would be) removed.
    """
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=older_than_days)
    # Scan all jobs in batches — there may be thousands.
    all_jobs = _job_store.list(limit=10_000, offset=0)
    terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    eligible = [
        j for j in all_jobs
        if j.status in terminal and j.updated_at < cutoff
    ]

    if dry_run:
        return {
            "dry_run": True,
            "jobs_would_remove": len(eligible),
            "job_ids": [j.id for j in eligible],
        }

    jobs_removed = 0
    files_removed = 0
    for job in eligible:
        files_removed += _delete_job_files(job)
        out_dir = settings.OUTPUT_DIR / job.id
        if out_dir.is_dir():
            try:
                shutil.rmtree(out_dir)
            except OSError as _e:
                _log.debug("INFRA-06: cleanup could not remove dir %s: %s", out_dir, _e)
        _job_store.delete(job.id)
        if hasattr(_job_store, "log_deletion"):
            _job_store.log_deletion(job.id, 0)  # files already counted above
        jobs_removed += 1

    _log.info(
        "INFRA-06: cleanup complete — removed %d jobs, %d files (older_than_days=%d)",
        jobs_removed, files_removed, older_than_days,
    )
    return {
        "dry_run": False,
        "jobs_removed": jobs_removed,
        "files_removed": files_removed,
        "older_than_days": older_than_days,
    }


@app.get("/admin/audit", tags=["admin"], dependencies=[Depends(_require_key)])
def get_audit_log(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of deletion log entries to return (newest first).",
    ),
) -> list:
    """LC-11: Return the deletion audit log — records which jobs were removed and when.

    Each entry contains:

    - ``job_id``       — ID of the deleted job.
    - ``deleted_at``   — ISO-8601 UTC timestamp of the deletion.
    - ``files_removed``— Number of output/input files removed alongside the job.

    Entries are ordered newest-first.  The log persists across server restarts.
    Returns an empty list when no deletions have been recorded yet.
    """
    if not hasattr(_job_store, "list_deletions"):
        return []
    return _job_store.list_deletions(limit=limit)


# ── Diagnostics ───────────────────────────────────────────────────────────────


@app.get("/diagnostics", tags=["meta"], dependencies=[Depends(_require_key)])
async def get_diagnostics() -> dict:
    """System diagnostics snapshot — lot store, price cache, jobs, and dedup stats.

    Intended for the dashboard Diagnostics panel.  All I/O is non-blocking
    (wrapped in ``asyncio.to_thread``).  Never raises — errors are captured
    per-section and reported as ``{"error": "<message>"}``.

    Response sections:

    - ``lots``   — lot store DB existence, years available, size.
    - ``prices`` — cached price CSV files (count, age of combined CSV).
    - ``jobs``   — total/running/failed/completed counts, last completed timestamp.
    - ``dedup``  — number of dedup source databases, total skip-log entries.
    """

    def _lots_section() -> dict:
        db = settings.LOT_STORE_DB
        if not db.is_file():
            return {"db_exists": False, "years": [], "size_kb": None}
        try:
            from tax_spine.pipeline.lot_store import LotPersistenceStore  # noqa: PLC0415
            store = LotPersistenceStore(str(db))
            with store:
                years = store.list_years()
            size_kb = round(db.stat().st_size / 1024, 1)
            return {"db_exists": True, "years": years, "size_kb": size_kb}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _prices_section() -> dict:
        pdir = settings.PRICES_DIR
        if not pdir.is_dir():
            return {"csv_count": 0, "combined_csvs": []}
        try:
            import time as _time  # noqa: PLC0415
            all_csvs = list(pdir.glob("*.csv"))
            combined = [p for p in all_csvs if p.name.startswith("combined_")]
            now = _time.time()
            combined_info = []
            for p in sorted(combined):
                age_h = round((now - p.stat().st_mtime) / 3600, 1)
                combined_info.append({"name": p.name, "age_hours": age_h})
            return {
                "csv_count":    len(all_csvs),
                "combined_csvs": combined_info,
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _jobs_section() -> dict:
        try:
            all_jobs = _job_service.list_jobs(limit=10_000, offset=0)
            total     = len(all_jobs)
            running   = sum(1 for j in all_jobs if j.status == JobStatus.RUNNING)
            failed    = sum(1 for j in all_jobs if j.status == JobStatus.FAILED)
            completed = sum(1 for j in all_jobs if j.status == JobStatus.COMPLETED)
            last_done = None
            done_jobs = [j for j in all_jobs if j.status == JobStatus.COMPLETED]
            if done_jobs:
                last_done = max(j.updated_at for j in done_jobs if j.updated_at)
            return {
                "total":     total,
                "running":   running,
                "failed":    failed,
                "completed": completed,
                "last_completed_at": last_done,
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _dedup_section() -> dict:
        ddir = settings.DEDUP_DIR
        if not ddir.is_dir():
            return {"source_count": 0, "total_skips": 0}
        try:
            dbs = list(ddir.glob("*.db"))
            total_skips = 0
            for db in dbs:
                try:
                    import sqlite3 as _sqlite3  # noqa: PLC0415
                    con = _sqlite3.connect(str(db))
                    cur = con.execute(
                        "SELECT COUNT(*) FROM skip_log"
                        if _table_exists(con, "skip_log") else "SELECT 0"
                    )
                    total_skips += cur.fetchone()[0]
                    con.close()
                except Exception:  # noqa: BLE001
                    pass
            return {"source_count": len(dbs), "total_skips": total_skips}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _workspace_section() -> dict:
        try:
            ws = _workspace_store.load()
            return {
                "xrpl_account_count": len(ws.xrpl_accounts or []),
                "csv_file_count":     len(ws.csv_files or []),
                "xrpl_asset_count":   len(ws.xrpl_assets or []),
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _disk_section() -> dict:
        """Summarise disk usage of OUTPUT_DIR, UPLOAD_DIR, and PRICES_DIR."""
        def _sz(path: Path) -> dict:
            if not path.is_dir():
                return {"exists": False, "size_mb": 0.0, "file_count": 0}
            try:
                files = [f for f in path.rglob("*") if f.is_file()]
                total = sum(f.stat().st_size for f in files)
                return {
                    "exists":     True,
                    "size_mb":    round(total / 1_048_576, 2),
                    "file_count": len(files),
                }
            except OSError:
                return {"exists": True, "size_mb": None, "file_count": None}

        try:
            return {
                "output_dir": _sz(settings.OUTPUT_DIR),
                "upload_dir": _sz(settings.UPLOAD_DIR),
                "prices_dir": _sz(settings.PRICES_DIR),
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    lots, prices, jobs, dedup, workspace, disk_usage = await asyncio.gather(
        asyncio.to_thread(_lots_section),
        asyncio.to_thread(_prices_section),
        asyncio.to_thread(_jobs_section),
        asyncio.to_thread(_dedup_section),
        asyncio.to_thread(_workspace_section),
        asyncio.to_thread(_disk_section),
    )
    return {
        "lots":       lots,
        "prices":     prices,
        "jobs":       jobs,
        "dedup":      dedup,
        "workspace":  workspace,
        "disk_usage": disk_usage,
    }


def _table_exists(con, table: str) -> bool:
    """Return True if *table* exists in the SQLite connection *con*."""
    cur = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


# ── Maintenance ───────────────────────────────────────────────────────────────


@app.get("/maintenance/disk-usage", tags=["meta"], dependencies=[Depends(_require_key)])
async def disk_usage() -> dict:
    """Return disk usage summary for data directories.

    Reports file count and total size (bytes) for OUTPUT_DIR, UPLOAD_DIR,
    and PRICES_DIR.  Useful for monitoring unbounded growth before running
    cleanup.
    """
    def _dir_stats(path: Path) -> dict:
        if not path.is_dir():
            return {"exists": False, "file_count": 0, "total_bytes": 0}
        files = [f for f in path.rglob("*") if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        return {"exists": True, "file_count": len(files), "total_bytes": total}

    return {
        "output_dir":  {**_dir_stats(settings.OUTPUT_DIR),  "path": str(settings.OUTPUT_DIR)},
        "upload_dir":  {**_dir_stats(settings.UPLOAD_DIR),  "path": str(settings.UPLOAD_DIR)},
        "prices_dir":  {**_dir_stats(settings.PRICES_DIR),  "path": str(settings.PRICES_DIR)},
    }


@app.post("/maintenance/cleanup", tags=["meta"], dependencies=[Depends(_require_key)])
async def cleanup_old_files(
    max_age_days: int = Query(
        default=90,
        ge=1,
        le=3650,
        description="Delete files older than this many days from OUTPUT_DIR and UPLOAD_DIR.",
    ),
    dry_run: bool = Query(
        default=True,
        description="When true (default), list files that would be deleted without deleting them.",
    ),
) -> dict:
    """Delete job output files and uploaded CSVs older than max_age_days.

    Targets OUTPUT_DIR and UPLOAD_DIR only — never touches PRICES_DIR,
    DATA_DIR (SQLite databases), or DEDUP_DIR.

    Defaults to dry_run=true so the caller can preview the deletion list
    before committing.  Pass dry_run=false to actually delete.

    Returns a summary with the list of files affected and total bytes freed.
    """
    import time as _time

    cutoff = _time.time() - (max_age_days * 86400)
    affected: list[dict] = []

    for scan_dir in (settings.OUTPUT_DIR, settings.UPLOAD_DIR):
        if not scan_dir.is_dir():
            continue
        for fpath in scan_dir.rglob("*"):
            if not fpath.is_file():
                continue
            try:
                mtime = fpath.stat().st_mtime
                size  = fpath.stat().st_size
            except OSError:
                continue
            if mtime < cutoff:
                affected.append({
                    "path": str(fpath),
                    "size_bytes": size,
                    "age_days": round((_time.time() - mtime) / 86400, 1),
                })
                if not dry_run:
                    try:
                        fpath.unlink(missing_ok=True)
                    except OSError as exc:
                        _log.warning("cleanup: could not delete %s: %s", fpath, exc)

    total_bytes = sum(f["size_bytes"] for f in affected)
    return {
        "dry_run":       dry_run,
        "max_age_days":  max_age_days,
        "files_affected": len(affected),
        "bytes_affected": total_bytes,
        "files":         affected,
    }
