"""Job orchestration service layer.

This module owns all job-lifecycle logic.  External CLI calls (blockchain-reader,
taxspine-nor, taxspine-uk) will be wired in here once the pipelines are ready.
"""

from __future__ import annotations

import uuid

from .models import Job, JobInput, JobStatus
from .storage import InMemoryJobStore


class JobService:
    """Create, query, and (eventually) execute tax jobs."""

    def __init__(self, store: InMemoryJobStore) -> None:
        self.store = store

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create_job(self, job_input: JobInput) -> Job:
        """Create a new job in PENDING state."""
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, status=JobStatus.PENDING, input=job_input)
        return self.store.add(job)

    def get_job(self, job_id: str) -> Job | None:
        return self.store.get(job_id)

    def list_jobs(self) -> list[Job]:
        return self.store.list()

    # ── Execution (stub) ─────────────────────────────────────────────────

    def start_job_execution(self, job_id: str) -> Job | None:
        """Mark a job as RUNNING and kick off the pipeline.

        Currently a synchronous stub — flips status only.

        TODO (pipeline wiring):
        1. Determine country from ``job.input.country``.
        2. For each XRPL account, call blockchain-reader to produce
           an events JSON (``xrpl-reader --mode scenario ...``).
        3. Merge events across accounts.
        4. Invoke the country-specific taxspine CLI:
           - Norway → ``taxspine-nor`` (scenario-run)
           - UK     → ``taxspine-uk``  (TBD)
        5. Collect output artefacts into ``job.output``.
        6. Update status to COMPLETED (or FAILED on error).
        """
        job = self.store.get(job_id)
        if job is None:
            return None
        return self.store.update_status(job_id, JobStatus.RUNNING)
