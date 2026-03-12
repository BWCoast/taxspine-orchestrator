"""Tests for CSV upload and attach-csv endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.config import settings
from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import JobStatus


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Clear the in-memory store between tests so they don't leak state."""
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_job(client: TestClient, **overrides: object) -> dict:
    """Helper — create a job and return the response body."""
    payload: dict = {
        "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
        "tax_year": 2025,
        "country": "norway",
        **overrides,
    }
    resp = client.post("/jobs", json=payload)
    assert resp.status_code == 200
    return resp.json()


def _force_status(job_id: str, status: JobStatus) -> None:
    """Directly mutate the in-memory store to set a job's status."""
    from taxspine_orchestrator import main as _m

    _m._job_store.update_job(job_id, status=status)


# ── POST /uploads/csv ───────────────────────────────────────────────────────


class TestUploadCsv:
    """Tests for ``POST /uploads/csv``."""

    def test_upload_success(self, client: TestClient) -> None:
        csv_content = b"event_id,timestamp,amount\n1,2025-01-01,100\n"
        files = {"file": ("test.csv", csv_content, "text/csv")}

        resp = client.post("/uploads/csv", files=files)

        assert resp.status_code == 200
        body = resp.json()
        assert "id" in body
        assert "path" in body
        assert body["original_filename"] == "test.csv"

        # File was actually written to disk with correct content.
        written = Path(body["path"])
        assert written.is_file()
        assert written.read_bytes() == csv_content

    def test_upload_returns_absolute_path(self, client: TestClient) -> None:
        files = {"file": ("data.csv", b"a,b\n1,2\n", "text/csv")}

        resp = client.post("/uploads/csv", files=files)
        body = resp.json()

        # Path should be absolute (under UPLOAD_DIR).
        assert Path(body["path"]).is_absolute()
        assert body["path"].startswith(str(settings.UPLOAD_DIR))

    def test_upload_unique_ids(self, client: TestClient) -> None:
        """Two uploads should produce distinct IDs and paths."""
        files = {"file": ("a.csv", b"col\n1\n", "text/csv")}

        r1 = client.post("/uploads/csv", files=files)
        r2 = client.post("/uploads/csv", files=files)

        assert r1.json()["id"] != r2.json()["id"]
        assert r1.json()["path"] != r2.json()["path"]

    def test_upload_octet_stream_accepted(self, client: TestClient) -> None:
        """application/octet-stream should be accepted (lenient check)."""
        files = {"file": ("data.csv", b"x\n1\n", "application/octet-stream")}

        resp = client.post("/uploads/csv", files=files)
        assert resp.status_code == 200

    def test_upload_vnd_ms_excel_accepted(self, client: TestClient) -> None:
        """application/vnd.ms-excel should be accepted."""
        files = {"file": ("data.csv", b"x\n1\n", "application/vnd.ms-excel")}

        resp = client.post("/uploads/csv", files=files)
        assert resp.status_code == 200

    def test_upload_image_rejected(self, client: TestClient) -> None:
        """Obvious non-CSV types like image/* should be rejected."""
        files = {"file": ("pic.png", b"\x89PNG\r\n", "image/png")}

        resp = client.post("/uploads/csv", files=files)
        assert resp.status_code == 400
        assert "image/png" in resp.json()["detail"]


# ── POST /jobs/{id}/attach-csv ───────────────────────────────────────────────


class TestAttachCsv:
    """Tests for ``POST /jobs/{job_id}/attach-csv``."""

    def test_attach_to_pending_job(self, client: TestClient) -> None:
        job = _create_job(client)

        # Upload a CSV first so we have a real file path.
        upload_resp = client.post(
            "/uploads/csv",
            files={"file": ("events.csv", b"id,ts\n1,2025\n", "text/csv")},
        )
        csv_path = upload_resp.json()["path"]

        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": [csv_path]},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert csv_path in body["input"]["csv_files"]

    def test_attach_does_not_duplicate(self, client: TestClient) -> None:
        """Attaching the same path twice should not produce duplicates."""
        job = _create_job(client)

        upload_resp = client.post(
            "/uploads/csv",
            files={"file": ("f.csv", b"a\n1\n", "text/csv")},
        )
        csv_path = upload_resp.json()["path"]

        # Attach the same path twice.
        client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": [csv_path]},
        )
        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": [csv_path]},
        )

        assert resp.status_code == 200
        csv_files = resp.json()["input"]["csv_files"]
        assert csv_files.count(csv_path) == 1

    def test_attach_preserves_existing_csv_files(self, client: TestClient) -> None:
        """csv_files supplied at creation time should be preserved."""
        # Upload two CSVs.
        r1 = client.post(
            "/uploads/csv",
            files={"file": ("a.csv", b"a\n1\n", "text/csv")},
        )
        r2 = client.post(
            "/uploads/csv",
            files={"file": ("b.csv", b"b\n2\n", "text/csv")},
        )
        path_a = r1.json()["path"]
        path_b = r2.json()["path"]

        # Create job with first CSV already included.
        job = _create_job(client, csv_files=[path_a])
        assert path_a in job["input"]["csv_files"]

        # Attach the second CSV.
        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": [path_b]},
        )
        assert resp.status_code == 200
        csv_files = resp.json()["input"]["csv_files"]
        assert path_a in csv_files
        assert path_b in csv_files

    def test_attach_updates_updated_at(self, client: TestClient) -> None:
        job = _create_job(client)
        original_updated_at = job["updated_at"]

        upload_resp = client.post(
            "/uploads/csv",
            files={"file": ("f.csv", b"a\n1\n", "text/csv")},
        )
        csv_path = upload_resp.json()["path"]

        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": [csv_path]},
        )
        assert resp.status_code == 200
        assert resp.json()["updated_at"] >= original_updated_at

    def test_attach_nonexistent_job_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs/does-not-exist/attach-csv",
            json={"csv_paths": ["/tmp/any.csv"]},
        )
        assert resp.status_code == 404

    def test_attach_to_completed_job_returns_400(self, client: TestClient) -> None:
        job = _create_job(client)
        _force_status(job["id"], JobStatus.COMPLETED)

        upload_resp = client.post(
            "/uploads/csv",
            files={"file": ("f.csv", b"a\n1\n", "text/csv")},
        )
        csv_path = upload_resp.json()["path"]

        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": [csv_path]},
        )
        assert resp.status_code == 400
        assert "non-pending" in resp.json()["detail"].lower()

    def test_attach_to_failed_job_returns_400(self, client: TestClient) -> None:
        job = _create_job(client)
        _force_status(job["id"], JobStatus.FAILED)

        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": ["/tmp/any.csv"]},
        )
        assert resp.status_code == 400
        assert "non-pending" in resp.json()["detail"].lower()

    def test_attach_to_running_job_returns_400(self, client: TestClient) -> None:
        job = _create_job(client)
        _force_status(job["id"], JobStatus.RUNNING)

        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": ["/tmp/any.csv"]},
        )
        assert resp.status_code == 400

    def test_attach_missing_file_returns_400(self, client: TestClient) -> None:
        job = _create_job(client)

        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": ["/nonexistent/path/data.csv"]},
        )
        assert resp.status_code == 400
        assert "/nonexistent/path/data.csv" in resp.json()["detail"]

    def test_attach_multiple_paths_partial_missing(self, client: TestClient) -> None:
        """If any path in the list is missing, all are rejected (no partial attach)."""
        job = _create_job(client)

        upload_resp = client.post(
            "/uploads/csv",
            files={"file": ("ok.csv", b"a\n1\n", "text/csv")},
        )
        good_path = upload_resp.json()["path"]
        bad_path = "/nonexistent/bad.csv"

        resp = client.post(
            f"/jobs/{job['id']}/attach-csv",
            json={"csv_paths": [good_path, bad_path]},
        )
        assert resp.status_code == 400
        assert bad_path in resp.json()["detail"]

        # Verify the good path was NOT attached (all-or-nothing).
        get_resp = client.get(f"/jobs/{job['id']}")
        assert good_path not in get_resp.json()["input"]["csv_files"]
