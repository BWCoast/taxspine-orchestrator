"""Tests for GET /jobs/{id}/review endpoint.

Covers:
  TestGetJobReview            — basic review endpoint behavior
  TestGetJobReviewMerge       — multi-source merging
  TestGetJobReviewEdgeCases   — missing files, no paths, etc.
  TestReviewJsonPaths         — JobOutput.review_json_paths field
  TestCommandBuilderReviewFlag — --review-json in command builders
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    JobInput,
    JobOutput,
    JobStatus,
)
from taxspine_orchestrator.services import JobService


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Clear the in-memory store between tests so they don't leak state."""
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_job_with_review(
    client: TestClient,
    tmp_path: Path,
    review_files: list[dict] | None = None,
) -> tuple[str, list[Path]]:
    """Create a job and inject review JSON files directly into the job store.

    Returns (job_id, list_of_written_paths).
    """
    from taxspine_orchestrator import main as _m

    resp = client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
    assert resp.status_code == 200
    job_id = resp.json()["id"]

    written_paths: list[Path] = []
    if review_files:
        for i, payload in enumerate(review_files):
            p = tmp_path / f"review_{i}.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            written_paths.append(p)

    paths_str = [str(p) for p in written_paths]
    output = JobOutput(
        review_json_path=paths_str[0] if paths_str else None,
        review_json_paths=paths_str,
    )
    _m._job_store.update_job(job_id, status=JobStatus.COMPLETED, output=output)
    return job_id, written_paths


_CLEAN_REVIEW = {
    "has_unlinked_transfers": False,
    "warning_count": 0,
    "warnings": [],
    "clean": True,
}

_UNLINKED_REVIEW = {
    "has_unlinked_transfers": True,
    "warning_count": 1,
    "warnings": ["Unlinked transfer detected for BTC"],
    "clean": False,
}


# ── TestGetJobReview ──────────────────────────────────────────────────────────


class TestGetJobReview:
    """Basic review endpoint behavior."""

    def test_404_when_job_not_found(self, client: TestClient) -> None:
        resp = client.get("/jobs/nonexistent-id/review")
        assert resp.status_code == 404

    def test_404_when_no_review_paths(self, client: TestClient, tmp_path: Path) -> None:
        """Completed job with no review paths → 404."""
        from taxspine_orchestrator import main as _m

        resp = client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
        job_id = resp.json()["id"]
        # Update to completed with empty output (no review paths)
        _m._job_store.update_job(job_id, status=JobStatus.COMPLETED, output=JobOutput())

        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 404

    def test_returns_review_data(self, client: TestClient, tmp_path: Path) -> None:
        """Job with review_json_path pointing to a real file → 200, correct body."""
        job_id, _ = _create_job_with_review(client, tmp_path, [_CLEAN_REVIEW])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_unlinked_transfers"] is False
        assert body["warning_count"] == 0
        assert body["warnings"] == []

    def test_clean_true_when_no_warnings(self, client: TestClient, tmp_path: Path) -> None:
        job_id, _ = _create_job_with_review(client, tmp_path, [_CLEAN_REVIEW])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["clean"] is True

    def test_has_unlinked_transfers(self, client: TestClient, tmp_path: Path) -> None:
        job_id, _ = _create_job_with_review(client, tmp_path, [_UNLINKED_REVIEW])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["has_unlinked_transfers"] is True

    def test_warning_count_matches(self, client: TestClient, tmp_path: Path) -> None:
        payload = {
            "has_unlinked_transfers": False,
            "warning_count": 3,
            "warnings": ["warn A", "warn B", "warn C"],
            "clean": False,
        }
        job_id, _ = _create_job_with_review(client, tmp_path, [payload])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["warning_count"] == len(body["warnings"])

    def test_source_count_in_response(self, client: TestClient, tmp_path: Path) -> None:
        job_id, _ = _create_job_with_review(client, tmp_path, [_CLEAN_REVIEW])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["source_count"] == 1


# ── TestGetJobReviewMerge ─────────────────────────────────────────────────────


class TestGetJobReviewMerge:
    """Multi-source review file merging."""

    def test_merges_multiple_review_files(self, client: TestClient, tmp_path: Path) -> None:
        """Warnings from both files are present in the merged response."""
        file_a = {
            "has_unlinked_transfers": False,
            "warning_count": 2,
            "warnings": ["warn-1", "warn-2"],
            "clean": False,
        }
        file_b = {
            "has_unlinked_transfers": False,
            "warning_count": 1,
            "warnings": ["warn-3"],
            "clean": False,
        }
        job_id, _ = _create_job_with_review(client, tmp_path, [file_a, file_b])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        body = resp.json()
        assert "warn-1" in body["warnings"]
        assert "warn-2" in body["warnings"]
        assert "warn-3" in body["warnings"]

    def test_any_unlinked_makes_overall_unlinked(self, client: TestClient, tmp_path: Path) -> None:
        """One file has has_unlinked_transfers=True → merged result is True."""
        file_clean = {
            "has_unlinked_transfers": False,
            "warning_count": 0,
            "warnings": [],
            "clean": True,
        }
        file_unlinked = _UNLINKED_REVIEW
        job_id, _ = _create_job_with_review(client, tmp_path, [file_clean, file_unlinked])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["has_unlinked_transfers"] is True

    def test_total_warning_count_is_sum(self, client: TestClient, tmp_path: Path) -> None:
        """2 warnings in file A + 3 warnings in file B → warning_count == 5."""
        file_a = {
            "has_unlinked_transfers": False,
            "warning_count": 2,
            "warnings": ["w1", "w2"],
            "clean": False,
        }
        file_b = {
            "has_unlinked_transfers": False,
            "warning_count": 3,
            "warnings": ["w3", "w4", "w5"],
            "clean": False,
        }
        job_id, _ = _create_job_with_review(client, tmp_path, [file_a, file_b])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["warning_count"] == 5

    def test_source_count_reflects_files_read(self, client: TestClient, tmp_path: Path) -> None:
        """Two review files → source_count == 2."""
        job_id, _ = _create_job_with_review(client, tmp_path, [_CLEAN_REVIEW, _CLEAN_REVIEW])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["source_count"] == 2


# ── TestGetJobReviewEdgeCases ─────────────────────────────────────────────────


class TestGetJobReviewEdgeCases:
    """Edge cases: missing files, fallback paths, etc."""

    def test_404_when_review_files_missing_on_disk(self, client: TestClient, tmp_path: Path) -> None:
        """review_json_path set but file deleted → 404."""
        from taxspine_orchestrator import main as _m

        resp = client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
        job_id = resp.json()["id"]

        ghost_path = str(tmp_path / "ghost_review.json")
        # Do NOT create the file — it should be missing on disk.
        output = JobOutput(
            review_json_path=ghost_path,
            review_json_paths=[ghost_path],
        )
        _m._job_store.update_job(job_id, status=JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 404

    def test_skips_unreadable_files_gracefully(self, client: TestClient, tmp_path: Path) -> None:
        """One good file, one missing → returns data from the good one."""
        from taxspine_orchestrator import main as _m

        good_path = tmp_path / "good_review.json"
        good_path.write_text(json.dumps(_CLEAN_REVIEW), encoding="utf-8")

        missing_path = str(tmp_path / "missing_review.json")
        # Don't create missing_path.

        resp = client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
        job_id = resp.json()["id"]
        output = JobOutput(
            review_json_path=str(good_path),
            review_json_paths=[str(good_path), missing_path],
        )
        _m._job_store.update_job(job_id, status=JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        # Only the one good file was loaded.
        assert resp.json()["source_count"] == 1

    def test_single_path_fallback(self, client: TestClient, tmp_path: Path) -> None:
        """review_json_path set (not review_json_paths list) → endpoint works."""
        from taxspine_orchestrator import main as _m

        review_path = tmp_path / "single_review.json"
        review_path.write_text(json.dumps(_CLEAN_REVIEW), encoding="utf-8")

        resp = client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
        job_id = resp.json()["id"]
        output = JobOutput(
            review_json_path=str(review_path),
            review_json_paths=[],  # empty list — fallback to singular field
        )
        _m._job_store.update_job(job_id, status=JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["clean"] is True

    def test_clean_false_when_has_unlinked(self, client: TestClient, tmp_path: Path) -> None:
        """has_unlinked_transfers=True → clean is False in response."""
        job_id, _ = _create_job_with_review(client, tmp_path, [_UNLINKED_REVIEW])
        resp = client.get(f"/jobs/{job_id}/review")
        assert resp.status_code == 200
        assert resp.json()["clean"] is False


# ── TestReviewJsonPaths ───────────────────────────────────────────────────────


class TestReviewJsonPaths:
    """JobOutput.review_json_paths field behavior."""

    def test_joboutput_has_review_fields(self) -> None:
        output = JobOutput()
        assert hasattr(output, "review_json_path")
        assert hasattr(output, "review_json_paths")

    def test_review_json_paths_defaults_to_empty_list(self) -> None:
        output = JobOutput()
        assert output.review_json_paths == []

    def test_review_json_path_defaults_to_none(self) -> None:
        output = JobOutput()
        assert output.review_json_path is None


# ── TestCommandBuilderReviewFlag ──────────────────────────────────────────────


class TestCommandBuilderReviewFlag:
    """--review-json is NOT passed to taxspine-nor-report or taxspine-nor-multi
    (neither CLI supports the flag in the current installed version)."""

    def test_build_csv_command_does_not_include_review_json(self, tmp_path: Path) -> None:
        """taxspine-nor-report does not accept --review-json; flag must be absent."""
        ji = JobInput(tax_year=2025, country=Country.NORWAY)
        spec = CsvFileSpec(path="/data/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)
        html_path = tmp_path / "report.html"
        review_path = tmp_path / "review.json"

        cmd = JobService._build_csv_command(
            ji,
            csv_spec=spec,
            html_path=html_path,
            review_json_path=review_path,
        )
        assert "--review-json" not in cmd

    def test_build_nor_multi_command_does_not_include_review_json(self, tmp_path: Path) -> None:
        """taxspine-nor-multi does not accept --review-json; flag must be absent."""
        ji = JobInput(tax_year=2025, country=Country.NORWAY)
        specs = [CsvFileSpec(path="/data/events.csv", source_type=CsvSourceType.GENERIC_EVENTS)]
        html_path = tmp_path / "report.html"
        review_path = tmp_path / "review.json"

        cmd = JobService._build_nor_multi_command(
            ji,
            csv_specs=specs,
            html_path=html_path,
            review_json_path=review_path,
        )
        assert "--review-json" not in cmd
