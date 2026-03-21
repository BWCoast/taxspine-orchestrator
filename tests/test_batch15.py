"""Batch 15 — regression tests for MEDIUM UI/frontend and API findings.

Findings covered
----------------
UX-18  Run overlay has no dismiss/abort mechanism
UX-19  Download buttons lack visual hierarchy (primary vs secondary)
UX-20  Tax Center tabs lack ARIA roles (tablist / tab / tabpanel)
API-22 Several endpoints lack response_model= (no OpenAPI schema)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── shared helpers ────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_REPO = _HERE.parent
_HTML_PATH = _REPO / "ui" / "index.html"
_MAIN_PATH = _REPO / "taxspine_orchestrator" / "main.py"
_MODELS_PATH = _REPO / "taxspine_orchestrator" / "models.py"


def _html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


def _main() -> str:
    return _MAIN_PATH.read_text(encoding="utf-8")


def _models() -> str:
    return _MODELS_PATH.read_text(encoding="utf-8")


# ── UX-18: AbortController + Dismiss button ───────────────────────────────────


class TestUX18DismissButton:
    """UX-18: the run overlay must have a Dismiss button that aborts the
    in-flight fetch via AbortController and re-enables the form."""

    def test_dismiss_button_in_overlay(self):
        """A Dismiss button must exist inside the run-overlay div."""
        src = _html()
        assert "Dismiss" in src, (
            "UX-18: 'Dismiss' button must be inside the run-overlay"
        )

    def test_overlay_dismiss_btn_id(self):
        """The dismiss button must have id='overlay-dismiss-btn'."""
        src = _html()
        assert "overlay-dismiss-btn" in src, (
            "UX-18: dismiss button must carry id='overlay-dismiss-btn'"
        )

    def test_dismiss_button_calls_dismissrun(self):
        """The Dismiss button must call dismissRun() on click."""
        src = _html()
        assert "onclick=\"dismissRun()\"" in src or "onclick='dismissRun()'" in src, (
            "UX-18: Dismiss button must call dismissRun() via onclick"
        )

    def test_dismissrun_function_defined(self):
        """dismissRun() function must be defined in the JS."""
        src = _html()
        assert "function dismissRun()" in src, (
            "UX-18: dismissRun() function must be defined"
        )

    def test_dismissrun_aborts_controller(self):
        """dismissRun() must call abort() on _runController."""
        src = _html()
        assert "_runController.abort()" in src, (
            "UX-18: dismissRun() must call _runController.abort()"
        )

    def test_run_controller_module_level_variable(self):
        """A module-level _runController variable must be declared."""
        src = _html()
        assert "_runController = null" in src, (
            "UX-18: let _runController = null must be declared at module scope"
        )

    def test_abort_controller_created_in_run_report(self):
        """runReport() must create a new AbortController before the fetch."""
        src = _html()
        assert "new AbortController()" in src, (
            "UX-18: runReport() must instantiate AbortController"
        )

    def test_signal_passed_to_fetch(self):
        """The AbortController signal must be passed to the fetch() call."""
        src = _html()
        assert "signal: _runController.signal" in src, (
            "UX-18: fetch() must receive signal: _runController.signal"
        )

    def test_abort_timeout_present(self):
        """A setTimeout-based 120-second automatic abort must be present."""
        src = _html()
        assert "120" in src and "_runController.abort()" in src, (
            "UX-18: automatic 120 s abort timeout must be set"
        )
        assert "setTimeout" in src, (
            "UX-18: setTimeout must be used for the automatic abort"
        )

    def test_abort_error_not_shown_as_alert(self):
        """AbortError must be caught silently (no 'server down' alert)."""
        src = _html()
        assert "AbortError" in src, (
            "UX-18: AbortError must be explicitly checked to suppress the "
            "'server down' alert when the user dismisses"
        )

    def test_clear_timeout_in_finally(self):
        """clearTimeout must be called in the finally block to avoid leaks."""
        src = _html()
        assert "clearTimeout" in src, (
            "UX-18: clearTimeout must be called in finally to cancel pending abort timer"
        )

    def test_run_controller_reset_to_null_in_finally(self):
        """_runController must be reset to null in the finally block."""
        src = _html()
        # The finally block sets _runController = null after the request
        assert "_runController = null" in src, (
            "UX-18: _runController must be reset to null in the finally block"
        )

    def test_ux18_comment_present(self):
        """A UX-18 comment must document the change."""
        src = _html()
        assert "UX-18" in src, (
            "UX-18 comment must be present in index.html"
        )


# ── UX-19: Download button visual hierarchy ───────────────────────────────────


class TestUX19DownloadButtonHierarchy:
    """UX-19: RF-1159 and HTML report download buttons must be btn-primary;
    all other file-download buttons (gains, wealth, summary, log, review)
    must be btn-secondary."""

    def test_btn_primary_present_in_download_section(self):
        """btn-primary must appear in the file-download / results area."""
        src = _html()
        assert "btn-primary" in src, (
            "UX-19: btn-primary class must be used for high-priority downloads"
        )

    def test_btn_secondary_present_in_download_section(self):
        """btn-secondary must appear for lower-priority downloads."""
        src = _html()
        assert "btn-secondary" in src, (
            "UX-19: btn-secondary class must be used for lower-priority downloads"
        )

    def test_rf1159_uses_primary(self):
        """The RF-1159 download must be rendered as btn-primary."""
        src = _html()
        assert "primaryKinds" in src or "rf1159" in src, (
            "UX-19: rf1159 kind must be identified as a primary download"
        )
        # primaryKinds set must reference rf1159
        assert "'rf1159'" in src or '"rf1159"' in src, (
            "UX-19: rf1159 key must appear in the primary kinds collection"
        )

    def test_format_badge_present(self):
        """A formatBadge (or equivalent) map must list file format labels."""
        src = _html()
        assert "formatBadge" in src or "format badge" in src.lower() or (
            "JSON" in src and "CSV" in src and "TXT" in src
        ), (
            "UX-19: format labels (CSV/JSON/TXT) must appear in download buttons"
        )

    def test_ux19_comment_present(self):
        """A UX-19 comment must document the hierarchy change."""
        src = _html()
        assert "UX-19" in src


# ── UX-20: Tax Center tab ARIA roles ─────────────────────────────────────────


class TestUX20TaxCenterTabAria:
    """UX-20: Tax Center tabs must use ARIA tablist/tab/tabpanel roles."""

    def test_tablist_role_present(self):
        """A role=\"tablist\" container must wrap the tab buttons."""
        src = _html()
        assert 'role="tablist"' in src, (
            "UX-20: tab container must have role=\"tablist\""
        )

    def test_tab_role_on_buttons(self):
        """Each tab button must have role=\"tab\"."""
        src = _html()
        assert 'role="tab"' in src, (
            "UX-20: tab buttons must have role=\"tab\""
        )

    def test_tabpanel_role_on_panels(self):
        """Each panel must have role=\"tabpanel\"."""
        src = _html()
        assert 'role="tabpanel"' in src, (
            "UX-20: panels must have role=\"tabpanel\""
        )

    def test_aria_selected_on_active_tab(self):
        """The active tab button must set aria-selected=\"true\"."""
        src = _html()
        assert 'aria-selected="true"' in src, (
            "UX-20: active tab must have aria-selected=\"true\""
        )

    def test_aria_selected_false_on_inactive_tabs(self):
        """Inactive tab buttons must have aria-selected=\"false\"."""
        src = _html()
        assert 'aria-selected="false"' in src, (
            "UX-20: inactive tabs must have aria-selected=\"false\""
        )

    def test_aria_controls_on_tabs(self):
        """Tab buttons must reference their panels via aria-controls."""
        src = _html()
        assert "aria-controls=" in src, (
            "UX-20: tab buttons must have aria-controls pointing to panel IDs"
        )

    def test_aria_labelledby_on_panels(self):
        """Panels must reference their tab buttons via aria-labelledby."""
        src = _html()
        assert "aria-labelledby=" in src, (
            "UX-20: tabpanel elements must have aria-labelledby referencing the tab"
        )

    def test_tctab_function_sets_aria_selected(self):
        """tcTab() must update aria-selected on the buttons."""
        src = _html()
        assert "aria-selected" in src, (
            "UX-20: tcTab() must manage aria-selected on tab switches"
        )
        # The function should set ariaSelected or setAttribute
        assert "ariaSelected" in src or "setAttribute" in src or "aria-selected" in src, (
            "UX-20: tcTab() must programmatically set aria-selected"
        )

    def test_tab_ids_present(self):
        """Each tab must have an id='tc-tab-*' so panels can label themselves."""
        src = _html()
        assert "tc-tab-holdings" in src, "UX-20: tc-tab-holdings id must be present"
        assert "tc-tab-lots" in src, "UX-20: tc-tab-lots id must be present"
        assert "tc-tab-dedup" in src, "UX-20: tc-tab-dedup id must be present"

    def test_tablist_aria_label(self):
        """The tablist must have an aria-label describing the tab group."""
        src = _html()
        assert 'aria-label=' in src, (
            "UX-20: role=\"tablist\" should have an aria-label"
        )

    def test_ux20_comment_present(self):
        """A UX-20 comment must document the ARIA change."""
        src = _html()
        assert "UX-20" in src


# ── API-22: response_model= on previously-untyped routes ─────────────────────


class TestAPI22ResponseModels:
    """API-22: /start, /cancel, DELETE /jobs, and /review must declare
    response_model= so OpenAPI generates accurate schema."""

    def test_start_route_has_response_model(self):
        """POST /jobs/{id}/start must declare response_model=StartJobResponse."""
        src = _main()
        assert "response_model=StartJobResponse" in src, (
            "API-22: /start route must have response_model=StartJobResponse"
        )

    def test_cancel_route_has_response_model(self):
        """POST /jobs/{id}/cancel must declare response_model=CancelledJobResponse."""
        src = _main()
        assert "response_model=CancelledJobResponse" in src, (
            "API-22: /cancel route must have response_model=CancelledJobResponse"
        )

    def test_delete_route_has_response_model(self):
        """DELETE /jobs/{id} must declare response_model=DeletedJobResponse."""
        src = _main()
        assert "response_model=DeletedJobResponse" in src, (
            "API-22: DELETE /jobs route must have response_model=DeletedJobResponse"
        )

    def test_review_route_has_response_model(self):
        """GET /jobs/{id}/review must declare response_model=JobReviewResponse."""
        src = _main()
        assert "response_model=JobReviewResponse" in src, (
            "API-22: /review route must have response_model=JobReviewResponse"
        )

    def test_start_job_response_model_defined(self):
        """StartJobResponse Pydantic model must be defined in models.py."""
        src = _models()
        assert "class StartJobResponse" in src, (
            "API-22: StartJobResponse must be a Pydantic BaseModel in models.py"
        )

    def test_cancelled_job_response_model_defined(self):
        """CancelledJobResponse Pydantic model must be defined in models.py."""
        src = _models()
        assert "class CancelledJobResponse" in src, (
            "API-22: CancelledJobResponse must be a Pydantic BaseModel in models.py"
        )

    def test_deleted_job_response_model_defined(self):
        """DeletedJobResponse Pydantic model must be defined in models.py."""
        src = _models()
        assert "class DeletedJobResponse" in src, (
            "API-22: DeletedJobResponse must be a Pydantic BaseModel in models.py"
        )

    def test_job_review_response_model_defined(self):
        """JobReviewResponse Pydantic model must be defined in models.py."""
        src = _models()
        assert "class JobReviewResponse" in src, (
            "API-22: JobReviewResponse must be a Pydantic BaseModel in models.py"
        )

    def test_start_job_response_has_status_and_job_id(self):
        """StartJobResponse must have status and job_id fields."""
        src = _models()
        idx = src.find("class StartJobResponse")
        snippet = src[idx:idx + 300]
        assert "status" in snippet and "job_id" in snippet, (
            "API-22: StartJobResponse must have status and job_id fields"
        )

    def test_deleted_job_response_has_files_removed(self):
        """DeletedJobResponse must have files_removed field."""
        src = _models()
        assert "files_removed" in src, (
            "API-22: DeletedJobResponse must have files_removed: int field"
        )

    def test_job_review_response_has_required_fields(self):
        """JobReviewResponse must have has_unlinked_transfers, warning_count,
        warnings, clean, source_count."""
        src = _models()
        idx = src.find("class JobReviewResponse")
        snippet = src[idx:idx + 700]
        assert "has_unlinked_transfers" in snippet, (
            "API-22: JobReviewResponse must have has_unlinked_transfers"
        )
        assert "warning_count" in snippet, (
            "API-22: JobReviewResponse must have warning_count"
        )
        assert "warnings" in snippet, (
            "API-22: JobReviewResponse must have warnings"
        )
        assert "clean" in snippet, (
            "API-22: JobReviewResponse must have clean"
        )
        assert "source_count" in snippet, (
            "API-22: JobReviewResponse must have source_count"
        )

    def test_response_models_imported_in_main(self):
        """All four response models must be imported in main.py."""
        src = _main()
        assert "StartJobResponse" in src
        assert "CancelledJobResponse" in src
        assert "DeletedJobResponse" in src
        assert "JobReviewResponse" in src

    def test_api22_comment_present(self):
        """An API-22 comment must be present somewhere in main.py or models.py."""
        combined = _main() + _models()
        assert "API-22" in combined, (
            "API-22 comment must be present in main.py or models.py"
        )
