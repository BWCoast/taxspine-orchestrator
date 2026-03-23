"""test_dashboard_phase3.py — Phase 3 dashboard UI structure tests.

Validates that the new Review Queue tab, Audit Log tab, and Diagnostics panel
are present in the single-file HTML dashboard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


def _html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


# ── TestReviewQueueTab ────────────────────────────────────────────────────────


class TestReviewQueueTab:
    """Review Queue tab exists with correct ARIA attributes and content."""

    def test_tab_button_present(self):
        assert 'id="tc-tab-review"' in _html()

    def test_tab_button_has_aria_controls(self):
        assert 'aria-controls="tc-panel-review"' in _html()

    def test_tab_panel_present(self):
        assert 'id="tc-panel-review"' in _html()

    def test_panel_has_role_tabpanel(self):
        html = _html()
        # The panel div must have role="tabpanel"
        panel_idx = html.index('id="tc-panel-review"')
        # Look back for role=tabpanel within the same tag
        tag_start = html.rfind('<div', 0, panel_idx)
        tag_end   = html.index('>', tag_start)
        assert 'role="tabpanel"' in html[tag_start:tag_end]

    def test_panel_has_stats_row(self):
        assert 'id="tc-review-stats"' in _html()

    def test_panel_has_banner(self):
        assert 'id="tc-review-banner"' in _html()

    def test_panel_has_unlinked_section(self):
        assert 'id="tc-rv-unlinked-section"' in _html()

    def test_panel_has_missing_basis_section(self):
        assert 'id="tc-rv-missing-section"' in _html()

    def test_panel_has_warnings_section(self):
        assert 'id="tc-rv-warnings-section"' in _html()

    def test_panel_has_empty_state(self):
        assert 'id="tc-review-empty"' in _html()


# ── TestAuditLogTab ───────────────────────────────────────────────────────────


class TestAuditLogTab:
    """Audit Log tab exists with correct ARIA attributes and table structure."""

    def test_tab_button_present(self):
        assert 'id="tc-tab-audit"' in _html()

    def test_tab_button_has_aria_controls(self):
        assert 'aria-controls="tc-panel-audit"' in _html()

    def test_tab_panel_present(self):
        assert 'id="tc-panel-audit"' in _html()

    def test_audit_table_present(self):
        assert 'id="tc-audit-table"' in _html()

    def test_audit_tbody_present(self):
        assert 'id="tc-audit-tbody"' in _html()

    def test_audit_empty_state_present(self):
        assert 'id="tc-audit-empty"' in _html()

    def test_audit_loading_indicator_present(self):
        assert 'id="tc-audit-loading"' in _html()


# ── TestDiagnosticsPanel ──────────────────────────────────────────────────────


class TestDiagnosticsPanel:
    """Diagnostics collapsible panel structure."""

    def test_details_element_present(self):
        assert 'id="diagnostics-details"' in _html()

    def test_has_lots_section(self):
        assert 'id="diag-lots"' in _html()

    def test_has_prices_section(self):
        assert 'id="diag-prices"' in _html()

    def test_has_jobs_section(self):
        assert 'id="diag-jobs"' in _html()

    def test_has_dedup_section(self):
        assert 'id="diag-dedup"' in _html()

    def test_summary_line_present(self):
        assert 'id="diag-summary-line"' in _html()


# ── TestTablistFlexWrap ────────────────────────────────────────────────────────


class TestTablistFlexWrap:
    """Tablist uses flex-wrap so 5 tabs don't overflow on narrow screens.

    UX-06: a @media query now also adds overflow-x:auto + flex-wrap:nowrap on
    narrow viewports.  The HTML element still carries 'flex-wrap' in its class
    for wide viewports; the CSS override handles mobile.
    """

    def test_tablist_has_flex_wrap(self):
        html = _html()
        # Skip past the <style> block so we find the HTML element, not the CSS
        # selector "[role=\"tablist\"]" that was added for the UX-06 media query.
        style_end = html.index('</style>')
        tablist_idx = html.index('role="tablist"', style_end)
        # Find the opening tag containing this attribute
        tag_start = html.rfind('<div', 0, tablist_idx)
        tag_end   = html.index('>', tag_start)
        assert 'flex-wrap' in html[tag_start:tag_end]

    def test_mobile_tablist_has_scroll_css(self):
        """UX-06: media query provides horizontal scroll on narrow viewports."""
        html = _html()
        assert 'overflow-x:auto' in html or 'overflow-x: auto' in html
        assert '@media' in html


# ── TestJsHooks ───────────────────────────────────────────────────────────────


class TestJsHooks:
    """Critical JS functions exist in the dashboard script."""

    def test_load_review_queue_function_defined(self):
        assert 'function loadReviewQueue' in _html() or 'async function loadReviewQueue' in _html()

    def test_load_audit_log_function_defined(self):
        assert 'function loadAuditLog' in _html() or 'async function loadAuditLog' in _html()

    def test_load_diagnostics_function_defined(self):
        assert 'function loadDiagnostics' in _html() or 'async function loadDiagnostics' in _html()

    def test_render_review_queue_function_defined(self):
        assert '_renderReviewQueue' in _html()

    def test_render_diagnostics_function_defined(self):
        assert '_renderDiagnostics' in _html()

    def test_show_review_empty_function_defined(self):
        assert '_showReviewEmpty' in _html()

    def test_show_audit_empty_function_defined(self):
        assert '_showAuditEmpty' in _html()

    def test_on_tc_year_change_calls_review_queue(self):
        """onTcYearChange must call loadReviewQueue so switching years refreshes the queue."""
        html = _html()
        fn_start = html.index('function onTcYearChange')
        fn_end   = html.index('}', fn_start)
        fn_body  = html[fn_start:fn_end]
        assert 'loadReviewQueue' in fn_body

    def test_init_calls_load_diagnostics(self):
        """The init IIFE must call loadDiagnostics() on page load."""
        # Find the init IIFE (last set of setInterval calls)
        html = _html()
        init_idx = html.rindex('window._jobsTimer')
        relevant = html[max(0, init_idx - 2000):init_idx + 500]
        assert 'loadDiagnostics' in relevant
