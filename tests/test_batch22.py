"""Batch 22 — Remaining open audit findings.

Coverage:
    API-10  get_job_review silently swallowed individual file read errors —
            now logs a WARNING instead of bare ``pass``.
    FE-06   badgeHtml() fallback used unescaped server-supplied status string —
            now wrapped in escHtml() to prevent XSS.
    LC-14   Repository had no LICENSE file — MIT LICENSE added to repo root.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    Job,
    JobInput,
    JobOutput,
    JobStatus,
)

# ---------------------------------------------------------------------------
# Shared test client
# ---------------------------------------------------------------------------

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helper: build a minimal Job fixture with specific review_json_paths
# ---------------------------------------------------------------------------

def _make_job(review_json_paths: list[str]) -> Job:
    now = datetime.now(timezone.utc)
    return Job(
        id="test-review-job-batch22",
        status=JobStatus.COMPLETED,
        input=JobInput(
            csv_files=[CsvFileSpec(path="dummy.csv", source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
        ),
        output=JobOutput(review_json_paths=review_json_paths),
        created_at=now,
        updated_at=now,
    )


# ===========================================================================
# TestAPI10ReviewErrorLogging
# ===========================================================================


class TestAPI10ReviewErrorLogging:
    """API-10: unreadable review files must be logged, not silently swallowed."""

    def test_api10_except_clause_captures_exc(self):
        """The except clause in get_job_review binds 'exc' (not bare pass)."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        assert "except (OSError, ValueError) as exc:" in src

    def test_api10_warning_logged_for_unreadable_file(self):
        """_log.warning is called when a review file cannot be read."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        assert "_log.warning" in src

    def test_api10_comment_present_in_main(self):
        """The API-10 comment is present in main.py."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        assert "API-10" in src

    def test_api10_warning_fires_on_corrupt_review_file(self, tmp_path, caplog):
        """A corrupt review file triggers a WARNING-level log entry."""
        # Write a corrupt (invalid JSON) review file.
        corrupt = tmp_path / "review.json"
        corrupt.write_bytes(b"NOT-JSON{{{{")

        job = _make_job([str(corrupt)])

        with patch("taxspine_orchestrator.main._job_service") as mock_svc:
            mock_svc.get_job.return_value = job
            with caplog.at_level(logging.WARNING, logger="taxspine_orchestrator.main"):
                resp = client.get(f"/jobs/{job.id}/review")

        # Invalid JSON → loaded == 0 → 404
        assert resp.status_code == 404
        # The filename appears in the warning regardless of path separator encoding.
        assert corrupt.name in caplog.text, (
            f"Expected warning mentioning {corrupt.name!r} in caplog, got: {caplog.text!r}"
        )

    def test_api10_warning_fires_on_missing_review_file(self, tmp_path, caplog):
        """A missing review file triggers a WARNING-level log entry (OSError)."""
        missing_path = str(tmp_path / "nonexistent_review.json")
        job = _make_job([missing_path])

        with patch("taxspine_orchestrator.main._job_service") as mock_svc:
            mock_svc.get_job.return_value = job
            with caplog.at_level(logging.WARNING, logger="taxspine_orchestrator.main"):
                resp = client.get(f"/jobs/{job.id}/review")

        assert resp.status_code == 404
        # The filename appears in the warning regardless of path separator encoding.
        assert "nonexistent_review.json" in caplog.text, (
            f"Expected warning mentioning the missing filename in caplog, got: {caplog.text!r}"
        )

    def test_api10_no_bare_pass_in_review_handler(self):
        """The bare 'pass' exception handler no longer exists in the review block."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        # The old pattern was: except (OSError, ValueError):\n            pass
        # That exact bare-pass form must not exist anywhere.
        assert "except (OSError, ValueError):\n            pass" not in src

    def test_api10_valid_review_file_still_loads(self, tmp_path):
        """A valid review file still loads correctly after the API-10 fix."""
        review_data = {
            "has_unlinked_transfers": False,
            "warnings": ["test-warning"],
        }
        review_file = tmp_path / "review.json"
        review_file.write_text(json.dumps(review_data), encoding="utf-8")

        job = _make_job([str(review_file)])

        with patch("taxspine_orchestrator.main._job_service") as mock_svc:
            mock_svc.get_job.return_value = job
            resp = client.get(f"/jobs/{job.id}/review")

        assert resp.status_code == 200
        body = resp.json()
        assert body["warnings"] == ["test-warning"]
        assert body["source_count"] == 1
        assert body["clean"] is False  # has a warning

    def test_api10_partial_failure_logs_and_loads_good_files(self, tmp_path, caplog):
        """When one review file is unreadable, the good one still loads and a warning is logged."""
        good = tmp_path / "review_good.json"
        good.write_text(json.dumps({"has_unlinked_transfers": False, "warnings": ["ok"]}),
                        encoding="utf-8")
        bad = tmp_path / "review_bad.json"
        bad.write_bytes(b"\x00\x01\x02BINARY")  # not valid JSON

        job = _make_job([str(good), str(bad)])

        with patch("taxspine_orchestrator.main._job_service") as mock_svc:
            mock_svc.get_job.return_value = job
            with caplog.at_level(logging.WARNING, logger="taxspine_orchestrator.main"):
                resp = client.get(f"/jobs/{job.id}/review")

        # One good file → 200
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_count"] == 1
        assert body["warnings"] == ["ok"]
        # The bad file's name is logged as a warning.
        assert bad.name in caplog.text


# ===========================================================================
# TestFE06BadgeHtmlEscaping
# ===========================================================================


class TestFE06BadgeHtmlEscaping:
    """FE-06: badgeHtml() fallback path must escape server-supplied status text."""

    def _read_ui(self) -> str:
        ui_path = Path(__file__).parent.parent / "ui" / "index.html"
        return ui_path.read_text(encoding="utf-8")

    def test_fe06_badge_fallback_uses_esc_html(self):
        """The badgeHtml fallback now wraps String(status) in escHtml()."""
        src = self._read_ui()
        assert "escHtml(String(status)).toUpperCase()" in src

    def test_fe06_bare_unescaped_fallback_removed(self):
        """The old unescaped fallback (String(status).toUpperCase() without escHtml) is gone."""
        src = self._read_ui()
        lines = src.splitlines()
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue  # skip comment lines
            if "String(status).toUpperCase()" in line and "escHtml" not in line:
                pytest.fail(
                    f"Unescaped fallback found on line {lineno}: {line.strip()!r}"
                )

    def test_fe06_comment_present(self):
        """The FE-06 audit comment is present in index.html."""
        src = self._read_ui()
        assert "FE-06" in src

    def test_fe06_esc_html_function_exists(self):
        """The escHtml() helper function is defined in index.html."""
        src = self._read_ui()
        assert "function escHtml(" in src

    def test_fe06_badge_html_function_present(self):
        """The badgeHtml() function is present in index.html."""
        src = self._read_ui()
        assert "function badgeHtml(" in src or "badgeHtml" in src


# ===========================================================================
# TestLC14LicenseFile
# ===========================================================================


class TestLC14LicenseFile:
    """LC-14: Repository must contain a LICENSE file."""

    def _license_path(self) -> Path:
        return Path(__file__).parent.parent / "LICENSE"

    def test_lc14_license_file_exists(self):
        """A LICENSE file exists at the repository root."""
        assert self._license_path().exists(), "LICENSE file not found in repo root"

    def test_lc14_license_file_not_empty(self):
        """The LICENSE file has content (not empty)."""
        content = self._license_path().read_text(encoding="utf-8")
        assert len(content.strip()) > 50, "LICENSE file appears to be empty or trivially short"

    def test_lc14_license_contains_mit_or_copyright(self):
        """The LICENSE file contains 'MIT' or 'Copyright' (standard license markers)."""
        content = self._license_path().read_text(encoding="utf-8").upper()
        assert "MIT" in content or "COPYRIGHT" in content, (
            "LICENSE file does not contain MIT or Copyright marker"
        )

    def test_lc14_license_contains_permission_grant(self):
        """The LICENSE file contains a permission grant clause."""
        content = self._license_path().read_text(encoding="utf-8")
        assert "Permission" in content or "permission" in content, (
            "LICENSE file does not contain a permission clause"
        )

    def test_lc14_license_contains_warranty_disclaimer(self):
        """The LICENSE file contains a warranty disclaimer."""
        content = self._license_path().read_text(encoding="utf-8").upper()
        assert "WARRANTY" in content, "LICENSE file does not contain a warranty disclaimer"
