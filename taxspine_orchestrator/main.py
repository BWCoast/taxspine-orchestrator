"""FastAPI application — HTTP entry point for the orchestrator."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .config import settings
from .models import Job, JobInput
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
def list_jobs() -> list[Job]:
    """List all known jobs."""
    return _job_service.list_jobs()


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
