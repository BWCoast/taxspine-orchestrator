"""FastAPI application — HTTP entry point for the orchestrator."""

from __future__ import annotations

import asyncio
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
from .models import Country, CsvFileSpec, CsvSourceType, Job, JobInput, JobOutput, JobStatus, ValuationMode, WorkspaceConfig, _XRPL_ADDRESS_RE
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

# Persistent SQLite job store — jobs survive server restarts.
_job_store = SqliteJobStore(settings.DATA_DIR / "jobs.db")
_job_service = JobService(_job_store)

# Persistent workspace — accounts and CSV files survive server restarts.
_workspace_store = WorkspaceStore(settings.DATA_DIR / "workspace.json")

# ── Static UI ─────────────────────────────────────────────────────────────────

_UI_DIR = Path(__file__).parent.parent / "ui"

if _UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(prices_router)
app.include_router(dedup_router)
app.include_router(lots_router)


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
    LOG = "log"


_KIND_TO_FIELD: Dict[FileKind, str] = {
    FileKind.GAINS: "gains_csv_path",
    FileKind.WEALTH: "wealth_csv_path",
    FileKind.SUMMARY: "summary_json_path",
    FileKind.REPORT: "report_html_path",
    FileKind.RF1159: "rf1159_json_path",
    FileKind.LOG: "log_path",
}

_KIND_MEDIA_TYPE: Dict[FileKind, str] = {
    FileKind.GAINS: "text/csv",
    FileKind.WEALTH: "text/csv",
    FileKind.SUMMARY: "application/json",
    FileKind.REPORT: "text/html",
    FileKind.RF1159: "application/json",
    FileKind.LOG: "text/plain",
}

_KIND_EXTENSION: Dict[FileKind, str] = {
    FileKind.GAINS: "csv",
    FileKind.WEALTH: "csv",
    FileKind.SUMMARY: "json",
    FileKind.REPORT: "html",
    FileKind.RF1159: "json",
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
        checks["db"] = f"error: {exc}"

    # OUTPUT_DIR writable
    out_ok = os.access(settings.OUTPUT_DIR, os.W_OK)
    checks["output_dir"] = "ok" if out_ok else "error: not writable"

    # CLI binaries present
    for cli_name in ["taxspine-nor-report", "taxspine-xrpl-nor"]:
        checks[cli_name] = "ok" if shutil.which(cli_name) else "missing"

    overall_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if overall_ok else "degraded", **checks},
        status_code=200 if overall_ok else 503,
    )


# ── Jobs ──────────────────────────────────────────────────────────────────────


@app.post("/jobs", response_model=Job, tags=["jobs"], dependencies=[Depends(_require_key)])
def create_job(job_input: JobInput) -> Job:
    """Create a new tax job (PENDING)."""
    return _job_service.create_job(job_input)


@app.get("/jobs", response_model=list[Job], tags=["jobs"])
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


@app.get("/jobs/{job_id}", response_model=Job, tags=["jobs"])
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
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Job is already running")
    if job.status != JobStatus.PENDING:
        # Return current state for COMPLETED/FAILED (idempotent).
        return JSONResponse(
            {"status": job.status.value, "job_id": job_id},
            status_code=200,
        )
    asyncio.create_task(asyncio.to_thread(_job_service.start_job_execution, job_id))
    return JSONResponse({"status": "accepted", "job_id": job_id}, status_code=202)


@app.post("/jobs/{job_id}/cancel", tags=["jobs"], dependencies=[Depends(_require_key)])
async def cancel_job(job_id: str) -> dict:
    """Cancel a PENDING or RUNNING job by marking it FAILED.

    Note: if the job is already executing in a background thread the subprocess
    cannot be killed immediately.  The DB status is set to FAILED right away,
    but the background thread may still complete and overwrite the status with
    COMPLETED/FAILED depending on subprocess outcome.  This is a known
    limitation and acceptable for the current implementation.
    """
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a job with status {job.status.value}",
        )
    _job_store.update_status(job_id, JobStatus.FAILED, error_message="Cancelled by user")
    return {"status": "cancelled", "job_id": job_id}


# ── File listing / download ───────────────────────────────────────────────────


@app.get("/jobs/{job_id}/files", tags=["files"])
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


@app.get("/jobs/{job_id}/files/{kind}", tags=["files"])
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


@app.get("/jobs/{job_id}/reports", tags=["files"])
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


@app.get("/jobs/{job_id}/reports/{index}", tags=["files"])
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

    for spec in body.csv_files:
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


@app.get("/workspace", response_model=WorkspaceConfig, tags=["workspace"])
def get_workspace() -> WorkspaceConfig:
    """Return the current persistent workspace configuration."""
    return _workspace_store.load()


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
    valuation_mode: ValuationMode = ValuationMode.DUMMY
    csv_prices_path: Optional[str] = None
    include_trades: bool = False
    debug_valuation: bool = False
    dry_run: bool = False


@app.post("/workspace/run", response_model=Job, tags=["workspace"], dependencies=[Depends(_require_key)])
def run_workspace_report(body: WorkspaceRunRequest) -> Job:
    """Create and immediately execute a job using all workspace accounts and CSVs.

    This is the primary year-over-year entry point:
    - All registered XRPL accounts are included automatically.
    - All registered CSV files are included automatically.
    - Change only ``tax_year`` from one year to the next.

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
        valuation_mode=body.valuation_mode,
        csv_prices_path=body.csv_prices_path,
        include_trades=body.include_trades,
        debug_valuation=body.debug_valuation,
        dry_run=body.dry_run,
    )

    job = _job_service.create_job(job_input)
    result = _job_service.start_job_execution(job.id)
    if result is None:
        raise HTTPException(status_code=500, detail="Job execution returned None")
    return result
