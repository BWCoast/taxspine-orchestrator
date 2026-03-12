"""Shared test fixtures and helpers."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def start_and_wait(client: TestClient, job_id: str, *, timeout: float = 5.0) -> dict:
    """POST /jobs/{job_id}/start (202) then poll GET until terminal status.

    Returns the final job dict (status = completed | failed).
    Raises TimeoutError if the job does not reach a terminal state within
    *timeout* seconds.
    """
    resp = client.post(f"/jobs/{job_id}/start")
    assert resp.status_code in (202, 200), f"start returned {resp.status_code}: {resp.text}"

    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        if time.monotonic() > deadline:
            raise TimeoutError(f"Job {job_id} did not complete within {timeout}s; status={job['status']}")
        time.sleep(0.05)
