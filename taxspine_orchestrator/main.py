"""FastAPI application — HTTP entry point for the orchestrator."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import shutil
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Security, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from .config import settings
from .dedup import router as dedup_router
from .lots import router as lots_router
from .models import Country, CsvFileSpec, CsvSourceType, Job, JobInput, JobOutput, JobStatus, PipelineMode, ValuationMode, WorkspaceConfig, _XRPL_ADDRESS_RE
from .prices import router as prices_router
from .services import JobService
from .storage import SqliteJobStore, WorkspaceStore

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

_api_key_header = APIKeyHeader(name="X-Orchestrator-Key", auto_error=False)


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
            detail="Invalid or missing X-Orchestrator-Key header",
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
    _startup_logger.warning(
        "ORCHESTRATOR_KEY is not set — all API endpoints are PUBLICLY ACCESSIBLE. "
        "Set ORCHESTRATOR_KEY in your environment or .env file before deploying "
        "to any network-reachable host."
    )

# Persistent SQLite job store — jobs survive server restarts.
_job_store = SqliteJobStore(settings.DATA_DIR / "jobs.db")
_job_service = JobService(_job_store)

# Persistent workspace — accounts and CSV files survive server restarts.
_workspace_store = WorkspaceStore(settings.DATA_DIR / "workspace.json")

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
app.include_router(prices_router, dependencies=[Depends(_require_key)])
app.include_router(dedup_router,  dependencies=[Depends(_require_key)])
app.include_router(lots_router,   dependencies=[Depends(_require_key)])


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

    # OUTPUT_DIR writable
    out_ok = os.access(settings.OUTPUT_DIR, os.W_OK)
    checks["output_dir"] = "ok" if out_ok else "error"

    # CLI binaries present
    for cli_name in ["taxspine-nor-report", "taxspine-xrpl-nor"]:
        checks[cli_name] = "ok" if shutil.which(cli_name) else "missing"

    overall_ok = all(v == "ok" for v in checks.values())
    # Always return HTTP 200 — this is a liveness probe; the process is alive
    # and can respond regardless of CLI binary availability.  Callers that need
    # readiness information should inspect the "status" field in the body
    # ("ok" vs "degraded") rather than the HTTP status code.
    return JSONResponse(
        {"status": "ok" if overall_ok else "degraded", **checks},
        status_code=200,
    )


# ── Jobs ──────────────────────────────────────────────────────────────────────


@app.post("/jobs", response_model=Job, status_code=201, tags=["jobs"], dependencies=[Depends(_require_key)])
def create_job(job_input: JobInput) -> Job:
    """Create a new tax job (PENDING)."""
    return _job_service.create_job(job_input)


@app.get("/jobs", response_model=list[Job], tags=["jobs"], dependencies=[Depends(_require_key)])
def list_jobs(
    status: Optional[JobStatus] = Query(default=None, description="Filter by job status"),
    country: Optional[Country] = Query(default=None, description="Filter by country"),
    query: Optional[str] = Query(
        default=None,
        description="Free-text search against case_name (case-insensitive substring match)",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Max jobs to return"),
    offset: int = Query(default=0, ge=0, description="Number of jobs to skip"),
) -> list[Job]:
    """List jobs, sorted newest-first, with filtering and paging."""
    return _job_service.list_jobs(
        status=status, country=country, query=query,
        limit=limit, offset=offset,
    )


@app.get("/jobs/{job_id}", response_model=Job, tags=["jobs"], dependencies=[Depends(_require_key)])
def get_job(job_id: str) -> Job:
    """Retrieve a single job by ID."""
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/jobs/{job_id}/start", tags=["jobs"], dependencies=[Depends(_require_key)])
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


@app.post("/jobs/{job_id}/cancel", tags=["jobs"], dependencies=[Depends(_require_key)])
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


@app.delete("/jobs/{job_id}", tags=["jobs"], dependencies=[Depends(_require_key)])
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
    _job_store.delete(job_id)
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


@app.get("/jobs/{job_id}/review", tags=["jobs"], dependencies=[Depends(_require_key)])
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
        except (OSError, ValueError):
            pass

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

    with dest.open("wb") as out:
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

    tax_year: int
    country: Country = Country.NORWAY
    case_name: Optional[str] = None
    pipeline_mode: PipelineMode = PipelineMode.PER_FILE
    valuation_mode: ValuationMode = ValuationMode.DUMMY
    csv_prices_path: Optional[str] = None
    include_trades: bool = False
    debug_valuation: bool = False
    dry_run: bool = False


@app.post("/workspace/run", response_model=Job, tags=["workspace"], dependencies=[Depends(_require_key)])
async def run_workspace_report(body: WorkspaceRunRequest) -> Job:
    """Create and immediately execute a job using all workspace accounts and CSVs.

    This is the primary year-over-year entry point:
    - All registered XRPL accounts are included automatically.
    - All registered CSV files are included automatically.
    - Change only ``tax_year`` from one year to the next.

    API-03: execution is offloaded to a thread-pool worker via
    ``asyncio.to_thread`` so the event loop is not blocked during the
    (potentially long-running) CLI subprocess calls.

    Returns the completed (or failed) job.
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
    )

    job = _job_service.create_job(job_input)
    # API-03: offload blocking CLI execution to the thread pool so the FastAPI
    # event loop is not blocked during subprocess calls.
    result = await asyncio.to_thread(_job_service.start_job_execution, job.id)
    if result is None:
        raise HTTPException(status_code=500, detail="Job execution returned None")
    return result


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
                "severity": severity,
                "category": "health",
                "message": f"{check_name}: {check_val}",
                "job_id": None,
                "detail": [],
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
                data = _json.loads(Path(p).read_text(encoding="utf-8"))
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
                "severity": severity,
                "category": "review",
                "message": msg,
                "job_id": job.id,
                "detail": all_warnings,
            })

    # ── Sort: error → warn → info ───────────────────────────────────────────
    _order = {"error": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: _order.get(a["severity"], 9))

    return alerts
