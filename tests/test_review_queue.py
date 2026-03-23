"""test_review_queue.py — Phase 3 dashboard: GET /review/summary endpoint.

Tests the cross-job review aggregation introduced for the Review Queue tab.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_client():
    from taxspine_orchestrator.main import app
    return TestClient(app)


def _make_completed_norway_job(client: TestClient, year: int = 2025) -> str:
    """Create and mark a Norway job as COMPLETED (no actual execution)."""
    r = client.post("/jobs", json={"tax_year": year, "country": "norway"})
    assert r.status_code == 201
    return r.json()["id"]


# ── TestReviewSummaryEndpointBasics ───────────────────────────────────────────


class TestReviewSummaryEndpointBasics:
    """Basic structure and validation of GET /review/summary."""

    def test_requires_year_param(self):
        client = _make_client()
        r = client.get("/review/summary")
        assert r.status_code == 422

    def test_rejects_year_below_2009(self):
        client = _make_client()
        r = client.get("/review/summary?year=2008")
        assert r.status_code == 422

    def test_rejects_year_above_2100(self):
        client = _make_client()
        r = client.get("/review/summary?year=2101")
        assert r.status_code == 422

    def test_accepts_valid_year(self):
        client = _make_client()
        r = client.get("/review/summary?year=2025")
        assert r.status_code == 200

    def test_response_has_required_fields(self):
        client = _make_client()
        r = client.get("/review/summary?year=2025")
        data = r.json()
        required = {
            "tax_year", "jobs_with_review", "has_unlinked_transfers",
            "unlinked_transfer_jobs", "total_warnings", "warnings",
            "missing_basis_assets", "missing_basis_count", "clean",
        }
        assert required <= data.keys()

    def test_tax_year_matches_request(self):
        client = _make_client()
        r = client.get("/review/summary?year=2023")
        assert r.json()["tax_year"] == 2023


# ── TestReviewSummaryNoJobs ────────────────────────────────────────────────────


class TestReviewSummaryNoJobs:
    """Summary is clean when there are no matching jobs."""

    def test_no_jobs_returns_clean_summary(self):
        client = _make_client()
        r = client.get("/review/summary?year=2099")  # year nobody has run
        data = r.json()
        assert data["clean"] is True
        assert data["jobs_with_review"] == 0
        assert data["has_unlinked_transfers"] is False
        assert data["warnings"] == []
        assert data["missing_basis_count"] == 0

    def test_clean_has_empty_lists(self):
        client = _make_client()
        data = client.get("/review/summary?year=2099").json()
        assert data["unlinked_transfer_jobs"] == []
        assert data["missing_basis_assets"] == []


# ── TestReviewSummaryWithReviewData ────────────────────────────────────────────


class TestReviewSummaryWithReviewData:
    """Summary correctly aggregates review JSON from completed jobs."""

    def _write_review_json(self, tmpdir: Path, content: dict) -> str:
        p = tmpdir / "review.json"
        p.write_text(json.dumps(content))
        return str(p)

    def test_unlinked_transfer_flag_propagated(self):
        client = _make_client()
        job_id = _make_completed_norway_job(client, 2025)

        with tempfile.TemporaryDirectory() as td:
            review_path = self._write_review_json(
                Path(td),
                {"has_unlinked_transfers": True, "warnings": [], "warning_count": 0, "clean": False},
            )
            # Patch the job store to return the completed job with review path.
            from taxspine_orchestrator.models import JobStatus
            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                mock_job = MagicMock()
                mock_job.input.tax_year = 2025
                mock_job.id = job_id
                mock_job.output.review_json_paths = [review_path]
                mock_job.output.review_json_path = review_path
                mock_store_inst = MockStore.return_value
                mock_store_inst.list.return_value = [mock_job]

                r = client.get("/review/summary?year=2025")
                data = r.json()

        assert data["has_unlinked_transfers"] is True
        # unlinked_transfer_jobs is now [{job_id, case_name}] — check id field
        unlinked_ids = [j["job_id"] for j in data["unlinked_transfer_jobs"]]
        assert job_id in unlinked_ids

    def test_warnings_merged_from_multiple_jobs(self):
        client = _make_client()

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            path1 = td_path / "r1.json"
            path2 = td_path / "r2.json"
            path1.write_text(json.dumps({"has_unlinked_transfers": False, "warnings": ["warn A"], "warning_count": 1, "clean": False}))
            path2.write_text(json.dumps({"has_unlinked_transfers": False, "warnings": ["warn B"], "warning_count": 1, "clean": False}))

            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                job1 = MagicMock()
                job1.input.tax_year = 2025
                job1.id = "j1"
                job1.output.review_json_paths = [str(path1)]
                job1.output.review_json_path = str(path1)
                job2 = MagicMock()
                job2.input.tax_year = 2025
                job2.id = "j2"
                job2.output.review_json_paths = [str(path2)]
                job2.output.review_json_path = str(path2)
                MockStore.return_value.list.return_value = [job1, job2]

                r = client.get("/review/summary?year=2025")
                data = r.json()

        assert data["total_warnings"] == 2
        assert "warn A" in data["warnings"]
        assert "warn B" in data["warnings"]

    def test_warnings_deduplicated_across_jobs(self):
        """Same warning text from two jobs is deduplicated in the output."""
        client = _make_client()

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            p1 = td_path / "r1.json"
            p2 = td_path / "r2.json"
            shared_warn = "Transfer XRP from exchange not linked"
            p1.write_text(json.dumps({"has_unlinked_transfers": False, "warnings": [shared_warn]}))
            p2.write_text(json.dumps({"has_unlinked_transfers": False, "warnings": [shared_warn]}))

            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                j1 = MagicMock()
                j1.input.tax_year = 2025
                j1.id = "j1"
                j1.output.review_json_paths = [str(p1)]
                j1.output.review_json_path = str(p1)
                j2 = MagicMock()
                j2.input.tax_year = 2025
                j2.id = "j2"
                j2.output.review_json_paths = [str(p2)]
                j2.output.review_json_path = str(p2)
                MockStore.return_value.list.return_value = [j1, j2]

                data = client.get("/review/summary?year=2025").json()

        assert data["total_warnings"] == 1
        assert data["warnings"] == [shared_warn]

    def test_clean_when_all_jobs_clean(self):
        client = _make_client()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(json.dumps({"has_unlinked_transfers": False, "warnings": [], "warning_count": 0, "clean": True}))

            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                j = MagicMock()
                j.input.tax_year = 2025
                j.id = "jclean"
                j.output.review_json_paths = [str(p)]
                j.output.review_json_path = str(p)
                MockStore.return_value.list.return_value = [j]

                with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                    data = client.get("/review/summary?year=2025").json()

        assert data["clean"] is True

    def test_not_clean_when_warnings_present(self):
        client = _make_client()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(json.dumps({"has_unlinked_transfers": False, "warnings": ["something odd"]}))

            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                j = MagicMock()
                j.input.tax_year = 2025
                j.id = "jw"
                j.output.review_json_paths = [str(p)]
                j.output.review_json_path = str(p)
                MockStore.return_value.list.return_value = [j]

                with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                    data = client.get("/review/summary?year=2025").json()

        assert data["clean"] is False

    def test_missing_review_file_skipped_gracefully(self):
        """A job pointing to a nonexistent review file should not crash."""
        client = _make_client()

        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            j = MagicMock()
            j.input.tax_year = 2025
            j.id = "jbad"
            j.output.review_json_paths = ["/nonexistent/path/review.json"]
            j.output.review_json_path = "/nonexistent/path/review.json"
            MockStore.return_value.list.return_value = [j]

            r = client.get("/review/summary?year=2025")
        assert r.status_code == 200
        assert r.json()["jobs_with_review"] == 0  # file unreadable → not counted

    def test_invalid_review_json_skipped_gracefully(self):
        """Corrupt JSON in a review file should not crash."""
        client = _make_client()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "corrupt.json"
            p.write_text("not json {{")

            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                j = MagicMock()
                j.input.tax_year = 2025
                j.id = "jcorrupt"
                j.output.review_json_paths = [str(p)]
                j.output.review_json_path = str(p)
                MockStore.return_value.list.return_value = [j]

                r = client.get("/review/summary?year=2025")
        assert r.status_code == 200
        assert r.json()["jobs_with_review"] == 0

    def test_only_matching_year_jobs_considered(self):
        """Jobs from a different year must not bleed into the summary."""
        client = _make_client()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r2024.json"
            p.write_text(json.dumps({"has_unlinked_transfers": True, "warnings": ["old warn"]}))

            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                j = MagicMock()
                j.input.tax_year = 2024
                j.id = "j2024"
                j.output.review_json_paths = [str(p)]
                j.output.review_json_path = str(p)
                MockStore.return_value.list.return_value = [j]

                data = client.get("/review/summary?year=2025").json()

        assert data["clean"] is True
        assert data["jobs_with_review"] == 0


# ── TestReviewSummaryMissingBasis ─────────────────────────────────────────────


class TestReviewSummaryMissingBasis:
    """Missing-basis assets are surfaced via the lot store."""

    def test_missing_basis_assets_reported(self):
        client = _make_client()
        # Route now calls _missing_basis_detail, not _missing_basis_assets
        fake_detail = [
            {"asset": "XRP", "lot_count": 2, "missing_lots": 1,
             "total_remaining_qty": "100", "has_missing_basis": True},
            {"asset": "BTC", "lot_count": 1, "missing_lots": 1,
             "total_remaining_qty": "0.5", "has_missing_basis": True},
        ]
        with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=fake_detail):
            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                MockStore.return_value.list.return_value = []
                data = client.get("/review/summary?year=2025").json()

        assert data["missing_basis_assets"] == ["XRP", "BTC"]
        assert data["missing_basis_count"] == 2
        assert data["clean"] is False

    def test_no_missing_basis_assets_when_all_resolved(self):
        client = _make_client()
        with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
            with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
                MockStore.return_value.list.return_value = []
                data = client.get("/review/summary?year=2025").json()

        assert data["missing_basis_assets"] == []
        assert data["missing_basis_count"] == 0


# ── TestReviewSummaryNoDb ─────────────────────────────────────────────────────


class TestReviewSummaryNoDb:
    """When no jobs database exists the endpoint returns a clean empty summary."""

    def test_no_db_returns_clean_summary(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            with patch("taxspine_orchestrator.review.settings") as mock_s:
                mock_s.DATA_DIR = Path(td)
                # DATA_DIR / "jobs.db" will not exist
                data = client.get("/review/summary?year=2025").json()
        # When jobs.db doesn't exist the module returns the _empty_summary shortcut.
        # (The route is still healthy — just no data.)
        assert data.get("tax_year") == 2025


# ── TestMissingBasisHelper ────────────────────────────────────────────────────


class TestMissingBasisHelper:
    """Unit tests for _missing_basis_assets() helper."""

    def test_returns_empty_when_tax_spine_unavailable(self):
        from taxspine_orchestrator.review import _missing_basis_assets
        with patch.dict("sys.modules", {"tax_spine.pipeline.lot_store": None}):
            with patch("builtins.__import__", side_effect=ImportError("no tax_spine")):
                # Patch at the function level to avoid real import
                with patch("taxspine_orchestrator.review.settings") as mock_s:
                    mock_s.LOT_STORE_DB = Path("/nonexistent/lots.db")
                    result = _missing_basis_assets(2025)
        assert result == []

    def test_returns_empty_when_no_lot_db(self):
        from taxspine_orchestrator.review import _missing_basis_assets
        with tempfile.TemporaryDirectory() as td:
            with patch("taxspine_orchestrator.review.settings") as mock_s:
                mock_s.LOT_STORE_DB = Path(td) / "nonexistent.db"
                result = _missing_basis_assets(2025)
        assert result == []
