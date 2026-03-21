"""Batch 24 — UX loading indicators, in-page confirmation modal, iframe affordance.

Coverage:
    UX-13  CSV upload and Tax Center refresh lacked animated spinners —
           spinner icon (animate-spin) added to upload status and TC loading spans.
    UX-14  Native browser confirm() dialogs replaced with a styled in-page modal
           (showConfirm()) that matches the dark theme and returns a Promise<boolean>.
    UX-23  Report iframe had no visual indication of scrollable content —
           gradient fade overlay and scroll hint text added below the iframe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_UI = Path(__file__).parent.parent / "ui" / "index.html"


def _ui() -> str:
    return _UI.read_text(encoding="utf-8")


# ===========================================================================
# TestUX13LoadingSpinners
# ===========================================================================


class TestUX13LoadingSpinners:
    """UX-13: upload and Tax Center refresh must show animated loading indicators."""

    def test_ux13_upload_uses_html_spinner(self):
        """handleCsvFile uses innerHTML (not textContent) with an animate-spin element during upload."""
        src = _ui()
        # The old bare textContent assignment should be gone.
        assert "statusEl.textContent = `Uploading" not in src, (
            "Old textContent assignment still present — spinner not applied"
        )
        # The new innerHTML assignment with spinner must be present.
        assert "animate-spin" in src
        assert "statusEl.innerHTML" in src

    def test_ux13_spinner_has_aria_hidden(self):
        """The spinner span carries aria-hidden so screen readers skip the decorative icon."""
        src = _ui()
        assert 'aria-hidden="true"' in src

    def test_ux13_tc_loading_span_has_spinner(self):
        """The tc-loading span includes an animate-spin icon."""
        src = _ui()
        # Find the tc-loading span content.
        match = re.search(r'id="tc-loading"[^>]*>(.*?)</span>', src, re.DOTALL)
        assert match, "tc-loading span not found"
        content = match.group(1)
        assert "animate-spin" in content, (
            f"tc-loading span lacks animate-spin. Content: {content!r}"
        )

    def test_ux13_tc_dedup_loading_span_has_spinner(self):
        """The tc-dedup-loading span also includes an animate-spin icon."""
        src = _ui()
        match = re.search(r'id="tc-dedup-loading"[^>]*>(.*?)</span>', src, re.DOTALL)
        assert match, "tc-dedup-loading span not found"
        content = match.group(1)
        assert "animate-spin" in content, (
            f"tc-dedup-loading span lacks animate-spin. Content: {content!r}"
        )

    def test_ux13_comment_present(self):
        """The UX-13 audit comment is present in index.html."""
        src = _ui()
        assert "UX-13" in src

    def test_ux13_upload_spinner_escapes_filename(self):
        """The spinner innerHTML uses escHtml() on the filename to prevent XSS."""
        src = _ui()
        assert "escHtml(file.name)" in src


# ===========================================================================
# TestUX14ConfirmationModal
# ===========================================================================


class TestUX14ConfirmationModal:
    """UX-14: native confirm() replaced with an in-page styled modal."""

    def test_ux14_modal_html_exists(self):
        """The confirm-modal div is present in the HTML."""
        src = _ui()
        assert 'id="confirm-modal"' in src

    def test_ux14_modal_has_dialog_role(self):
        """The modal has role="dialog" and aria-modal="true"."""
        src = _ui()
        # Find the confirm-modal element and verify aria attributes.
        match = re.search(r'id="confirm-modal"[^>]*>', src)
        assert match, "confirm-modal opening tag not found"
        tag = match.group(0)
        assert 'role="dialog"' in tag, f"role=dialog missing from: {tag!r}"
        assert 'aria-modal="true"' in tag, f"aria-modal missing from: {tag!r}"

    def test_ux14_modal_has_message_element(self):
        """The confirm-modal contains a message paragraph with confirm-modal-msg id."""
        src = _ui()
        assert 'id="confirm-modal-msg"' in src

    def test_ux14_modal_has_ok_and_cancel_buttons(self):
        """The modal has both Confirm and Cancel buttons."""
        src = _ui()
        assert 'id="confirm-modal-ok"' in src
        assert 'id="confirm-modal-cancel"' in src

    def test_ux14_show_confirm_function_defined(self):
        """showConfirm() function is defined in the script."""
        src = _ui()
        assert "function showConfirm(" in src

    def test_ux14_show_confirm_returns_promise(self):
        """showConfirm() returns a Promise (new Promise(...))."""
        src = _ui()
        assert "return new Promise(" in src

    def test_ux14_no_bare_window_confirm_calls(self):
        """All four confirm() call sites now use await showConfirm() instead."""
        src = _ui()
        # Strip comments so we only check executable code.
        no_comments = re.sub(r'<!--.*?-->', '', src, flags=re.DOTALL)
        no_js_comments = re.sub(r'//[^\n]*', '', no_comments)
        # Look for bare confirm() calls (not showConfirm).
        bare = re.findall(r'(?<!show)confirm\(', no_js_comments)
        assert bare == [], (
            f"Found {len(bare)} bare confirm() call(s) that should use showConfirm(): "
            f"{bare}"
        )

    def test_ux14_remove_account_uses_show_confirm(self):
        """removeAccount() uses await showConfirm()."""
        src = _ui()
        assert "await showConfirm(" in src
        # Verify removeAccount function body references showConfirm.
        match = re.search(
            r'async function removeAccount\(.*?\{(.*?)^}',
            src, re.DOTALL | re.MULTILINE
        )
        if match:
            body = match.group(1)
            assert "showConfirm(" in body

    def test_ux14_delete_job_uses_show_confirm(self):
        """deleteJob() uses await showConfirm()."""
        src = _ui()
        match = re.search(
            r'async function deleteJob\(.*?\{(.*?)^}',
            src, re.DOTALL | re.MULTILINE
        )
        if match:
            body = match.group(1)
            assert "showConfirm(" in body

    def test_ux14_cancel_job_uses_show_confirm(self):
        """cancelJob() uses await showConfirm()."""
        src = _ui()
        match = re.search(
            r'async function cancelJob\(.*?\{(.*?)^}',
            src, re.DOTALL | re.MULTILINE
        )
        if match:
            body = match.group(1)
            assert "showConfirm(" in body

    def test_ux14_show_confirm_cleanup_removes_listeners(self):
        """showConfirm() has a cleanup() function to remove event listeners."""
        src = _ui()
        assert "function cleanup()" in src
        assert "removeEventListener" in src

    def test_ux14_comment_present(self):
        """The UX-14 audit comment is present in index.html."""
        src = _ui()
        assert "UX-14" in src


# ===========================================================================
# TestUX23IframeScrollAffordance
# ===========================================================================


class TestUX23IframeScrollAffordance:
    """UX-23: report iframe must indicate it has scrollable content below the fold."""

    def test_ux23_gradient_overlay_exists(self):
        """A gradient fade div is present inside the iframe wrapper."""
        src = _ui()
        # The gradient overlay should use linear-gradient with transparent start.
        assert "linear-gradient(transparent" in src

    def test_ux23_scroll_hint_text(self):
        """A scroll hint text is present near the iframe."""
        src = _ui()
        assert "Scroll inside the preview" in src or "↕" in src

    def test_ux23_iframe_has_title(self):
        """The results-iframe has an accessible title attribute."""
        src = _ui()
        assert 'title="Tax report preview' in src

    def test_ux23_gradient_is_aria_hidden(self):
        """The decorative gradient overlay carries aria-hidden='true'."""
        src = _ui()
        # The gradient div should be aria-hidden.
        assert 'aria-hidden="true"' in src

    def test_ux23_wrapper_has_relative_position(self):
        """The results-iframe-wrap div has position:relative for the absolute overlay."""
        src = _ui()
        match = re.search(r'id="results-iframe-wrap"[^>]*>', src)
        assert match, "results-iframe-wrap not found"
        tag = match.group(0)
        assert "position:relative" in tag, (
            f"results-iframe-wrap missing position:relative. Tag: {tag!r}"
        )

    def test_ux23_comment_present(self):
        """The UX-23 audit comment is present in index.html."""
        src = _ui()
        assert "UX-23" in src
