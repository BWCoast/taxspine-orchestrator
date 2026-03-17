"""Batch 12 — regression tests for MEDIUM findings.

Findings covered
----------------
API-18  tax_year has no range validation (models.py)
API-20  blocking Path.read_text() in async /alerts handler
SEC-18  Tailwind loaded from CDN without SRI → replaced with local file
SEC-19  /dedup/sources exposed absolute db_path
FE-10   loadAlerts() skips r.ok check
FE-11   openResultsById silent failure on !r.ok
FE-12   dry_run checkbox not reset after successful runReport
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ── shared helpers ────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_REPO = _HERE.parent
_HTML_PATH = _REPO / "ui" / "index.html"


def _html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


# ── API-18 · tax_year validation ──────────────────────────────────────────────


class TestAPI18TaxYearValidation:
    """POST /jobs rejects out-of-range tax_year values (API-18)."""

    @pytest.fixture(autouse=True)
    def _client(self):
        from taxspine_orchestrator.main import app
        self.client = TestClient(app)

    def _create_body(self, tax_year: int) -> dict:
        return {
            "xrpl_accounts": [],
            "csv_files": [],
            "tax_year": tax_year,
            "country": "norway",
        }

    def test_valid_tax_year_accepted(self):
        """2025 is within the valid range."""
        resp = self.client.post("/jobs", json=self._create_body(2025))
        # May succeed (201) or fail with 422 for missing inputs, but NOT because
        # of the tax_year field itself.
        data = resp.json()
        if resp.status_code == 422:
            errors = data.get("detail", [])
            assert not any(
                "tax_year" in str(e) for e in errors
            ), f"tax_year=2025 should be valid, got: {errors}"

    def test_tax_year_zero_rejected(self):
        """tax_year=0 is below ge=2009 and must return 422."""
        resp = self.client.post("/jobs", json=self._create_body(0))
        assert resp.status_code == 422
        detail = str(resp.json())
        assert "tax_year" in detail.lower() or "2009" in detail

    def test_tax_year_negative_rejected(self):
        """tax_year=-1 is below ge=2009 and must return 422."""
        resp = self.client.post("/jobs", json=self._create_body(-1))
        assert resp.status_code == 422

    def test_tax_year_too_far_future_rejected(self):
        """tax_year=9999 is above le=2100 and must return 422."""
        resp = self.client.post("/jobs", json=self._create_body(9999))
        assert resp.status_code == 422

    def test_tax_year_minimum_boundary_accepted(self):
        """tax_year=2009 (Bitcoin genesis year) is the lower bound."""
        resp = self.client.post("/jobs", json=self._create_body(2009))
        # Should not fail with a tax_year validation error.
        if resp.status_code == 422:
            errors = resp.json().get("detail", [])
            assert not any(
                "tax_year" in str(e) for e in errors
            ), f"tax_year=2009 should be valid, got: {errors}"

    def test_workspace_run_tax_year_zero_rejected(self):
        """POST /workspace/run also validates tax_year."""
        resp = self.client.post(
            "/workspace/run",
            json={"tax_year": 0, "country": "norway"},
        )
        assert resp.status_code == 422

    def test_workspace_run_tax_year_2025_accepted(self):
        """POST /workspace/run accepts tax_year=2025."""
        resp = self.client.post(
            "/workspace/run",
            json={"tax_year": 2025, "country": "norway"},
        )
        # A workspace run may fail for other reasons (no accounts/CSVs),
        # but NOT because of the tax_year field.
        if resp.status_code == 422:
            errors = resp.json().get("detail", [])
            assert not any(
                "tax_year" in str(e) for e in errors
            ), f"tax_year=2025 should be valid, got: {errors}"


# ── API-20 · asyncio.to_thread in get_alerts ─────────────────────────────────


class TestAPI20AsyncAlertsIO:
    """get_alerts wraps Path.read_text() in asyncio.to_thread (API-20)."""

    def test_asyncio_to_thread_present_in_alerts_handler(self):
        """The get_alerts function uses asyncio.to_thread for file I/O."""
        main_path = _REPO / "taxspine_orchestrator" / "main.py"
        src = main_path.read_text(encoding="utf-8")

        # Find the get_alerts function body
        fn_start = src.find("async def get_alerts(")
        assert fn_start != -1, "get_alerts function not found"

        # The next async def after get_alerts (end of function scope)
        next_fn = src.find("\n@app.", fn_start + 1)
        fn_body = src[fn_start:next_fn] if next_fn != -1 else src[fn_start:]

        assert "asyncio.to_thread" in fn_body, (
            "get_alerts must use asyncio.to_thread for Path.read_text() "
            "to avoid blocking the event loop (API-20)"
        )

    def test_read_text_not_called_bare_in_alerts(self):
        """No bare .read_text() call in get_alerts (should be inside to_thread)."""
        main_path = _REPO / "taxspine_orchestrator" / "main.py"
        src = main_path.read_text(encoding="utf-8")

        fn_start = src.find("async def get_alerts(")
        assert fn_start != -1
        next_fn = src.find("\n@app.", fn_start + 1)
        fn_body = src[fn_start:next_fn] if next_fn != -1 else src[fn_start:]

        # .read_text() must NOT appear as a direct await target; it must be
        # wrapped in asyncio.to_thread(...)
        import re
        bare_reads = re.findall(r"Path\([^)]+\)\.read_text\(", fn_body)
        for call in bare_reads:
            # Each occurrence should be preceded by "asyncio.to_thread(" in the
            # same expression — check by looking for to_thread in the same line.
            for line in fn_body.splitlines():
                if call in line:
                    assert "to_thread" in line, (
                        f"Bare .read_text() found without asyncio.to_thread "
                        f"in get_alerts: {line.strip()!r}"
                    )


# ── SEC-18 · self-hosted Tailwind CSS ────────────────────────────────────────


class TestSEC18TailwindSelfHosted:
    """Tailwind CSS is loaded from a local file, not the CDN play-script (SEC-18)."""

    def test_cdn_play_script_tag_removed(self):
        """The CDN play-script <script src="cdn.tailwindcss.com"> must be gone."""
        html = _html()
        assert 'src="https://cdn.tailwindcss.com"' not in html, (
            "CDN play-script tag still present in index.html — "
            "replace with self-hosted tailwind.min.css (SEC-18)"
        )

    def test_local_tailwind_link_present(self):
        """index.html references tailwind.min.css as a local stylesheet."""
        html = _html()
        assert "tailwind.min.css" in html, (
            "index.html must reference tailwind.min.css (local file) for SEC-18"
        )

    def test_dockerfile_downloads_tailwind(self):
        """Dockerfile contains a step to download tailwind.min.css."""
        dockerfile = (_REPO / "Dockerfile").read_text(encoding="utf-8")
        assert "tailwind" in dockerfile.lower(), (
            "Dockerfile must download tailwind.min.css during build (SEC-18)"
        )
        assert "TAILWIND_VERSION" in dockerfile, (
            "Tailwind version must be pinned via ARG TAILWIND_VERSION in Dockerfile"
        )

    def test_cdn_fallback_script_present(self):
        """An error-handler fallback to CDN exists for local dev convenience."""
        html = _html()
        assert "tw-css" in html or "tailwind.min.css" in html
        # The onerror CDN fallback must reference cdn.tailwindcss.com
        assert "cdn.tailwindcss.com" in html, (
            "CDN fallback must be present for local dev (onerror handler)"
        )


# ── SEC-19 · db_path removed from dedup/sources ──────────────────────────────


class TestSEC19DedupNoPaths:
    """GET /dedup/sources must not expose absolute filesystem paths (SEC-19)."""

    def test_list_dedup_sources_no_db_path_field(self):
        """dedup.py list_dedup_sources() returns no db_path key."""
        dedup_path = _REPO / "taxspine_orchestrator" / "dedup.py"
        src = dedup_path.read_text(encoding="utf-8")

        # Find list_dedup_sources function body
        fn_start = src.find("def list_dedup_sources(")
        assert fn_start != -1
        next_fn = src.find("\n@router.", fn_start + 1)
        fn_body = src[fn_start:next_fn] if next_fn != -1 else src[fn_start:]

        assert '"db_path"' not in fn_body, (
            "list_dedup_sources() must not return db_path — "
            "it exposes absolute server filesystem paths (SEC-19)"
        )

    def test_get_dedup_summary_no_db_path_field(self):
        """get_dedup_summary() returns no db_path key in its response dict."""
        dedup_path = _REPO / "taxspine_orchestrator" / "dedup.py"
        src = dedup_path.read_text(encoding="utf-8")

        fn_start = src.find("def get_dedup_summary(")
        assert fn_start != -1
        next_fn = src.find("\ndef ", fn_start + 1)
        fn_body = src[fn_start:next_fn] if next_fn != -1 else src[fn_start:]

        # The return dict should not contain "db_path" key
        assert '"db_path"' not in fn_body, (
            "get_dedup_summary() must not return db_path — "
            "it exposes absolute server filesystem paths (SEC-19)"
        )

    @pytest.fixture(autouse=True)
    def _client(self):
        from taxspine_orchestrator.main import app
        self.client = TestClient(app)

    def test_sources_endpoint_response_has_no_db_path(self, tmp_path, monkeypatch):
        """GET /dedup/sources response items contain no db_path key."""
        from taxspine_orchestrator import config as cfg
        monkeypatch.setattr(cfg.settings, "DEDUP_DIR", tmp_path)

        # Create a fake .db file so the endpoint returns something.
        (tmp_path / "test_source.db").write_bytes(b"")

        resp = self.client.get("/dedup/sources")
        assert resp.status_code == 200
        for item in resp.json():
            assert "db_path" not in item, (
                f"Response item contains db_path: {item}"
            )


# ── FE-10 · loadAlerts r.ok check ────────────────────────────────────────────


class TestFE10LoadAlertsRokCheck:
    """loadAlerts() must check r.ok before parsing JSON (FE-10)."""

    def test_load_alerts_has_rok_check(self):
        """loadAlerts() checks r.ok before calling .json()."""
        html = _html()
        fn_start = html.find("async function loadAlerts()")
        assert fn_start != -1, "loadAlerts() function not found"

        # Find the end of the function (next async function declaration)
        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 800]

        assert "r.ok" in fn_body, (
            "loadAlerts() must check r.ok before parsing JSON body (FE-10)"
        )

    def test_load_alerts_throws_on_error_status(self):
        """loadAlerts() throws (doesn't silently show 'all clear') on error response."""
        html = _html()
        fn_start = html.find("async function loadAlerts()")
        assert fn_start != -1
        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 800]

        # Must throw an error when r.ok is false
        assert "throw new Error" in fn_body or "throw Error" in fn_body, (
            "loadAlerts() must throw when !r.ok to surface server failures (FE-10)"
        )

    def test_load_alerts_no_bare_json_on_await(self):
        """loadAlerts() must not call .json() without first checking r.ok."""
        html = _html()
        fn_start = html.find("async function loadAlerts()")
        assert fn_start != -1
        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 800]

        # The old pattern was: `await (await fetch(...)).json()` — this bypasses r.ok
        assert "(await fetch(" not in fn_body or ".json()" not in fn_body.split("r.ok")[0], (
            "loadAlerts() must check r.ok before calling .json() — "
            "one-liner fetch().json() pattern bypasses the check (FE-10)"
        )


# ── FE-11 · openResultsById error display ────────────────────────────────────


class TestFE11OpenResultsByIdError:
    """openResultsById shows an error in the results panel on failure (FE-11)."""

    def test_results_panel_shown_on_error(self):
        """openResultsById makes results-panel visible on error status."""
        html = _html()
        fn_start = html.find("async function openResultsById(")
        assert fn_start != -1

        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 1000]

        assert "results-panel" in fn_body, (
            "openResultsById() must reference results-panel to show errors (FE-11)"
        )

    def test_error_text_rendered_on_bad_status(self):
        """openResultsById injects error HTML when !r.ok."""
        html = _html()
        fn_start = html.find("async function openResultsById(")
        assert fn_start != -1
        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 1000]

        # Must set innerHTML to an error message rather than silently returning
        assert "innerHTML" in fn_body, (
            "openResultsById() must set innerHTML to display the error (FE-11)"
        )

    def test_catch_block_shows_error(self):
        """openResultsById shows an error message on network failure too."""
        html = _html()
        fn_start = html.find("async function openResultsById(")
        assert fn_start != -1
        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 1000]

        # The catch block should not be empty `catch {}`
        assert "catch {}" not in fn_body, (
            "openResultsById() must not have an empty catch block (FE-11)"
        )


# ── FE-12 · dry_run reset after success ──────────────────────────────────────


class TestFE12DryRunReset:
    """dry_run checkbox is unchecked after a successful runReport submission (FE-12)."""

    def test_run_dry_unchecked_after_success(self):
        """runReport() unchecks run-dry after successJob is set."""
        html = _html()
        fn_start = html.find("async function runReport()")
        assert fn_start != -1

        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 3000]

        assert "run-dry" in fn_body, (
            "runReport() must reference run-dry to uncheck it (FE-12)"
        )
        assert "checked = false" in fn_body, (
            "runReport() must set .checked = false on the dry-run checkbox (FE-12)"
        )

    def test_reset_happens_in_success_branch(self):
        """The dry_run reset is inside the successJob branch (not before submission)."""
        html = _html()
        fn_start = html.find("async function runReport()")
        assert fn_start != -1
        next_fn = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:next_fn] if next_fn != -1 else html[fn_start:fn_start + 3000]

        # The reset must appear after successJob is set, not before the fetch
        success_pos = fn_body.find("successJob")
        reset_pos   = fn_body.find("checked = false")
        assert success_pos != -1
        assert reset_pos != -1
        assert reset_pos > success_pos, (
            "dry_run reset must occur after successJob is set — "
            "it should only happen on success (FE-12)"
        )
