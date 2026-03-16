"""Tests for POST /workspace/run.

Covers:
- 400 when workspace has no accounts or CSVs.
- pipeline_mode default is per_file when not supplied.
- pipeline_mode=nor_multi is forwarded to the created job.
- pipeline_mode=per_file is forwarded to the created job.
- case_name is forwarded; missing case_name auto-labels from country + year.
- country is forwarded.
- dry_run is forwarded.
- valuation_mode is forwarded.
- WorkspaceRunRequest rejects unknown pipeline_mode values (422).
- Job returned has status completed (synchronous execution path).
- Job returned has correct input.xrpl_accounts from workspace.
- Job returned has correct input.csv_files from workspace.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import Country, PipelineMode, ValuationMode


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ok():
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


_XRPL_ACCOUNT = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"

_BASE_RUN = {"tax_year": 2025, "country": "norway"}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_stores():
    """Clear both job store and workspace before/after every test."""
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()
    _m._workspace_store.clear()
    yield
    _m._job_store.clear()
    _m._workspace_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def client_with_account(client: TestClient) -> TestClient:
    """Client whose workspace already has one XRPL account."""
    client.post("/workspace/accounts", json={"account": _XRPL_ACCOUNT})
    return client


@pytest.fixture()
def client_with_csv(client: TestClient) -> tuple[TestClient, str]:
    """Client whose workspace has one CSV file registered directly (bypasses upload-dir restriction).

    Returns (client, csv_path).  The file is created inside UPLOAD_DIR so the
    services layer can find it on disk; it is registered via the workspace store
    directly rather than through the HTTP endpoint to avoid path-containment checks
    (those are tested in test_csv_uploads.py).
    """
    from taxspine_orchestrator import main as _m
    from taxspine_orchestrator.config import settings
    from taxspine_orchestrator.models import CsvFileSpec, CsvSourceType

    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = settings.UPLOAD_DIR / "_test_workspace_run_events.csv"
    csv_path.write_text("header\nrow\n", encoding="utf-8")
    _m._workspace_store.add_csv(CsvFileSpec(path=str(csv_path), source_type=CsvSourceType.GENERIC_EVENTS))
    yield client, str(csv_path)
    csv_path.unlink(missing_ok=True)


# ── TestWorkspaceRunPreconditions ─────────────────────────────────────────────


class TestWorkspaceRunPreconditions:
    def test_empty_workspace_returns_400(self, client: TestClient) -> None:
        resp = client.post("/workspace/run", json=_BASE_RUN)
        assert resp.status_code == 400

    def test_empty_workspace_detail_mentions_no_inputs(self, client: TestClient) -> None:
        resp = client.post("/workspace/run", json=_BASE_RUN)
        assert "accounts" in resp.json()["detail"].lower() or "csv" in resp.json()["detail"].lower()

    def test_unknown_pipeline_mode_returns_422(self, client_with_account: TestClient) -> None:
        resp = client_with_account.post(
            "/workspace/run", json={**_BASE_RUN, "pipeline_mode": "invalid_mode"}
        )
        assert resp.status_code == 422


# ── TestWorkspaceRunPipelineMode ──────────────────────────────────────────────


class TestWorkspaceRunPipelineMode:
    """pipeline_mode must survive the /workspace/run → JobInput round-trip."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_default_pipeline_mode_is_per_file(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post("/workspace/run", json=_BASE_RUN)
        assert resp.status_code == 200
        assert resp.json()["input"]["pipeline_mode"] == "per_file"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_forwarded_to_job(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post(
            "/workspace/run", json={**_BASE_RUN, "pipeline_mode": "nor_multi"}
        )
        assert resp.status_code == 200
        assert resp.json()["input"]["pipeline_mode"] == "nor_multi"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_per_file_forwarded_to_job(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post(
            "/workspace/run", json={**_BASE_RUN, "pipeline_mode": "per_file"}
        )
        assert resp.status_code == 200
        assert resp.json()["input"]["pipeline_mode"] == "per_file"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_nor_multi_single_subprocess_call(
        self, mock_run: MagicMock, client_with_csv: tuple[TestClient, str]
    ) -> None:
        """nor_multi produces exactly one subprocess call (vs one-per-file for per_file)."""
        mock_run.return_value = _make_ok()
        client, _ = client_with_csv
        resp = client.post("/workspace/run", json={**_BASE_RUN, "pipeline_mode": "nor_multi"})
        assert resp.status_code == 200
        assert mock_run.call_count == 1

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_per_file_single_file_single_call(
        self, mock_run: MagicMock, client_with_csv: tuple[TestClient, str]
    ) -> None:
        """per_file with one CSV also produces one subprocess call."""
        mock_run.return_value = _make_ok()
        client, _ = client_with_csv
        resp = client.post("/workspace/run", json={**_BASE_RUN, "pipeline_mode": "per_file"})
        assert resp.status_code == 200
        assert mock_run.call_count == 1


# ── TestWorkspaceRunFieldForwarding ───────────────────────────────────────────


class TestWorkspaceRunFieldForwarding:
    """All WorkspaceRunRequest fields must reach the created JobInput."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_case_name_forwarded(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post(
            "/workspace/run", json={**_BASE_RUN, "case_name": "My test run"}
        )
        assert resp.json()["input"]["case_name"] == "My test run"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_missing_case_name_auto_labels(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post("/workspace/run", json=_BASE_RUN)
        label = resp.json()["input"]["case_name"]
        assert "2025" in label
        assert "norway" in label.lower()

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_forwarded(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post(
            "/workspace/run", json={**_BASE_RUN, "dry_run": True}
        )
        assert resp.json()["input"]["dry_run"] is True

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_country_forwarded(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post(
            "/workspace/run", json={**_BASE_RUN, "country": "norway"}
        )
        assert resp.json()["input"]["country"] == "norway"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_valuation_mode_forwarded(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post(
            "/workspace/run", json={**_BASE_RUN, "valuation_mode": "dummy"}
        )
        assert resp.json()["input"]["valuation_mode"] == "dummy"


# ── TestWorkspaceRunExecution ─────────────────────────────────────────────────


class TestWorkspaceRunExecution:
    """Execution semantics of /workspace/run."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_returns_completed_job(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        """workspace/run executes synchronously and returns the final job."""
        mock_run.return_value = _make_ok()
        resp = client_with_account.post("/workspace/run", json=_BASE_RUN)
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_workspace_accounts_included_in_job(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post("/workspace/run", json=_BASE_RUN)
        assert _XRPL_ACCOUNT in resp.json()["input"]["xrpl_accounts"]

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_workspace_csv_included_in_job(
        self, mock_run: MagicMock, client_with_csv: tuple[TestClient, str]
    ) -> None:
        mock_run.return_value = _make_ok()
        client, csv_path = client_with_csv
        resp = client.post("/workspace/run", json=_BASE_RUN)
        assert resp.status_code == 200
        # The workspace should contain exactly the CSV we registered
        csv_files = resp.json()["input"]["csv_files"]
        assert len(csv_files) == 1
        assert csv_files[0]["path"] == csv_path

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_job_appears_in_job_list(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        resp = client_with_account.post("/workspace/run", json=_BASE_RUN)
        job_id = resp.json()["id"]
        ids = [j["id"] for j in client_with_account.get("/jobs").json()]
        assert job_id in ids

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_skips_subprocess(
        self, mock_run: MagicMock, client_with_account: TestClient
    ) -> None:
        mock_run.return_value = _make_ok()
        client_with_account.post("/workspace/run", json={**_BASE_RUN, "dry_run": True})
        mock_run.assert_not_called()
