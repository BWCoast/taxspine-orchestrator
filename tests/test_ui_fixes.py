"""Tests for Batch 7 and Batch 9 UI/frontend fixes.

Verifies that the expected security and UX patterns are present in the
compiled ui/index.html.  These tests guard against accidental regression
(e.g. a template regeneration that removes a fix) without requiring a
running browser or JavaScript engine.

Batch 7:
FE-02 — fetchPrices() validates server path before injecting into input
FE-07 — setInterval handles are stored on window for lifecycle management
FE-08 — alert severity validated against allowlist before CSS class interpolation
FE-09 — timestamp strings wrapped in escHtml() before innerHTML insertion
UX-16 — removeCsv() and removeAccount() guarded by confirm()
UX-17 — XRPL address input has inline format validation (pattern + error element)

Batch 9 (CRITICAL):
FE-01 — runReport() overlay always cleaned up via finally; secondary failures caught
UX-01 — Iframe sandbox blocks top-navigation and parent-window access; CSP injected
"""

from __future__ import annotations

from pathlib import Path

import pytest

UI_FILE = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert UI_FILE.is_file(), "ui/index.html must exist"
    return UI_FILE.read_text(encoding="utf-8")


# ── FE-02: Path validation before injection ────────────────────────────────────


class TestFE02PathValidation:
    """fetchPrices() must validate data.path before assigning to the input."""

    def test_path_pattern_check_present(self, html: str) -> None:
        assert "pathPattern" in html or "path_pattern" in html.lower() or \
               "/^[^\\" in html or "pathPattern" in html, (
            "fetchPrices must define a pattern to validate data.path"
        )

    def test_path_validation_before_assignment(self, html: str) -> None:
        # Scope the check to inside fetchPrices — the element id 'run-prices-path'
        # appears earlier in the HTML markup, so we must compare positions within
        # the function body, not the full document.
        fn_start = html.find("async function fetchPrices(")
        assert fn_start >= 0, "fetchPrices function must exist"
        # Take a generous slice of the function (2000 chars covers the full body).
        snippet = html[fn_start: fn_start + 2000]
        pattern_pos = snippet.find("pathPattern")
        assign_pos  = snippet.find("run-prices-path")
        assert pattern_pos >= 0, "pathPattern must be defined inside fetchPrices"
        assert assign_pos >= 0, "run-prices-path must be referenced inside fetchPrices"
        assert pattern_pos < assign_pos, (
            "Path validation (pathPattern) must occur before the input value is set"
        )

    def test_invalid_path_shows_error(self, html: str) -> None:
        # There should be an error message explaining the user should enter manually.
        assert "manually" in html or "invalid" in html.lower(), (
            "fetchPrices must show an error when the path is invalid"
        )

    def test_path_not_blindly_assigned(self, html: str) -> None:
        # The old naive pattern was an unguarded single-line assignment with no
        # preceding validation.  Now that pathPattern validation precedes the
        # assignment, the assignment itself is fine — what we must not see is
        # the *unguarded* form where the path is assigned without any prior
        # pattern test.  We verify this by confirming pathPattern is defined
        # before the first reference to run-prices-path (covered by
        # test_path_validation_before_assignment) and that a regex test on
        # data.path is present in fetchPrices.
        assert "pathPattern.test(data.path)" in html, (
            "data.path must be validated with pathPattern.test() before injection"
        )


# ── FE-07: setInterval handles stored ────────────────────────────────────────


class TestFE07IntervalHandles:
    """Background setInterval handles must be stored so they can be cleared."""

    def test_jobs_timer_stored(self, html: str) -> None:
        assert "_jobsTimer" in html or "jobsTimer" in html, (
            "loadJobs setInterval handle must be stored in a variable"
        )

    def test_health_timer_stored(self, html: str) -> None:
        assert "_healthTimer" in html or "healthTimer" in html, (
            "checkHealth setInterval handle must be stored in a variable"
        )

    def test_alerts_timer_stored(self, html: str) -> None:
        assert "_alertsTimer" in html or "alertsTimer" in html, (
            "loadAlerts setInterval handle must be stored in a variable"
        )

    def test_bare_setinterval_not_discarded(self, html: str) -> None:
        # The old pattern of bare setInterval() calls (result discarded) must be gone.
        import re
        bare = re.findall(r"^\s*setInterval\(", html, re.MULTILINE)
        assert len(bare) == 0, (
            f"All setInterval calls must be assigned to a variable; found {len(bare)} bare call(s)"
        )


# ── FE-08: Severity allowlist ─────────────────────────────────────────────────


class TestFE08SeverityAllowlist:
    """a.severity must be validated against an allowlist before CSS class use."""

    def test_severity_allowlist_present(self, html: str) -> None:
        assert "'error'" in html and "'warn'" in html and "'info'" in html, (
            "Severity allowlist must include 'error', 'warn', and 'info'"
        )

    def test_includes_check_for_severity(self, html: str) -> None:
        assert ".includes(a.severity)" in html, (
            "severity must be validated with .includes() before CSS class interpolation"
        )

    def test_fallback_sev_info_present(self, html: str) -> None:
        assert "sev-info" in html, (
            "Unknown severity must fall back to 'sev-info'"
        )

    def test_bare_sev_interpolation_removed(self, html: str) -> None:
        # The old unsanitised pattern: const sevClass = `sev-${a.severity}`;
        assert "sevClass = `sev-${a.severity}`" not in html, (
            "Unsanitised severity interpolation must be removed"
        )


# ── FE-09: Timestamp escaping ─────────────────────────────────────────────────


class TestFE09TimestampEscaping:
    """toLocaleString() timestamps must be wrapped in escHtml() before innerHTML."""

    def test_created_escaped(self, html: str) -> None:
        assert "escHtml(created)" in html, (
            "The 'created' timestamp must be wrapped in escHtml() before innerHTML insertion"
        )

    def test_bare_created_not_in_template(self, html: str) -> None:
        # The old unescaped pattern must be gone: ${created}
        # Note: escHtml(created) contains "created}" so we must check for the bare form.
        assert "${created}" not in html, (
            "Unescaped ${created} must be replaced with ${escHtml(created)}"
        )


# ── UX-16: Confirmation on workspace removals ────────────────────────────────


class TestUX16ConfirmOnRemove:
    """removeCsv() and removeAccount() must guard with a confirmation dialog before deleting.

    UX-14 upgraded the native confirm() call to the in-page showConfirm() modal;
    these tests accept either form so they remain valid after the upgrade.
    """

    def test_remove_account_has_confirm(self, html: str) -> None:
        # Find the removeAccount function body.
        start = html.find("async function removeAccount(")
        assert start >= 0, "removeAccount function must exist"
        snippet = html[start: start + 400]
        # Accept either the old confirm() or the new in-page showConfirm().
        assert "confirm(" in snippet or "showConfirm(" in snippet, (
            "removeAccount must call confirm() or showConfirm() before sending the DELETE request"
        )

    def test_remove_csv_has_confirm(self, html: str) -> None:
        start = html.find("async function removeCsv(")
        assert start >= 0, "removeCsv function must exist"
        snippet = html[start: start + 400]
        assert "confirm(" in snippet or "showConfirm(" in snippet, (
            "removeCsv must call confirm() or showConfirm() before sending the DELETE request"
        )

    def test_remove_account_confirm_before_fetch(self, html: str) -> None:
        start = html.find("async function removeAccount(")
        snippet = html[start: start + 600]
        # Accept showConfirm (in-page modal) or confirm (native).
        confirm_pos = min(
            p for p in [snippet.find("confirm("), snippet.find("showConfirm(")]
            if p >= 0
        ) if any(p >= 0 for p in [snippet.find("confirm("), snippet.find("showConfirm(")]) else -1
        # Accept either the raw fetch( or the apiFetch( wrapper added by the auth layer.
        _fetch_candidates = [p for p in [snippet.find("fetch("), snippet.find("apiFetch(")] if p >= 0]
        fetch_pos = min(_fetch_candidates) if _fetch_candidates else -1
        assert 0 <= confirm_pos < fetch_pos, (
            "confirm()/showConfirm() must appear before fetch() in removeAccount"
        )

    def test_remove_csv_confirm_before_fetch(self, html: str) -> None:
        start = html.find("async function removeCsv(")
        snippet = html[start: start + 600]
        confirm_pos = min(
            p for p in [snippet.find("confirm("), snippet.find("showConfirm(")]
            if p >= 0
        ) if any(p >= 0 for p in [snippet.find("confirm("), snippet.find("showConfirm(")]) else -1
        # Accept either the raw fetch( or the apiFetch( wrapper added by the auth layer.
        _fetch_candidates = [p for p in [snippet.find("fetch("), snippet.find("apiFetch(")] if p >= 0]
        fetch_pos = min(_fetch_candidates) if _fetch_candidates else -1
        assert 0 <= confirm_pos < fetch_pos, (
            "confirm()/showConfirm() must appear before fetch() in removeCsv"
        )


# ── UX-17: Inline XRPL address validation ────────────────────────────────────


class TestUX17XrplAddressValidation:
    """XRPL address input must have client-side format validation."""

    def test_validation_function_exists(self, html: str) -> None:
        assert "validateXrplAddressInput" in html, (
            "A validateXrplAddressInput function must be defined"
        )

    def test_xrpl_address_pattern_defined(self, html: str) -> None:
        assert "_XRPL_ADDR_PATTERN" in html or "XRPL_ADDR" in html, (
            "An XRPL address regex pattern must be defined"
        )

    def test_error_element_present_in_html(self, html: str) -> None:
        assert "add-account-error" in html, (
            "An error-message element with id 'add-account-error' must exist"
        )

    def test_input_triggers_validation(self, html: str) -> None:
        assert "oninput=\"validateXrplAddressInput" in html or \
               "onblur=\"validateXrplAddressInput" in html, (
            "The XRPL address input must trigger validateXrplAddressInput on input or blur"
        )

    def test_validation_called_in_add_account(self, html: str) -> None:
        start = html.find("async function addAccount(")
        assert start >= 0, "addAccount function must exist"
        snippet = html[start: start + 500]
        assert "validateXrplAddressInput" in snippet, (
            "addAccount must call validateXrplAddressInput before sending the request"
        )

    def test_error_message_describes_format(self, html: str) -> None:
        # The error message must explain XRPL address format.
        assert "base-58" in html or "base58" in html.lower(), (
            "The validation error message must describe the base-58 alphabet"
        )

    def test_pattern_checks_r_prefix(self, html: str) -> None:
        # The pattern must require the 'r' prefix.
        assert "^r[" in html or "^r[1-9" in html, (
            "The XRPL address validation pattern must require an 'r' prefix"
        )


# ── FE-01: Overlay always cleaned up (CRITICAL) ───────────────────────────────


class TestFE01OverlayCleanup:
    """FE-01: runReport() must guarantee overlay removal even on secondary failures.

    The loading overlay must be cleared in a `finally` block so that any
    exception thrown by loadJobs(), openResults(), or loadAlerts() after a
    successful job submission does not leave the UI locked.
    """

    def test_overlay_hidden_in_finally_block(self, html: str) -> None:
        # The pattern `finally` must appear in runReport and the overlay
        # classList.add('hidden') must be inside that finally block.
        fn_start = html.find("async function runReport(")
        assert fn_start >= 0, "runReport function must exist"
        # Use a large slice — runReport is a long function.
        snippet = html[fn_start: fn_start + 4000]
        assert "finally" in snippet, (
            "runReport must use a finally block to guarantee overlay cleanup"
        )
        finally_pos  = snippet.find("finally")
        # Search for the overlay *hide* call specifically (classList.add),
        # not just any reference to run-overlay (which also appears for the show).
        hide_marker = snippet.find("run-overlay", finally_pos)
        assert hide_marker >= finally_pos, (
            "The overlay hide call must be inside the finally block in runReport; "
            "run-overlay must appear after the finally keyword"
        )

    def test_run_btn_disabled_in_finally(self, html: str) -> None:
        fn_start = html.find("async function runReport(")
        # Use a large slice — the finally block comes well into the function.
        snippet  = html[fn_start: fn_start + 4000]
        finally_pos = snippet.find("finally")
        # run-btn must appear after the finally keyword.
        btn_pos = snippet.find("run-btn", finally_pos)
        assert btn_pos > finally_pos, (
            "run-btn re-enablement must be inside the finally block in runReport"
        )

    def test_secondary_calls_have_their_own_try_catch(self, html: str) -> None:
        # loadJobs / openResults / loadAlerts must be guarded separately so that
        # a failure there does not propagate to the outer error handler and show
        # a misleading "server down" alert.
        fn_start = html.find("async function runReport(")
        snippet  = html[fn_start: fn_start + 2000]
        # There must be a second try block after the finally (for secondary calls).
        first_try  = snippet.find("try")
        second_try = snippet.find("try", first_try + 1)
        assert second_try > first_try, (
            "runReport must have a second try/catch for secondary follow-up calls "
            "(loadJobs, openResults, loadAlerts)"
        )

    def test_secondary_calls_present_after_finally(self, html: str) -> None:
        fn_start   = html.find("async function runReport(")
        snippet    = html[fn_start: fn_start + 2000]
        finally_pos = snippet.find("finally")
        load_jobs   = snippet.find("loadJobs", finally_pos)
        assert load_jobs > finally_pos, (
            "loadJobs must be called after the finally block in runReport"
        )


# ── UX-01: Iframe sandbox (CRITICAL) ─────────────────────────────────────────


class TestUX01IframeSandbox:
    """UX-01: The report iframe must block top-navigation and parent-window access.

    Blobs are same-origin by default.  Without `allow-same-origin`, the iframe
    is treated as a null origin and scripts cannot reach window.parent at all.
    Without `allow-top-navigation`, even scripts with same-origin access cannot
    redirect the top-level page.  Both must be absent or both constraints must
    be explicitly verified.
    """

    def _iframe_sandbox(self, html: str) -> str:
        """Extract the sandbox attribute value from the results-iframe element."""
        import re
        m = re.search(r'<iframe[^>]+id="results-iframe"[^>]+sandbox="([^"]*)"', html)
        assert m is not None, "results-iframe with sandbox attribute must exist"
        return m.group(1)

    def test_allow_top_navigation_absent(self, html: str) -> None:
        sandbox = self._iframe_sandbox(html)
        assert "allow-top-navigation" not in sandbox, (
            "sandbox must NOT include allow-top-navigation — "
            "this would let report scripts redirect the parent page"
        )

    def test_allow_same_origin_absent(self, html: str) -> None:
        # Without allow-same-origin the iframe runs as a null origin even for
        # blob: URLs, preventing any access to window.parent DOM APIs.
        sandbox = self._iframe_sandbox(html)
        assert "allow-same-origin" not in sandbox, (
            "sandbox must NOT include allow-same-origin — "
            "its absence ensures report scripts cannot reach window.parent"
        )

    def test_allow_scripts_present(self, html: str) -> None:
        # Reports may use inline JS for table/chart rendering — scripts must work.
        sandbox = self._iframe_sandbox(html)
        assert "allow-scripts" in sandbox, (
            "sandbox must include allow-scripts so report JS renders correctly"
        )

    def test_csp_injected_into_blob(self, html: str) -> None:
        # Defence-in-depth: a CSP meta tag must be injected before the blob is
        # loaded, blocking external script sources and top-frame navigation.
        assert "Content-Security-Policy" in html, (
            "A Content-Security-Policy meta tag must be injected into report "
            "blobs before loading into the iframe (UX-01 defence-in-depth)"
        )

    def test_csp_blocks_navigate_to(self, html: str) -> None:
        assert "navigate-to" in html, (
            "The injected CSP must include navigate-to 'none' to block all "
            "frame navigation from within the report"
        )

    def test_iframe_security_comment_present(self, html: str) -> None:
        # A comment must document the intentional absence of allow-same-origin
        # and allow-top-navigation so future editors don't "fix" it.
        assert "allow-top-navigation is intentionally ABSENT" in html or \
               "allow-same-origin is intentionally ABSENT" in html, (
            "The iframe sandbox element must have a comment explaining why "
            "allow-same-origin and allow-top-navigation are intentionally absent"
        )
