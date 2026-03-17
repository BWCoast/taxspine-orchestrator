"""Batch 14 — regression tests for MEDIUM UI/frontend findings.

Findings covered
----------------
FE-03  Drop zone inside <label> causes double file dialog
FE-04  Inconsistent escaping in job action handlers (onclick vs data-job-id)
FE-13  API constant assumes same-origin root — breaks behind reverse proxy
UX-02  Alert severity emoji icons lack accessible labels
UX-03  Dummy valuation warning not announced to screen readers
UX-04  Price table path field shows server-side path without context
UX-05  Status badges use decorative symbols without screen-reader alternatives
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── shared helpers ────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_REPO = _HERE.parent
_HTML_PATH = _REPO / "ui" / "index.html"


def _html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


# ── FE-03: no double file dialog ──────────────────────────────────────────────


class TestFE03NoDoubleFileDialog:
    """FE-03: drop zone must not call fileInput.click() — the enclosing <label>
    already triggers the file picker on click."""

    def test_explicit_click_handler_removed(self):
        """The redundant fileInput.click() listener must be absent."""
        src = _html()
        assert "dropZone.addEventListener('click', () => fileInput.click())" not in src, (
            "FE-03: dropZone.addEventListener('click', ...) must be removed — "
            "the <label> wrapper already opens the file dialog on click"
        )

    def test_fe03_comment_present(self):
        """A FE-03 explanatory comment must document why the listener was removed."""
        src = _html()
        assert "FE-03" in src, (
            "FE-03 comment must be present in index.html explaining the removal"
        )

    def test_drop_zone_still_handles_drag_events(self):
        """dragover, dragleave, and drop event handlers must still be present."""
        src = _html()
        assert "dropZone.addEventListener('dragover'" in src
        assert "dropZone.addEventListener('dragleave'" in src
        assert "dropZone.addEventListener('drop'" in src

    def test_label_wraps_file_input(self):
        """The <label> element must still wrap the file input (native click works)."""
        src = _html()
        assert 'label class="block cursor-pointer"' in src or \
               "label class='block cursor-pointer'" in src or \
               '<label class="block cursor-pointer">' in src, (
            "The <label> wrapping the file input must be present for FE-03 fix to work"
        )


# ── FE-04: data-job-id attribute escaping ─────────────────────────────────────


class TestFE04DataJobIdEscaping:
    """FE-04: job action handlers must use data-job-id attributes, not inline
    ID strings embedded in onclick attribute values."""

    def test_job_row_has_data_job_id_attribute(self):
        """The job row div must use data-job-id instead of inline escHtml(j.id)."""
        src = _html()
        assert 'data-job-id="${safeId}"' in src, (
            "FE-04: job row must use data-job-id=\"${safeId}\" attribute"
        )

    def test_onclick_reads_from_dataset(self):
        """onclick handlers must read from this.dataset.jobId, not from arg."""
        src = _html()
        assert "this.dataset.jobId" in src, (
            "FE-04: onclick handlers must read job ID from this.dataset.jobId"
        )

    def test_cancel_button_uses_data_job_id(self):
        """Cancel button must carry data-job-id and pass dataset.jobId to handler."""
        src = _html()
        assert "cancelJob(this.dataset.jobId)" in src, (
            "FE-04: cancelJob must be called with this.dataset.jobId"
        )

    def test_delete_button_uses_data_job_id(self):
        """Delete button must carry data-job-id and pass dataset.jobId to handler."""
        src = _html()
        assert "deleteJob(this.dataset.jobId)" in src, (
            "FE-04: deleteJob must be called with this.dataset.jobId"
        )

    def test_open_results_uses_data_job_id(self):
        """Row click handler must call openResultsById with this.dataset.jobId."""
        src = _html()
        assert "openResultsById(this.dataset.jobId)" in src, (
            "FE-04: openResultsById must be called with this.dataset.jobId on row click"
        )

    def test_fe04_comment_present(self):
        """A FE-04 comment must explain the data-job-id pattern."""
        src = _html()
        assert "FE-04" in src


# ── FE-13: API base meta tag ──────────────────────────────────────────────────


class TestFE13ApiBaseMeta:
    """FE-13: API constant must support a configurable base URL via <meta> tag."""

    def test_meta_api_base_tag_present(self):
        """<meta name=\"api-base\"> must be present in the <head>."""
        src = _html()
        assert 'name="api-base"' in src, (
            "FE-13: <meta name=\"api-base\"> must be present in <head>"
        )

    def test_meta_api_base_default_is_empty(self):
        """Default content must be empty string (same-origin root)."""
        src = _html()
        assert 'name="api-base" content=""' in src, (
            "FE-13: meta api-base must default to content=\"\" for same-origin deployments"
        )

    def test_js_reads_meta_api_base(self):
        """JS must read the meta tag to set the API constant."""
        src = _html()
        assert "querySelector('meta[name=\"api-base\"]')" in src or \
               'querySelector(\'meta[name="api-base"]\')' in src, (
            "FE-13: JS must read meta[name=\"api-base\"] content for the API base URL"
        )

    def test_api_constant_uses_meta_with_fallback(self):
        """API constant must use meta content with file:// fallback."""
        src = _html()
        assert "meta[name=\"api-base\"]" in src or 'meta[name="api-base"]' in src
        # The old hardcoded pattern should no longer be the sole definition
        assert "location.protocol === 'file:'" in src, (
            "FE-13: file:// fallback must still be present for local dev"
        )

    def test_fe13_comment_present(self):
        """A FE-13 comment must explain the meta tag mechanism."""
        src = _html()
        assert "FE-13" in src


# ── UX-02: alert severity emoji aria-label ────────────────────────────────────


class TestUX02AlertEmojiAccessibility:
    """UX-02: alert severity icons must have role=img and aria-label."""

    def test_alert_icon_has_role_img(self):
        """Alert icon span must have role=\"img\"."""
        src = _html()
        assert 'role="img"' in src, (
            "UX-02: alert icon span must have role=\"img\""
        )

    def test_alert_icon_has_aria_label(self):
        """Alert icon span must have an aria-label attribute."""
        src = _html()
        assert 'aria-label="${iconLabel}"' in src, (
            "UX-02: alert icon span must have aria-label=\"${iconLabel}\""
        )

    def test_severity_to_label_map_present(self):
        """A mapping from severity to human label must exist in the JS."""
        src = _html()
        assert "critical" in src, "UX-02: 'critical' label must be mapped for error severity"
        assert "warning" in src, "UX-02: 'warning' label must be mapped for warn severity"
        assert "information" in src, "UX-02: 'information' label must be mapped for info severity"

    def test_ux02_comment_present(self):
        """A UX-02 comment must document the accessibility fix."""
        src = _html()
        assert "UX-02" in src


# ── UX-03: dummy valuation warning aria-live ──────────────────────────────────


class TestUX03DummyWarningAriaLive:
    """UX-03: dummy-valuation-warning must have aria-live and aria-atomic."""

    def test_dummy_warning_has_aria_live_polite(self):
        """The dummy-valuation-warning div must have aria-live=\"polite\"."""
        src = _html()
        assert 'aria-live="polite"' in src, (
            "UX-03: dummy-valuation-warning must have aria-live=\"polite\""
        )

    def test_dummy_warning_has_aria_atomic_true(self):
        """The dummy-valuation-warning div must have aria-atomic=\"true\"."""
        src = _html()
        assert 'aria-atomic="true"' in src, (
            "UX-03: dummy-valuation-warning must have aria-atomic=\"true\""
        )

    def test_aria_live_on_warning_element(self):
        """aria-live must be on the same element as the dummy-valuation-warning id."""
        src = _html()
        # The warning div contains both the id and aria-live on the same line/element
        warning_section = src[src.find("dummy-valuation-warning"):
                               src.find("dummy-valuation-warning") + 300]
        assert "aria-live" in warning_section, (
            "UX-03: aria-live must be on the dummy-valuation-warning element itself"
        )

    def test_ux03_comment_present(self):
        """A UX-03 comment must be present."""
        src = _html()
        assert "UX-03" in src


# ── UX-04: price path helper text ────────────────────────────────────────────


class TestUX04PricePathHelperText:
    """UX-04: the price table path field must display helper text explaining
    that it shows a server-side path."""

    def test_server_side_path_helper_text_present(self):
        """Helper text must say 'Server-side path'."""
        src = _html()
        assert "Server-side path" in src, (
            "UX-04: helper text 'Server-side path' must appear below the price path input"
        )

    def test_helper_text_near_prices_path_input(self):
        """Helper text must be near the run-prices-path input element."""
        src = _html()
        input_pos = src.find("run-prices-path")
        helper_pos = src.find("Server-side path")
        assert input_pos > 0 and helper_pos > 0, (
            "Both run-prices-path and helper text must be present"
        )
        # Helper text should be within 1000 chars of the input element
        assert abs(helper_pos - input_pos) < 1000, (
            "UX-04: 'Server-side path' helper text must be close to the price path input"
        )

    def test_ux04_comment_present(self):
        """A UX-04 comment must be present."""
        src = _html()
        assert "UX-04" in src


# ── UX-05: status badge aria-hidden symbols ───────────────────────────────────


class TestUX05BadgeAriaHidden:
    """UX-05: decorative badge symbols (●, ▶, ✓, ✗) must be aria-hidden."""

    def test_badge_symbol_has_aria_hidden(self):
        """The symbol span inside each badge must have aria-hidden=\"true\"."""
        src = _html()
        assert 'aria-hidden="true"' in src, (
            "UX-05: badge symbol span must have aria-hidden=\"true\""
        )

    def test_badge_symbol_wrapped_in_span(self):
        """Symbols must be wrapped in a dedicated <span aria-hidden=\"true\">."""
        src = _html()
        assert '<span aria-hidden="true">' in src, (
            "UX-05: symbols must be in <span aria-hidden=\"true\">"
        )

    def test_badge_text_separate_from_symbol(self):
        """Badge label text must be separate from the symbol in the template."""
        src = _html()
        # The new template uses entry.sym and entry.text separately
        assert "entry.sym" in src and "entry.text" in src, (
            "UX-05: badgeHtml map must have separate sym and text fields"
        )

    def test_cancelled_badge_present(self):
        """CANCELLED status must be in the badge map (bonus fix)."""
        src = _html()
        assert "cancelled" in src, (
            "UX-05: CANCELLED status should be in the badge map"
        )

    def test_ux05_comment_present(self):
        """A UX-05 comment must be present."""
        src = _html()
        assert "UX-05" in src
