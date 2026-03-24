"""Shared test fixtures and helpers."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_job_state():
    """Session-wide autouse fixture: clear the job store and ``_background_tasks``
    before **and** after every test.

    Running both before (setup) and after (teardown via yield) prevents two
    failure modes:

    * **Setup clear**: ensures each test starts with an empty store regardless
      of what previous tests left behind.
    * **Teardown clear**: prevents lingering background tasks — particularly
      ``asyncio.to_thread`` executor threads that outlive their test — from
      writing stale rows into the store while a subsequent test is running.

    This fixture intentionally uses ``_background_tasks.clear()`` rather than
    task cancellation because the set only holds strong references; dropping
    them allows the GC/executor to drain naturally without blocking the test
    runner.  Tests that need deterministic task completion should mock
    ``start_job_execution`` directly.
    """
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()
    _m._background_tasks.clear()
    yield
    _m._job_store.clear()
    _m._background_tasks.clear()


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
