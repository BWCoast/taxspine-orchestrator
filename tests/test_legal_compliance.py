"""Tests for LC-01 through LC-05 legal-compliance fixes.

LC-01 — DELETE /workspace purge endpoint (data retention / right to erasure)
LC-02 — GET /workspace data-notice documentation (encryption requirement)
LC-03 — DELETE /jobs/{id} deletes associated output files (storage limitation)
LC-04 — POST /jobs/{id}/redact nulls XRPL addresses in job record (field-level erasure)
LC-05 — XRPL addresses redacted from execution logs (log data minimisation)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import Country, JobInput, JobOutput, JobStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


_NORWAY_JOB = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}


def _create_job(client: TestClient, payload: dict | None = None) -> dict:
    resp = client.post("/jobs", json=payload or _NORWAY_JOB)
    assert resp.status_code == 201
    return resp.json()


def _force_status(job_id: str, status: JobStatus, output: JobOutput | None = None) -> None:
    from taxspine_orchestrator import main as _m
    fields: dict = {"status": status}
    if output:
        fields["output"] = output
    _m._job_store.update_job(job_id, **fields)


# ── LC-01: DELETE /workspace purge endpoint ───────────────────────────────────


class TestWorkspacePurge:
    """LC-01: DELETE /workspace must clear accounts and CSV registrations."""

    @pytest.fixture(autouse=True)
    def _reset_workspace(self):
        from taxspine_orchestrator import main as _m
        _m._workspace_store.clear()
        yield
        _m._workspace_store.clear()

    def test_purge_endpoint_exists(self, client: TestClient) -> None:
        resp = client.delete("/workspace")
        assert resp.status_code == 200

    def test_purge_clears_accounts(self, client: TestClient) -> None:
        client.post("/workspace/accounts", json={"account": "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"})
        resp = client.delete("/workspace")
        assert resp.status_code == 200
        assert resp.json()["xrpl_accounts"] == []

    def test_purge_clears_csv_registrations(self, client: TestClient, tmp_path: Path) -> None:
        csv = tmp_path / "events.csv"
        csv.write_text("header\n", encoding="utf-8")
        from taxspine_orchestrator import main as _m
        _m._workspace_store.add_csv(
            __import__("taxspine_orchestrator.models", fromlist=["CsvFileSpec"]).CsvFileSpec(
                path=str(csv), source_type="generic_events"
            )
        )
        resp = client.delete("/workspace")
        assert resp.json()["csv_files"] == []

    def test_purge_with_delete_files_removes_csv_from_disk(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        csv = tmp_path / "events.csv"
        csv.write_text("header\n", encoding="utf-8")
        from taxspine_orchestrator import main as _m
        from taxspine_orchestrator.models import CsvFileSpec
        _m._workspace_store.add_csv(CsvFileSpec(path=str(csv), source_type="generic_events"))

        resp = client.delete("/workspace", params={"delete_files": "true"})
        assert resp.status_code == 200
        assert not csv.exists(), "CSV file must be deleted from disk when delete_files=true"

    def test_purge_without_delete_files_leaves_csv_on_disk(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        csv = tmp_path / "events.csv"
        csv.write_text("header\n", encoding="utf-8")
        from taxspine_orchestrator import main as _m
        from taxspine_orchestrator.models import CsvFileSpec
        _m._workspace_store.add_csv(CsvFileSpec(path=str(csv), source_type="generic_events"))

        resp = client.delete("/workspace", params={"delete_files": "false"})
        assert resp.status_code == 200
        assert csv.exists(), "CSV file must NOT be deleted when delete_files=false (default)"

    def test_purge_is_idempotent(self, client: TestClient) -> None:
        client.delete("/workspace")
        resp = client.delete("/workspace")
        assert resp.status_code == 200
        assert resp.json()["xrpl_accounts"] == []

    def test_purge_requires_auth_when_key_set(self) -> None:
        with patch.dict(
            __import__("os").environ, {"ORCHESTRATOR_KEY": "secret123"}
        ):
            from taxspine_orchestrator.config import settings as _s
            original = _s.ORCHESTRATOR_KEY
            _s.ORCHESTRATOR_KEY = "secret123"
            try:
                c = TestClient(app)
                resp = c.delete("/workspace")
                assert resp.status_code == 401
            finally:
                _s.ORCHESTRATOR_KEY = original


# ── LC-02: GET /workspace data notice ─────────────────────────────────────────


class TestWorkspaceDataNotice:
    """LC-02: GET /workspace description must document encryption requirement."""

    def test_openapi_workspace_get_mentions_encryption(self, client: TestClient) -> None:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        # Find the GET /workspace operation description.
        get_op = spec["paths"]["/workspace"]["get"]
        description = (get_op.get("description") or "").lower()
        assert "encrypt" in description, (
            "GET /workspace must document that workspace.json must be encrypted at OS level"
        )

    def test_openapi_workspace_delete_mentions_retention(self, client: TestClient) -> None:
        resp = client.get("/openapi.json")
        spec = resp.json()
        delete_op = spec["paths"]["/workspace"]["delete"]
        description = (delete_op.get("description") or "").lower()
        assert "retention" in description or "erasure" in description, (
            "DELETE /workspace must document the data retention / erasure policy"
        )


# ── LC-03: DELETE /jobs/{id} deletes output files ─────────────────────────────


class TestDeleteJobFiles:
    """LC-03: Deleting a job should also delete its associated files from disk."""

    def test_delete_job_removes_output_files_by_default(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        job = _create_job(client)
        job_id = job["id"]

        # Inject fake output paths that exist on disk.
        report = tmp_path / "report.html"
        log = tmp_path / "execution.log"
        report.write_text("<html/>", encoding="utf-8")
        log.write_text("log\n", encoding="utf-8")

        _force_status(
            job_id, JobStatus.COMPLETED,
            output=JobOutput(
                report_html_path=str(report),
                log_path=str(log),
            ),
        )

        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert not report.exists(), "Report HTML must be deleted when delete_files=true"
        assert not log.exists(), "Execution log must be deleted when delete_files=true"

    def test_delete_job_delete_files_false_leaves_files(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        job = _create_job(client)
        job_id = job["id"]

        report = tmp_path / "report.html"
        report.write_text("<html/>", encoding="utf-8")
        _force_status(job_id, JobStatus.COMPLETED, output=JobOutput(report_html_path=str(report)))

        resp = client.delete(f"/jobs/{job_id}", params={"delete_files": "false"})
        assert resp.status_code == 200
        assert report.exists(), "Files must NOT be deleted when delete_files=false"

    def test_delete_job_response_includes_files_removed_count(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        job = _create_job(client)
        job_id = job["id"]
        log = tmp_path / "execution.log"
        log.write_text("log\n", encoding="utf-8")
        _force_status(job_id, JobStatus.COMPLETED, output=JobOutput(log_path=str(log)))

        resp = client.delete(f"/jobs/{job_id}")
        body = resp.json()
        assert "files_removed" in body
        assert body["files_removed"] >= 1

    def test_delete_job_files_removed_zero_when_no_files(
        self, client: TestClient
    ) -> None:
        job = _create_job(client)
        job_id = job["id"]
        # Job has no output files yet.
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.json()["files_removed"] == 0

    def test_delete_job_removes_input_csv_from_disk(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        csv = tmp_path / "events.csv"
        csv.write_text("header\n", encoding="utf-8")
        payload = {
            "csv_files": [{"path": str(csv), "source_type": "generic_events"}],
            "tax_year": 2025,
            "country": "norway",
        }
        job = _create_job(client, payload)
        _force_status(job["id"], JobStatus.COMPLETED)

        client.delete(f"/jobs/{job['id']}")
        assert not csv.exists(), "Input CSV must be deleted along with job record"

    def test_delete_job_handles_missing_files_gracefully(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        job = _create_job(client)
        job_id = job["id"]
        # Point to a non-existent file — deletion must not raise.
        _force_status(
            job_id, JobStatus.COMPLETED,
            output=JobOutput(log_path=str(tmp_path / "nonexistent.log")),
        )
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["files_removed"] == 0


# ── LC-04: POST /jobs/{id}/redact field-level erasure ─────────────────────────


class TestJobRedact:
    """LC-04: POST /jobs/{id}/redact must null out xrpl_accounts in the stored record."""

    def test_redact_completed_job_clears_accounts(self, client: TestClient) -> None:
        job = _create_job(client)
        job_id = job["id"]
        _force_status(job_id, JobStatus.COMPLETED)

        resp = client.post(f"/jobs/{job_id}/redact")
        assert resp.status_code == 200
        body = resp.json()
        assert body["input"]["xrpl_accounts"] == [], (
            "xrpl_accounts must be cleared after redaction"
        )

    def test_redact_failed_job_is_allowed(self, client: TestClient) -> None:
        job = _create_job(client)
        _force_status(job["id"], JobStatus.FAILED)

        resp = client.post(f"/jobs/{job['id']}/redact")
        assert resp.status_code == 200

    def test_redact_pending_job_returns_400(self, client: TestClient) -> None:
        job = _create_job(client)
        # Job is still PENDING.
        resp = client.post(f"/jobs/{job['id']}/redact")
        assert resp.status_code == 400

    def test_redact_running_job_returns_400(self, client: TestClient) -> None:
        job = _create_job(client)
        _force_status(job["id"], JobStatus.RUNNING)
        resp = client.post(f"/jobs/{job['id']}/redact")
        assert resp.status_code == 400

    def test_redact_nonexistent_job_returns_404(self, client: TestClient) -> None:
        resp = client.post("/jobs/does-not-exist/redact")
        assert resp.status_code == 404

    def test_redact_is_idempotent(self, client: TestClient) -> None:
        job = _create_job(client)
        _force_status(job["id"], JobStatus.COMPLETED)
        client.post(f"/jobs/{job['id']}/redact")
        resp = client.post(f"/jobs/{job['id']}/redact")
        assert resp.status_code == 200
        assert resp.json()["input"]["xrpl_accounts"] == []

    def test_redact_persists_to_store(self, client: TestClient) -> None:
        job = _create_job(client)
        job_id = job["id"]
        _force_status(job_id, JobStatus.COMPLETED)

        client.post(f"/jobs/{job_id}/redact")
        # Read back via GET.
        get_resp = client.get(f"/jobs/{job_id}")
        assert get_resp.json()["input"]["xrpl_accounts"] == []


# ── LC-05: XRPL address redaction in logs ─────────────────────────────────────


class TestLogAddressRedaction:
    """LC-05: Execution logs must not contain literal XRPL addresses."""

    def test_redact_function_replaces_address(self) -> None:
        from taxspine_orchestrator.services import _redact_xrpl_addresses
        text = "running: taxspine-xrpl-nor --account rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh --year 2025"
        result = _redact_xrpl_addresses(text)
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" not in result
        assert "[XRPL-ADDRESS]" in result

    def test_redact_function_replaces_multiple_addresses(self) -> None:
        from taxspine_orchestrator.services import _redact_xrpl_addresses
        text = "addr1=rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh addr2=rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe"
        result = _redact_xrpl_addresses(text)
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" not in result
        assert "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe" not in result
        assert result.count("[XRPL-ADDRESS]") == 2

    def test_redact_function_leaves_other_text_unchanged(self) -> None:
        from taxspine_orchestrator.services import _redact_xrpl_addresses
        text = "taxspine-xrpl-nor --year 2025 --html-output report.html"
        assert _redact_xrpl_addresses(text) == text

    def test_redact_function_does_not_alter_partial_addresses(self) -> None:
        from taxspine_orchestrator.services import _redact_xrpl_addresses
        # "r" followed by fewer than 24 chars should not be redacted.
        text = "error: rshort is not valid"
        result = _redact_xrpl_addresses(text)
        assert "rshort" in result

    def test_write_log_redacts_addresses(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.services import JobService
        lines = [
            "$ taxspine-xrpl-nor --account rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh --year 2025",
            "  rc=0",
        ]
        log_path = JobService._write_log(tmp_path, lines)
        content = log_path.read_text(encoding="utf-8")
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" not in content
        assert "[XRPL-ADDRESS]" in content

    def test_dry_run_log_does_not_contain_literal_address(
        self, client: TestClient
    ) -> None:
        payload = {**_NORWAY_JOB, "dry_run": True}
        job = _create_job(client, payload)
        from tests.conftest import start_and_wait
        result = start_and_wait(client, job["id"])
        log_text = Path(result["output"]["log_path"]).read_text(encoding="utf-8")
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" not in log_text
        assert "[XRPL-ADDRESS]" in log_text
