"""In-memory job store (to be replaced with real persistence later)."""

from __future__ import annotations

from typing import Dict, List

from .models import Job, JobStatus


class InMemoryJobStore:
    """Thread-unsafe, dict-backed store — good enough for scaffolding.

    TODO: Replace with a proper DB-backed store (e.g. SQLAlchemy / SQLModel)
          once we need persistence across restarts.
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    def add(self, job: Job) -> Job:
        """Insert a new job.  Returns the stored copy."""
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        """Retrieve a job by ID, or ``None`` if not found."""
        return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        """Return all stored jobs (insertion order)."""
        return list(self._jobs.values())

    def update_status(self, job_id: str, status: JobStatus) -> Job | None:
        """Set a new status on an existing job.  Returns the updated job."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(update={"status": status})
        self._jobs[job_id] = updated
        return updated
