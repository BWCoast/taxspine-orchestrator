"""Tests for timestamps, paging/sorting, and dry_run behaviour."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import JobOutput, JobStatus
from tests.conftest import start_and_wait


# ── Helpers ──────────────────────────────────────────────────────────────────


_SAMPLE_INPUT = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}


def _ok_subprocess(*_args, **_kwargs):
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


# ── 3a: Timestamps ──────────────────────────────────────────────────────────


class TestTimestamps:
    """Verify created_at / updated_at on Job responses."""

    def test_create_sets_timestamps(self, client: TestClient) -> None:
        before = datetime.now(timezone.utc)
        resp = client.post("/jobs", json=_SAMPLE_INPUT)
        after = datetime.now(timezone.utc)

        body = resp.json()
        created = datetime.fromisoformat(body["created_at"])
        updated = datetime.fromisoformat(body["updated_at"])

        assert created == updated, "created_at == updated_at on creation"
        assert before <= created <= after

    def test_get_returns_timestamps(self, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)
        job_id = resp.json()["id"]

        get_resp = client.get(f"/jobs/{job_id}")
        body = get_resp.json()

        # Both must parse as valid ISO-8601 datetimes.
        datetime.fromisoformat(body["created_at"])
        datetime.fromisoformat(body["updated_at"])

    @patch(
        "taxspine_orchestrator.services.subprocess.run",
        side_effect=_ok_subprocess,
    )
    def test_start_updates_updated_at(self, mock_run, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)
        body = resp.json()
        created_at = body["created_at"]
        original_updated = body["updated_at"]

        # Small sleep to ensure the timestamps diverge.
        time.sleep(0.01)

        started_body = start_and_wait(client, body["id"])

        assert started_body["created_at"] == created_at, "created_at never changes"
        assert started_body["updated_at"] >= original_updated

    def test_list_returns_timestamps(self, client: TestClient) -> None:
        client.post("/jobs", json=_SAMPLE_INPUT)

        resp = client.get("/jobs")
        body = resp.json()
        assert len(body) == 1
        datetime.fromisoformat(body[0]["created_at"])
        datetime.fromisoformat(body[0]["updated_at"])


# ── 3b: Paging and sorting ─────────────────────────────────────────────────


class TestPagingAndSorting:
    """Verify GET /jobs pagination and newest-first sorting."""

    def _create_n_jobs(self, client: TestClient, n: int = 5) -> list[str]:
        """Create *n* jobs sequentially.  Returns IDs in creation order."""
        ids = []
        for i in range(n):
            resp = client.post("/jobs", json=_SAMPLE_INPUT)
            ids.append(resp.json()["id"])
            time.sleep(0.002)  # ensure distinct created_at
        return ids

    def test_default_order_is_newest_first(self, client: TestClient) -> None:
        ids = self._create_n_jobs(client, 4)

        resp = client.get("/jobs")
        body = resp.json()
        returned_ids = [j["id"] for j in body]

        # Newest (last created) should come first.
        assert returned_ids == list(reversed(ids))

    def test_limit(self, client: TestClient) -> None:
        ids = self._create_n_jobs(client, 5)

        resp = client.get("/jobs", params={"limit": 2})
        body = resp.json()
        assert len(body) == 2
        # The two newest.
        assert body[0]["id"] == ids[4]
        assert body[1]["id"] == ids[3]

    def test_offset(self, client: TestClient) -> None:
        ids = self._create_n_jobs(client, 5)

        resp = client.get("/jobs", params={"limit": 2, "offset": 2})
        body = resp.json()
        assert len(body) == 2
        # Skipped the 2 newest → ids[2] and ids[1] (descending).
        assert body[0]["id"] == ids[2]
        assert body[1]["id"] == ids[1]

    def test_offset_beyond_total_returns_empty(self, client: TestClient) -> None:
        self._create_n_jobs(client, 3)

        resp = client.get("/jobs", params={"offset": 100})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_limit_with_filter(self, client: TestClient) -> None:
        """Filters are applied before paging."""
        from taxspine_orchestrator import main as _m

        ids = self._create_n_jobs(client, 4)
        # Mark the two oldest as COMPLETED.
        _m._job_store.update_job(ids[0], status=JobStatus.COMPLETED)
        _m._job_store.update_job(ids[1], status=JobStatus.COMPLETED)
        # ids[2], ids[3] stay PENDING.

        resp = client.get("/jobs", params={"status": "pending", "limit": 1})
        body = resp.json()
        assert len(body) == 1
        # Newest pending job.
        assert body[0]["id"] == ids[3]

    def test_negative_offset_returns_422(self, client: TestClient) -> None:
        resp = client.get("/jobs", params={"offset": -1})
        assert resp.status_code == 422

    def test_limit_zero_returns_422(self, client: TestClient) -> None:
        resp = client.get("/jobs", params={"limit": 0})
        assert resp.status_code == 422

    def test_limit_exceeds_max_returns_422(self, client: TestClient) -> None:
        resp = client.get("/jobs", params={"limit": 201})
        assert resp.status_code == 422


# ── 3c: dry_run behaviour ──────────────────────────────────────────────────


class TestDryRun:
    """Verify dry_run skips subprocesses and writes a preview log."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_xrpl_completes_without_subprocess(
        self, mock_run, client: TestClient,
    ) -> None:
        payload = {**_SAMPLE_INPUT, "dry_run": True}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert body["output"]["log_path"] is not None
        assert body["output"]["gains_csv_path"] is None
        assert body["output"]["wealth_csv_path"] is None
        assert body["output"]["summary_json_path"] is None
        assert body["output"]["error_message"] is None
        assert mock_run.call_count == 0

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_csv_only_completes_without_subprocess(
        self, mock_run, client: TestClient, tmp_path: Path,
    ) -> None:
        csv_file = tmp_path / "events.csv"
        csv_file.write_text("h\nr\n")
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "uk",
            "csv_files": [str(csv_file)],
            "dry_run": True,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert body["output"]["log_path"] is not None
        assert mock_run.call_count == 0

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_log_contains_would_run_commands(
        self, mock_run, client: TestClient,
    ) -> None:
        payload = {**_SAMPLE_INPUT, "dry_run": True}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)
        log_path = body["output"]["log_path"]
        log_text = Path(log_path).read_text()

        assert "DRY RUN" in log_text
        assert "[would run]" in log_text
        assert "taxspine-xrpl-nor" in log_text
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" in log_text

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_no_inputs_still_fails(
        self, mock_run, client: TestClient,
    ) -> None:
        """dry_run does NOT override the no-inputs guard."""
        payload = {
            "xrpl_accounts": [],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [],
            "dry_run": True,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "no inputs" in body["output"]["error_message"].lower()
        assert mock_run.call_count == 0

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_false_still_calls_subprocess(
        self, mock_run, client: TestClient,
    ) -> None:
        """Normal job (dry_run=false) still triggers subprocess calls."""
        mock_run.side_effect = [_ok_subprocess()]
        payload = {**_SAMPLE_INPUT, "dry_run": False}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert mock_run.call_count == 1
        assert body["output"]["log_path"] is not None

    def test_dry_run_defaults_to_false(self, client: TestClient) -> None:
        resp = client.post("/jobs", json=_SAMPLE_INPUT)
        assert resp.json()["input"]["dry_run"] is False

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_combined_xrpl_csv_logs_consolidated_command(
        self, mock_run, client: TestClient, tmp_path: Path,
    ) -> None:
        """Mixed workspace dry-run logs a single consolidated xrpl-nor command.

        The old behaviour logged both a taxspine-xrpl-nor command AND a
        taxspine-nor-report command (one per source).  The new behaviour logs
        a single taxspine-xrpl-nor command that includes the CSV via
        --generic-events-csv so both sources share a unified FIFO lot pool.
        """
        csv_file = tmp_path / "events.csv"
        csv_file.write_text("h\nr\n")
        payload = {
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [str(csv_file)],
            "dry_run": True,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)
        log_path = body["output"]["log_path"]
        log_text = Path(log_path).read_text()

        # Single consolidated command.
        assert "taxspine-xrpl-nor" in log_text
        assert "--generic-events-csv" in log_text
        assert str(csv_file) in log_text

        # Old nor-report call must NOT appear.
        assert "taxspine-nor-report" not in log_text
        assert "--input" not in log_text

        assert mock_run.call_count == 0
