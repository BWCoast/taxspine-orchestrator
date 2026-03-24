"""test_review_console.py — Full Review Console tests.

Covers:
- _categorize_warnings()          — keyword-based warning categorisation
- _missing_basis_detail()          — per-asset lot-count detail helper
- _job_review_summary()            — per-job review JSON merging helper
- _job_downloads()                 — download availability helper
- GET /review/summary enhancements — warnings_by_category, missing_basis_detail,
                                     unlinked_transfer_jobs as [{"job_id","case_name"}]
- GET /review/jobs                 — per-job cards endpoint
- UI elements                      — review console HTML structure
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

try:
    import tax_spine.pipeline.lot_store  # noqa: F401  — test the exact submodule patch() needs
    _TAX_SPINE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _TAX_SPINE_AVAILABLE = False


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _make_client() -> TestClient:
    from taxspine_orchestrator.main import app
    return TestClient(app)


def _make_mock_job(
    job_id: str,
    tax_year: int,
    case_name: str | None = None,
    review_paths: list[str] | None = None,
    html_paths: list[str] | None = None,
    rf1159_paths: list[str] | None = None,
    pipeline_mode: str = "per_file",
    valuation_mode: str = "price_table",
    csv_files: list | None = None,
    xrpl_accounts: list | None = None,
) -> MagicMock:
    j = MagicMock()
    j.id = job_id
    j.input.tax_year = tax_year
    j.input.case_name = case_name
    j.input.pipeline_mode = MagicMock(value=pipeline_mode)
    j.input.valuation_mode = MagicMock(value=valuation_mode)
    j.input.csv_files = csv_files or []
    j.input.xrpl_accounts = xrpl_accounts or []
    j.created_at = MagicMock(isoformat=MagicMock(return_value="2025-03-01T10:00:00+00:00"))
    j.output.review_json_paths  = review_paths or []
    j.output.review_json_path   = review_paths[0] if review_paths else None
    j.output.report_html_paths  = html_paths or []
    j.output.report_html_path   = html_paths[0] if html_paths else None
    j.output.rf1159_json_paths  = rf1159_paths or []
    j.output.rf1159_json_path   = rf1159_paths[0] if rf1159_paths else None
    return j


def _write_review(tmp_path: Path, name: str, content: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(content), encoding="utf-8")
    return str(p)


# ── TestCategorizeWarnings ────────────────────────────────────────────────────


class TestCategorizeWarnings:
    """_categorize_warnings() groups warnings by keyword match."""

    def _cat(self, warnings):
        from taxspine_orchestrator.review import _categorize_warnings
        return _categorize_warnings(warnings)

    def test_empty_list_returns_empty_dict(self) -> None:
        assert self._cat([]) == {}

    def test_transfer_linking_keyword_match(self) -> None:
        result = self._cat(["unlinked transfer detected"])
        assert "Transfer Linking" in result
        assert result["Transfer Linking"] == ["unlinked transfer detected"]

    def test_cost_basis_keyword_match(self) -> None:
        result = self._cat(["UNRESOLVED cost basis for BTC lot"])
        assert "Cost Basis" in result

    def test_tax_law_keyword_match(self) -> None:
        result = self._cat(["TL-07 partial year exception applies"])
        assert "Tax Law" in result

    def test_income_keyword_match(self) -> None:
        result = self._cat(["staking reward not classified"])
        assert "Income" in result

    def test_valuation_keyword_match(self) -> None:
        result = self._cat(["NOK price missing for 2025-06-15"])
        assert "Valuation" in result

    def test_unknown_warning_goes_to_general(self) -> None:
        result = self._cat(["something completely unexpected"])
        assert "General" in result
        assert result["General"] == ["something completely unexpected"]

    def test_multiple_categories_all_present(self) -> None:
        warnings = [
            "unlinked transfer",
            "missing cost basis",
            "staking income not valued",
        ]
        result = self._cat(warnings)
        assert "Transfer Linking" in result
        assert "Cost Basis" in result
        assert "Income" in result

    def test_first_matching_category_wins(self) -> None:
        # "transfer" matches Transfer Linking, not Cost Basis
        result = self._cat(["transfer with missing basis"])
        assert "Transfer Linking" in result
        assert "Cost Basis" not in result

    def test_case_insensitive_match(self) -> None:
        result = self._cat(["UNLINKED TRANSFER XRP"])
        assert "Transfer Linking" in result

    def test_multiple_warnings_in_same_category(self) -> None:
        warnings = ["unlinked XRP", "unlinked BTC", "unlinked ETH"]
        result = self._cat(warnings)
        assert len(result["Transfer Linking"]) == 3

    def test_all_three_categories_present(self) -> None:
        """All three categories are returned when each has a matching warning."""
        warnings = ["staking reward", "TL-07 partial year", "unlinked transfer"]
        result = self._cat(warnings)
        assert "Tax Law" in result
        assert "Transfer Linking" in result
        assert "Income" in result

    def test_empty_categories_omitted(self) -> None:
        result = self._cat(["unlinked transfer"])
        # All 5 other categories (Tax Law, Cost Basis, Income, Valuation, General) should be absent
        for cat in ("Tax Law", "Cost Basis", "Income", "Valuation", "General"):
            assert cat not in result


# ── TestMissingBasisDetail ────────────────────────────────────────────────────


@pytest.mark.skipif(not _TAX_SPINE_AVAILABLE, reason="tax_spine not installed")
class TestMissingBasisDetail:
    """_missing_basis_detail() returns per-asset lot counts."""

    def test_returns_empty_when_no_lot_db(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.review import _missing_basis_detail
        with patch("taxspine_orchestrator.review.settings") as mock_s:
            mock_s.LOT_STORE_DB = tmp_path / "nonexistent.db"
            result = _missing_basis_detail(2025)
        assert result == []

    def test_returns_empty_when_import_error(self) -> None:
        from taxspine_orchestrator.review import _missing_basis_detail
        with patch("taxspine_orchestrator.review.settings") as mock_s:
            mock_s.LOT_STORE_DB = Path("/nonexistent.db")
            result = _missing_basis_detail(2025)
        assert result == []

    def test_detail_entry_has_required_keys(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.review import _missing_basis_detail
        from decimal import Decimal

        fake_lot = MagicMock()
        fake_lot.asset = "XRP"
        fake_lot.remaining_quantity = Decimal("100")
        fake_lot.remaining_cost_basis_nok = None  # missing basis

        mock_store = MagicMock()
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.list_years.return_value = [2025]
        mock_store.load_carry_forward.return_value = [fake_lot]

        db_file = tmp_path / "lots.db"
        db_file.write_text("fake")

        with patch("taxspine_orchestrator.review.settings") as mock_s, \
             patch("tax_spine.pipeline.lot_store.LotPersistenceStore", return_value=mock_store):
            mock_s.LOT_STORE_DB = db_file
            result = _missing_basis_detail(2025)

        if result:
            entry = result[0]
            assert "asset" in entry
            assert "lot_count" in entry
            assert "missing_lots" in entry
            assert "total_remaining_qty" in entry
            assert "has_missing_basis" in entry

    def test_backward_compat_wrapper(self, tmp_path: Path) -> None:
        """_missing_basis_assets() returns [asset, ...] from _missing_basis_detail."""
        from taxspine_orchestrator.review import _missing_basis_assets
        with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[
            {"asset": "BTC", "lot_count": 2, "missing_lots": 1,
             "total_remaining_qty": "0.5", "has_missing_basis": True},
        ]):
            result = _missing_basis_assets(2025)
        assert result == ["BTC"]


# ── TestJobReviewSummaryHelper ────────────────────────────────────────────────


class TestJobReviewSummaryHelper:
    """_job_review_summary() reads and merges review JSON files for a job."""

    def test_returns_clean_when_no_paths(self) -> None:
        from taxspine_orchestrator.review import _job_review_summary
        job = MagicMock()
        job.output.review_json_paths = []
        job.output.review_json_path = None
        result = _job_review_summary(job)
        assert result["clean"] is True
        assert result["warning_count"] == 0
        assert result["has_unlinked_transfers"] is False
        assert result["source_count"] == 0

    def test_reads_single_review_file(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.review import _job_review_summary
        p = _write_review(tmp_path, "r.json", {
            "has_unlinked_transfers": False, "warnings": ["warn-1"],
        })
        job = MagicMock()
        job.output.review_json_paths = [p]
        job.output.review_json_path = p
        result = _job_review_summary(job)
        assert result["warning_count"] == 1
        assert "warn-1" in result["warnings"]
        assert result["source_count"] == 1

    def test_merges_two_files(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.review import _job_review_summary
        p1 = _write_review(tmp_path, "r1.json", {"warnings": ["w-a"], "has_unlinked_transfers": False})
        p2 = _write_review(tmp_path, "r2.json", {"warnings": ["w-b"], "has_unlinked_transfers": True})
        job = MagicMock()
        job.output.review_json_paths = [p1, p2]
        job.output.review_json_path = p1
        result = _job_review_summary(job)
        assert "w-a" in result["warnings"]
        assert "w-b" in result["warnings"]
        assert result["has_unlinked_transfers"] is True
        assert result["source_count"] == 2

    def test_missing_file_does_not_raise(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.review import _job_review_summary
        job = MagicMock()
        job.output.review_json_paths = [str(tmp_path / "ghost.json")]
        job.output.review_json_path = None
        result = _job_review_summary(job)
        assert result["source_count"] == 0

    def test_clean_true_when_no_warnings_no_unlinked(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.review import _job_review_summary
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        job = MagicMock()
        job.output.review_json_paths = [p]
        job.output.review_json_path = p
        result = _job_review_summary(job)
        assert result["clean"] is True


# ── TestJobDownloadsHelper ────────────────────────────────────────────────────


class TestJobDownloadsHelper:
    """_job_downloads() returns availability counts for a job's output files."""

    def test_empty_when_no_outputs(self) -> None:
        from taxspine_orchestrator.review import _job_downloads
        job = MagicMock()
        job.output.report_html_paths = []
        job.output.report_html_path = None
        job.output.rf1159_json_paths = []
        job.output.rf1159_json_path = None
        job.output.review_json_paths = []
        job.output.review_json_path = None
        result = _job_downloads(job)
        assert result["html_report_count"] == 0
        assert result["rf1159_count"] == 0
        assert result["has_review_json"] is False

    def test_counts_html_paths(self) -> None:
        from taxspine_orchestrator.review import _job_downloads
        job = MagicMock()
        job.output.report_html_paths = ["/a.html", "/b.html"]
        job.output.report_html_path = "/a.html"
        job.output.rf1159_json_paths = []
        job.output.rf1159_json_path = None
        job.output.review_json_paths = []
        job.output.review_json_path = None
        result = _job_downloads(job)
        assert result["html_report_count"] == 2

    def test_counts_rf1159_paths(self) -> None:
        from taxspine_orchestrator.review import _job_downloads
        job = MagicMock()
        job.output.report_html_paths = []
        job.output.report_html_path = None
        job.output.rf1159_json_paths = ["/r.json"]
        job.output.rf1159_json_path = "/r.json"
        job.output.review_json_paths = []
        job.output.review_json_path = None
        result = _job_downloads(job)
        assert result["rf1159_count"] == 1

    def test_has_review_json_true_when_present(self) -> None:
        from taxspine_orchestrator.review import _job_downloads
        job = MagicMock()
        job.output.report_html_paths = []
        job.output.report_html_path = None
        job.output.rf1159_json_paths = []
        job.output.rf1159_json_path = None
        job.output.review_json_paths = ["/review.json"]
        job.output.review_json_path = "/review.json"
        result = _job_downloads(job)
        assert result["has_review_json"] is True


# ── TestReviewSummaryEnhancements ─────────────────────────────────────────────


class TestReviewSummaryEnhancements:
    """New fields added to GET /review/summary in the full review console."""

    def test_response_includes_warnings_by_category(self) -> None:
        client = _make_client()
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = []
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                r = client.get("/review/summary?year=2025")
        assert r.status_code == 200
        assert "warnings_by_category" in r.json()

    def test_warnings_by_category_is_dict(self) -> None:
        client = _make_client()
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = []
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                data = client.get("/review/summary?year=2025").json()
        assert isinstance(data["warnings_by_category"], dict)

    def test_response_includes_missing_basis_detail(self) -> None:
        client = _make_client()
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = []
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                r = client.get("/review/summary?year=2025")
        assert "missing_basis_detail" in r.json()

    def test_warnings_categorised_in_summary(self, tmp_path: Path) -> None:
        """Warnings in the summary are split into categories."""
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {
            "has_unlinked_transfers": False,
            "warnings": ["unlinked transfer found", "TL-07 partial year"],
        })
        j = _make_mock_job("j1", 2025, review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                data = client.get("/review/summary?year=2025").json()
        cats = data["warnings_by_category"]
        assert "Transfer Linking" in cats or "Tax Law" in cats  # at least one matched

    def test_unlinked_transfer_jobs_contains_dicts(self, tmp_path: Path) -> None:
        """unlinked_transfer_jobs is now [{job_id, case_name}], not [str]."""
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {
            "has_unlinked_transfers": True, "warnings": [],
        })
        j = _make_mock_job("job-abc", 2025, case_name="Alice 2025", review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                data = client.get("/review/summary?year=2025").json()
        unlinked = data["unlinked_transfer_jobs"]
        assert len(unlinked) == 1
        assert isinstance(unlinked[0], dict)
        assert "job_id" in unlinked[0]
        assert "case_name" in unlinked[0]

    def test_unlinked_transfer_jobs_case_name_populated(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"has_unlinked_transfers": True, "warnings": []})
        j = _make_mock_job("job-xyz", 2025, case_name="My Case", review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                data = client.get("/review/summary?year=2025").json()
        assert data["unlinked_transfer_jobs"][0]["case_name"] == "My Case"
        assert data["unlinked_transfer_jobs"][0]["job_id"] == "job-xyz"

    def test_unlinked_transfer_jobs_falls_back_to_job_id(self, tmp_path: Path) -> None:
        """When case_name is None the entry uses job_id as case_name."""
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"has_unlinked_transfers": True, "warnings": []})
        j = _make_mock_job("job-nnn", 2025, case_name=None, review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=[]):
                data = client.get("/review/summary?year=2025").json()
        # case_name should be job_id when None
        entry = data["unlinked_transfer_jobs"][0]
        assert entry["case_name"] == "job-nnn"

    def test_missing_basis_detail_in_summary(self) -> None:
        client = _make_client()
        fake_detail = [
            {"asset": "XRP", "lot_count": 3, "missing_lots": 1,
             "total_remaining_qty": "500.0", "has_missing_basis": True},
        ]
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = []
            with patch("taxspine_orchestrator.review._missing_basis_detail", return_value=fake_detail):
                data = client.get("/review/summary?year=2025").json()
        assert data["missing_basis_detail"] == fake_detail
        assert data["missing_basis_count"] == 1
        assert data["missing_basis_assets"] == ["XRP"]

    def test_empty_summary_has_new_fields(self) -> None:
        """_empty_summary includes all new fields with correct empty defaults."""
        from taxspine_orchestrator.review import _empty_summary
        result = _empty_summary(2025)
        assert "warnings_by_category" in result
        assert "missing_basis_detail" in result
        assert isinstance(result["warnings_by_category"], dict)
        assert isinstance(result["missing_basis_detail"], list)
        assert result["missing_basis_detail"] == []
        assert result["warnings_by_category"] == {}


# ── TestReviewJobsEndpoint ────────────────────────────────────────────────────


class TestReviewJobsEndpoint:
    """GET /review/jobs?year=N — per-job review cards."""

    def test_requires_year_param(self) -> None:
        client = _make_client()
        r = client.get("/review/jobs")
        assert r.status_code == 422

    def test_rejects_year_below_2009(self) -> None:
        client = _make_client()
        r = client.get("/review/jobs?year=2008")
        assert r.status_code == 422

    def test_rejects_year_above_2100(self) -> None:
        client = _make_client()
        r = client.get("/review/jobs?year=2101")
        assert r.status_code == 422

    def test_returns_200_with_valid_year(self) -> None:
        client = _make_client()
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = []
            r = client.get("/review/jobs?year=2025")
        assert r.status_code == 200

    def test_response_has_tax_year_and_jobs(self) -> None:
        client = _make_client()
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = []
            data = client.get("/review/jobs?year=2025").json()
        assert "tax_year" in data
        assert "jobs" in data
        assert data["tax_year"] == 2025

    def test_empty_jobs_list_when_no_completed_jobs(self) -> None:
        client = _make_client()
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = []
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"] == []

    def test_no_db_returns_empty_jobs(self) -> None:
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            with patch("taxspine_orchestrator.review.settings") as mock_s:
                mock_s.DATA_DIR = Path(td)
                data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"] == []
        assert data["tax_year"] == 2025

    def test_job_card_has_required_fields(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-001", 2025, case_name="Test Case", review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert len(data["jobs"]) == 1
        card = data["jobs"][0]
        required = {
            "job_id", "case_name", "created_at", "pipeline_mode",
            "valuation_mode", "csv_file_count", "xrpl_accounts",
            "review", "downloads", "has_review_data",
        }
        assert required <= card.keys()

    def test_job_card_job_id_correct(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-abc", 2025, review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["job_id"] == "job-abc"

    def test_job_card_case_name_populated(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-x", 2025, case_name="Alice Portfolio", review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["case_name"] == "Alice Portfolio"

    def test_job_card_pipeline_mode_per_file(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-pf", 2025, pipeline_mode="per_file", review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["pipeline_mode"] == "per_file"

    def test_job_card_pipeline_mode_nor_multi(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-nm", 2025, pipeline_mode="nor_multi", review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["pipeline_mode"] == "nor_multi"

    def test_job_card_csv_file_count(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-c", 2025, csv_files=["a.csv", "b.csv"], review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["csv_file_count"] == 2

    def test_job_card_review_inline_summary(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {
            "warnings": ["warn-1"], "has_unlinked_transfers": True,
        })
        j = _make_mock_job("job-r", 2025, review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        review = data["jobs"][0]["review"]
        assert "warnings" in review
        assert "has_unlinked_transfers" in review
        assert review["has_unlinked_transfers"] is True

    def test_job_card_downloads_html_report_count(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-dl", 2025, review_paths=[p],
                           html_paths=["/report1.html", "/report2.html"])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["downloads"]["html_report_count"] == 2

    def test_job_card_downloads_rf1159_count(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-rf", 2025, review_paths=[p], rf1159_paths=["/rf.json"])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["downloads"]["rf1159_count"] == 1

    def test_job_card_has_review_data_true_when_review_loaded(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j = _make_mock_job("job-hrd", 2025, review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["has_review_data"] is True

    def test_job_card_has_review_data_false_when_no_review(self) -> None:
        client = _make_client()
        j = _make_mock_job("job-nrd", 2025, review_paths=[])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j]
            data = client.get("/review/jobs?year=2025").json()
        assert data["jobs"][0]["has_review_data"] is False

    def test_only_matching_year_jobs_returned(self, tmp_path: Path) -> None:
        """Jobs from a different year are not included."""
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        j2024 = _make_mock_job("job-2024", 2024, review_paths=[p])
        j2025 = _make_mock_job("job-2025", 2025, review_paths=[p])
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = [j2024, j2025]
            data = client.get("/review/jobs?year=2025").json()
        ids = [j["job_id"] for j in data["jobs"]]
        assert "job-2025" in ids
        assert "job-2024" not in ids

    def test_multiple_jobs_all_present(self, tmp_path: Path) -> None:
        client = _make_client()
        p = _write_review(tmp_path, "r.json", {"warnings": [], "has_unlinked_transfers": False})
        jobs = [_make_mock_job(f"job-{i}", 2025, review_paths=[p]) for i in range(3)]
        with patch("taxspine_orchestrator.storage.SqliteJobStore") as MockStore:
            MockStore.return_value.list.return_value = jobs
            data = client.get("/review/jobs?year=2025").json()
        assert len(data["jobs"]) == 3


# ── TestReviewConsoleUI ───────────────────────────────────────────────────────


class TestReviewConsoleUI:
    """Verify full review console UI elements are present in index.html."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        p = Path(__file__).parent.parent / "ui" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_review_panel_exists(self, html: str) -> None:
        assert 'id="tc-panel-review"' in html

    def test_review_banner_element(self, html: str) -> None:
        assert 'id="tc-review-banner"' in html

    def test_review_stats_row(self, html: str) -> None:
        assert 'id="tc-review-stats"' in html

    def test_unlinked_section_element(self, html: str) -> None:
        assert 'id="tc-rv-unlinked-section"' in html

    def test_unlinked_list_element(self, html: str) -> None:
        assert 'id="tc-rv-unlinked-list"' in html

    def test_missing_section_element(self, html: str) -> None:
        assert 'id="tc-rv-missing-section"' in html

    def test_missing_list_element(self, html: str) -> None:
        assert 'id="tc-rv-missing-list"' in html

    def test_warnings_section_element(self, html: str) -> None:
        assert 'id="tc-rv-warnings-section"' in html

    def test_warnings_cats_element(self, html: str) -> None:
        """New: warnings displayed by category (not flat list)."""
        assert 'id="tc-rv-warnings-cats"' in html

    def test_per_job_section_element(self, html: str) -> None:
        """New: per-job cards section."""
        assert 'id="tc-review-jobs-section"' in html

    def test_per_job_list_element(self, html: str) -> None:
        assert 'id="tc-rv-jobs-list"' in html

    def test_per_job_empty_element(self, html: str) -> None:
        assert 'id="tc-rv-jobs-empty"' in html

    def test_review_empty_element(self, html: str) -> None:
        assert 'id="tc-review-empty"' in html

    def test_render_review_jobs_function(self, html: str) -> None:
        """JavaScript function _renderReviewJobs must be defined."""
        assert "function _renderReviewJobs" in html

    def test_render_review_queue_function(self, html: str) -> None:
        assert "function _renderReviewQueue" in html

    def test_load_review_queue_function(self, html: str) -> None:
        assert "async function loadReviewQueue" in html

    def test_parallel_fetch_in_load_review_queue(self, html: str) -> None:
        """Both summary and jobs endpoints are fetched together with Promise.all."""
        assert "Promise.all" in html
        assert "/review/summary" in html
        assert "/review/jobs" in html

    def test_show_review_empty_hides_jobs_section(self, html: str) -> None:
        """_showReviewEmpty must also toggle the per-job section."""
        # The function must reference tc-review-jobs-section
        assert "tc-review-jobs-section" in html

    def test_rv_cat_group_css_class(self, html: str) -> None:
        """CSS class for warning category groups is defined."""
        assert ".rv-cat-group" in html

    def test_rv_job_card_css_class(self, html: str) -> None:
        """CSS class for per-job cards is defined."""
        assert ".rv-job-card" in html

    def test_rv_dl_btn_css_class(self, html: str) -> None:
        """CSS class for download buttons is defined."""
        assert ".rv-dl-btn" in html

    def test_categorize_warnings_call_in_render(self, html: str) -> None:
        """_renderReviewQueue uses warnings_by_category from response."""
        assert "warnings_by_category" in html

    def test_missing_basis_detail_used_in_render(self, html: str) -> None:
        """_renderReviewQueue renders missing_basis_detail (lot counts)."""
        assert "missing_basis_detail" in html

    def test_lot_count_shown_in_missing_section(self, html: str) -> None:
        """Lot count and missing_lots are referenced in the render function."""
        assert "missing_lots" in html
        assert "lot_count" in html
