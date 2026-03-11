"""FastAPI application — HTTP entry point for the orchestrator."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .models import Country, Job, JobInput, JobOutput, JobStatus, ValuationMode, WorkspaceConfig
from .prices import router as prices_router
from .services import JobService
from .storage import SqliteJobStore, WorkspaceStore

# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(title="Taxspine Orchestrator")

# Allow browser access from any origin (file://, localhost ports, NAS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    REPORT = "report"   # self-contained HTML tax report
    LOG = "log"


_KIND_TO_FIELD: Dict[FileKind, str] = {
    FileKind.GAINS: "gains_csv_path",
    FileKind.WEALTH: "wealth_csv_path",
    FileKind.SUMMARY: "summary_json_path",
    FileKind.REPORT: "report_html_path",
    FileKind.LOG: "log_path",
}

_KIND_MEDIA_TYPE: Dict[FileKind, str] = {
    FileKind.GAINS: "text/csv",
    FileKind.WEALTH: "text/csv",
    FileKind.SUMMARY: "application/json",
    FileKind.REPORT: "text/html",
    FileKind.LOG: "text/plain",
}

_KIND_EXTENSION: Dict[FileKind, str] = {
    FileKind.GAINS: "csv",
    FileKind.WEALTH: "csv",
    FileKind.SUMMARY: "json",
    FileKind.REPORT: "html",
    FileKind.LOG: "txt",
}


# ── Meta ──────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Simple liveness probe."""
    return {"status": "ok"}


# ── Jobs ──────────────────────────────────────────────────────────────────────


@app.post("/jobs", response_model=Job, tags=["jobs"])
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


@app.post("/jobs/{job_id}/start", response_model=Job, tags=["jobs"])
def start_job(job_id: str) -> Job:
    """Execute a pending job synchronously and return the final state."""
    job = _job_service.start_job_execution(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


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

    file_path = Path(path_str)
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


# ── CSV uploads ───────────────────────────────────────────────────────────────


_CSV_CONTENT_TYPES = {"text/csv", "application/vnd.ms-excel"}


@app.post("/uploads/csv", tags=["uploads"])
async def upload_csv(
    file: UploadFile = File(...),
    register: bool = Query(
        default=True,
        description="Automatically register the uploaded CSV in the workspace.",
    ),
) -> dict:
    """Accept a single CSV file via multipart upload.

    The file is stored under ``UPLOAD_DIR`` with a unique server-managed
    name.  When *register* is True (default), the file is also added to
    the workspace so it is included in future report runs automatically.

    The returned ``path`` is an absolute path suitable for use in
    ``JobInput.csv_files``.
    """
    content_type = file.content_type or ""
    if content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail=f"Expected a CSV file but received content-type '{content_type}'",
        )

    upload_id = str(uuid4())
    filename = f"{upload_id}.csv"
    dest = settings.UPLOAD_DIR / filename

    with dest.open("wb") as out:
        while chunk := await file.read(8192):
            out.write(chunk)

    path_str = str(dest)

    if register:
        _workspace_store.add_csv(path_str)

    return {
        "id": upload_id,
        "path": path_str,
        "original_filename": file.filename,
        "registered": register,
    }


# ── Attach CSVs to a job ──────────────────────────────────────────────────────


class AttachCsvRequest(BaseModel):
    """Request body for attaching uploaded CSV paths to an existing job."""

    csv_paths: List[str]


@app.post("/jobs/{job_id}/attach-csv", response_model=Job, tags=["jobs"])
def attach_csv_to_job(job_id: str, body: AttachCsvRequest) -> Job:
    """Append CSV file paths to a PENDING job's ``csv_files`` list."""
    job = _job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail="Cannot attach CSV files to a non-pending job",
        )

    for csv_path in body.csv_paths:
        if not Path(csv_path).is_file():
            raise HTTPException(
                status_code=400,
                detail=f"CSV file not found: {csv_path}",
            )

    existing = set(job.input.csv_files)
    new_csv_files = list(job.input.csv_files)
    for csv_path in body.csv_paths:
        if csv_path not in existing:
            new_csv_files.append(csv_path)
            existing.add(csv_path)

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


@app.post("/workspace/accounts", response_model=WorkspaceConfig, tags=["workspace"])
def add_workspace_account(body: AddAccountRequest) -> WorkspaceConfig:
    """Register an XRPL account address in the workspace."""
    account = body.account.strip()
    if not account:
        raise HTTPException(status_code=400, detail="Account address must not be empty")
    return _workspace_store.add_account(account)


@app.delete("/workspace/accounts/{account}", response_model=WorkspaceConfig, tags=["workspace"])
def remove_workspace_account(account: str) -> WorkspaceConfig:
    """Remove an XRPL account address from the workspace."""
    return _workspace_store.remove_account(account)


class AddCsvRequest(BaseModel):
    path: str


@app.post("/workspace/csv", response_model=WorkspaceConfig, tags=["workspace"])
def add_workspace_csv(body: AddCsvRequest) -> WorkspaceConfig:
    """Register a CSV file path in the workspace."""
    if not Path(body.path).is_file():
        raise HTTPException(
            status_code=400,
            detail=f"File not found on disk: {body.path}",
        )
    return _workspace_store.add_csv(body.path)


@app.delete("/workspace/csv", response_model=WorkspaceConfig, tags=["workspace"])
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


@app.post("/workspace/run", response_model=Job, tags=["workspace"])
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
