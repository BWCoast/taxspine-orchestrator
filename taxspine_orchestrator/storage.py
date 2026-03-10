"""In-memory job store (to be replaced with real persistence later)."""

from __future__ import annotations

from typing import Any, Dict, List

from .models import Country, Job, JobStatus


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

    def list(
        self,
        *,
        status: JobStatus | None = None,
        country: Country | None = None,
    ) -> List[Job]:
        """Return stored jobs, optionally filtered by *status* and/or *country*."""
        jobs = self._jobs.values()
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        if country is not None:
            jobs = [j for j in jobs if j.input.country == country]
        return list(jobs)

    def update_status(self, job_id: str, status: JobStatus) -> Job | None:
        """Set a new status on an existing job.  Returns the updated job."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(update={"status": status})
        self._jobs[job_id] = updated
        return updated

    def update_job(self, job_id: str, **fields: Any) -> Job | None:
        """Update arbitrary top-level fields on a job.

        Accepts keyword arguments matching Job field names, e.g.
        ``store.update_job(id, status=JobStatus.COMPLETED, output=new_output)``.
        Returns the updated job, or ``None`` if not found.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(update=fields)
        self._jobs[job_id] = updated
        return updated
