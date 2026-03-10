"""FastAPI application — HTTP entry point for the orchestrator."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Query

from .config import settings
from .models import Country, Job, JobInput, JobStatus
from .services import JobService
from .storage import InMemoryJobStore

# ── Wiring ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Taxspine Orchestrator")

# Ensure working directories exist at import time so the first job doesn't
# have to create them mid-flight.
settings.ensure_dirs()

# TODO: Replace with proper dependency injection (e.g. FastAPI Depends)
#       once the store is DB-backed and needs request-scoped sessions.
_job_store = InMemoryJobStore()
_job_service = JobService(_job_store)


# ── Helpers ──────────────────────────────────────────────────────────────────

# Valid output-file "kinds" and the corresponding JobOutput field names.


class FileKind(str, Enum):
    """Supported output-file kinds for the file-download endpoint."""

    GAINS = "gains"
    WEALTH = "wealth"
    SUMMARY = "summary"
    LOG = "log"


_KIND_TO_FIELD: Dict[FileKind, str] = {
    FileKind.GAINS: "gains_csv_path",
    FileKind.WEALTH: "wealth_csv_path",
    FileKind.SUMMARY: "summary_json_path",
    FileKind.LOG: "log_path",
}


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Simple liveness probe."""
    return {"status": "ok"}


@app.post("/jobs", response_model=Job, tags=["jobs"])
def create_job(job_input: JobInput) -> Job:
    """Create a new tax job (PENDING)."""
    return _job_service.create_job(job_input)


@app.get("/jobs", response_model=list[Job], tags=["jobs"])
def list_jobs(
    status: Optional[JobStatus] = Query(default=None, description="Filter by job status"),
    country: Optional[Country] = Query(default=None, description="Filter by country"),
) -> list[Job]:
    """List jobs, optionally filtered by status and/or country."""
    return _job_service.list_jobs(status=status, country=country)


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


# ── File listing / download ──────────────────────────────────────────────────


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
def get_job_file(job_id: str, kind: FileKind) -> dict:
    """Resolve a single output file for *job_id*.

    Returns the file path and confirms the file exists on disk.
    In a future iteration this will return a ``FileResponse`` for direct
    download.

    Raises 404 if the job does not exist or the requested kind has no
    path recorded.
    """
    # TODO: Replace the JSON response with ``FileResponse`` once we decide
    #       on content-disposition headers and streaming behaviour.
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

    exists = Path(path_str).is_file()
    return {"kind": kind.value, "path": path_str, "exists_on_disk": exists}
