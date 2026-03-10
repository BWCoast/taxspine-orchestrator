"""In-memory job store (to be replaced with real persistence later)."""

from __future__ import annotations

from datetime import datetime, timezone
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
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Job]:
        """Return stored jobs, filtered, sorted newest-first, then paged.

        Filtering (all optional, combined with AND):
        - *status* — exact match on job status.
        - *country* — exact match on input country.
        - *query* — case-insensitive substring match against ``case_name``.

        Results are sorted by ``created_at`` descending (newest first),
        then sliced by *offset* and *limit*.
        """
        jobs: list[Job] = list(self._jobs.values())

        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        if country is not None:
            jobs = [j for j in jobs if j.input.country == country]
        if query is not None:
            query_lower = query.lower()
            jobs = [
                j for j in jobs
                if j.input.case_name is not None
                and query_lower in j.input.case_name.lower()
            ]

        # Sort by created_at descending (newest first).
        jobs.sort(key=lambda j: j.created_at, reverse=True)

        return jobs[offset : offset + limit]

    def update_status(self, job_id: str, status: JobStatus) -> Job | None:
        """Set a new status on an existing job.  Returns the updated job."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        now = datetime.now(timezone.utc)
        updated = job.model_copy(update={"status": status, "updated_at": now})
        self._jobs[job_id] = updated
        return updated

    def update_job(self, job_id: str, **fields: Any) -> Job | None:
        """Update arbitrary top-level fields on a job.

        Accepts keyword arguments matching Job field names, e.g.
        ``store.update_job(id, status=JobStatus.COMPLETED, output=new_output)``.
        Automatically refreshes ``updated_at`` to the current UTC time.
        Returns the updated job, or ``None`` if not found.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        fields.setdefault("updated_at", datetime.now(timezone.utc))
        updated = job.model_copy(update=fields)
        self._jobs[job_id] = updated
        return updated
