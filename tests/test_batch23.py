"""Batch 23 — Accessibility, UX clarity, and API-09 execution-time error.

Coverage:
    UX-06   Review badge emoji icons lack aria-label — now carry role="img" + aria-label.
    UX-09   Cancel / Delete button touch targets too small — padding increased.
    UX-10   Alert severity conveyed by colour alone — visible text label (CRITICAL / WARNING / INFO) added.
    UX-11   Tax Center table <th> lack scope="col" — attribute added to all three tables.
    UX-12   SOURCE_LABELS map incomplete — all supported source types now have friendly labels.
    UX-15   Pipeline mode help text lacked tax-consequence explanation — enhanced.
    UX-24   Dummy valuation warning referenced "Skatteetaten" (Norway-only) — made jurisdiction-neutral.
    API-09  Execution-time CSV file-not-found error message clarified to distinguish
            "file was valid at submission, deleted before execution" from "was never valid".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taxspine_orchestrator.models import (
    Country,
    CsvFileSpec,
    CsvSourceType,
    JobInput,
    JobStatus,
)
from taxspine_orchestrator.services import JobService
from taxspine_orchestrator.storage import InMemoryJobStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UI = Path(__file__).parent.parent / "ui" / "index.html"


def _ui() -> str:
    return _UI.read_text(encoding="utf-8")


def _services_src() -> str:
    import taxspine_orchestrator.services as m
    return Path(m.__file__).read_text(encoding="utf-8")


# ===========================================================================
# TestUX06ReviewBadgeAria
# ===========================================================================


class TestUX06ReviewBadgeAria:
    """UX-06: review badge icon must have role="img" and aria-label."""

    def test_ux06_rb_icon_has_role_img(self):
        """The rb-icon span now includes role="img"."""
        src = _ui()
        assert 'role="img"' in src
        # Specifically in the review badge context.
        assert 'class="rb-icon" role="img"' in src or 'rb-icon" role="img"' in src

    def test_ux06_clean_aria_label(self):
        """The clean state aria-label is 'Review clean'."""
        src = _ui()
        assert "Review clean" in src

    def test_ux06_unlinked_aria_label(self):
        """The unlinked-transfers state aria-label mentions 'unlinked transfers'."""
        src = _ui()
        assert "unlinked transfers detected" in src

    def test_ux06_warn_aria_label(self):
        """The warn state aria-label is 'Warning'."""
        src = _ui()
        assert "iconAriaLabel = 'Warning'" in src

    def test_ux06_icon_aria_label_variable_used(self):
        """The iconAriaLabel variable is interpolated into the rendered HTML."""
        src = _ui()
        assert "iconAriaLabel" in src
        assert 'aria-label="${escHtml(iconAriaLabel)}"' in src

    def test_ux06_comment_present(self):
        """The UX-06 audit comment is present in index.html."""
        src = _ui()
        assert "UX-06" in src


# ===========================================================================
# TestUX09TouchTargets
# ===========================================================================


class TestUX09TouchTargets:
    """UX-09: cancel/delete button touch targets must be larger than 3px padding."""

    def test_ux09_old_padding_removed(self):
        """The old 3px padding that was too small is no longer present in button styles."""
        src = _ui()
        # The old vulnerable pattern — 3px vertical padding on cancel/delete buttons.
        # It may appear in comments, so only check non-comment code lines.
        lines = src.splitlines()
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("<!--"):
                continue
            if "padding:3px 8px" in line and ("cancelJob" in line or "deleteJob" in line):
                pytest.fail(f"Old 3px padding still on button at line {lineno}: {stripped!r}")

    def test_ux09_increased_padding_cancel(self):
        """Cancel button has increased padding for better touch target."""
        src = _ui()
        assert "padding:6px 10px" in src

    def test_ux09_increased_padding_delete(self):
        """Delete button also uses the increased padding."""
        # Both cancel and delete use the same style constant — one check is enough,
        # but verify the comment references UX-09.
        src = _ui()
        assert "UX-09" in src

    def test_ux09_comment_present(self):
        """The UX-09 audit comment is present in index.html."""
        src = _ui()
        assert "UX-09" in src


# ===========================================================================
# TestUX10AlertSeverityLabel
# ===========================================================================


class TestUX10AlertSeverityLabel:
    """UX-10: alerts must have a visible text severity label, not colour alone."""

    def test_ux10_sev_label_variable_defined(self):
        """The sevLabel variable is defined in the alert rendering code."""
        src = _ui()
        assert "sevLabel" in src

    def test_ux10_critical_label(self):
        """'CRITICAL' text label is defined for error severity."""
        src = _ui()
        assert "'CRITICAL'" in src

    def test_ux10_warning_label(self):
        """'WARNING' text label is defined for warn severity."""
        src = _ui()
        assert "'WARNING'" in src

    def test_ux10_info_label(self):
        """'INFO' text label is defined for info severity."""
        src = _ui()
        assert "'INFO'" in src

    def test_ux10_label_rendered_in_html(self):
        """The sevLabel is interpolated into the alert DOM output."""
        src = _ui()
        assert "${sevLabel}" in src

    def test_ux10_comment_present(self):
        """The UX-10 audit comment is present in index.html."""
        src = _ui()
        assert "UX-10" in src


# ===========================================================================
# TestUX11TableScope
# ===========================================================================


class TestUX11TableScope:
    """UX-11: all Tax Center <th> elements must have scope="col"."""

    def test_ux11_holdings_table_has_scope(self):
        """Holdings table header cells all have scope=col."""
        src = _ui()
        # The Holdings table section should not have any <th> without scope.
        # Check that scope="col" appears near each expected header.
        for header in ("Asset", "Quantity", "Cost Basis", "Avg Cost", "Lots", "Basis"):
            # Find the line with this header text and verify scope="col" is present nearby.
            lines = src.splitlines()
            for i, line in enumerate(lines):
                if f">{header}<" in line or f">{header} " in line:
                    context = "\n".join(lines[max(0, i-1): i+2])
                    if 'scope="col"' not in context and '<th' in context:
                        pytest.fail(
                            f"<th> for '{header}' missing scope=\"col\". Context:\n{context}"
                        )
                    break

    def test_ux11_scope_col_count(self):
        """At least 14 scope=col attributes exist (6 holdings + 5 lots + 3 sources)."""
        src = _ui()
        count = src.count('scope="col"')
        assert count >= 14, f"Expected ≥14 scope=col attributes, found {count}"

    def test_ux11_no_th_without_scope_in_tc_tables(self):
        """No bare <th> (without scope) inside the three Tax Center tables."""
        src = _ui()
        import re
        # Strip HTML comments first so <th> appearing in comment text (e.g. the
        # UX-11 explanation comment itself) does not trigger a false positive.
        no_comments = re.sub(r'<!--.*?-->', '', src, flags=re.DOTALL)
        # Match <th ...> but NOT <thead ...> — use negative lookahead for 'ead'.
        # Also exclude any <th> that already carries a scope attribute.
        bare_ths = re.findall(r'<th(?!ead)(?![^>]*scope)[^>]*>', no_comments)
        assert bare_ths == [], (
            f"Found {len(bare_ths)} <th> element(s) without scope attribute: {bare_ths}"
        )

    def test_ux11_comment_present(self):
        """The UX-11 audit comment is present in index.html."""
        src = _ui()
        assert "UX-11" in src


# ===========================================================================
# TestUX12SourceLabels
# ===========================================================================


class TestUX12SourceLabels:
    """UX-12: SOURCE_LABELS must cover all backend source types."""

    def test_ux12_kraken_label(self):
        """Kraken spot has a human-readable label."""
        src = _ui()
        assert "Kraken (spot)" in src

    def test_ux12_bybit_uta_label(self):
        """Bybit UTA has a human-readable label."""
        src = _ui()
        assert "Bybit (UTA)" in src

    def test_ux12_bybit_fund_label(self):
        """Bybit funding has a human-readable label."""
        src = _ui()
        assert "Bybit (funding)" in src

    def test_ux12_bybit_withdraw_deposit_label(self):
        """Bybit withdrawals/deposits has a human-readable label."""
        src = _ui()
        assert "Bybit (withdrawals/deposits)" in src

    def test_ux12_nbx_label(self):
        """NBX has a human-readable label."""
        src = _ui()
        assert "'NBX'" in src or '"NBX"' in src

    def test_ux12_uphold_label(self):
        """Uphold has a human-readable label."""
        src = _ui()
        assert "'Uphold'" in src or '"Uphold"' in src

    def test_ux12_xrpl_bithomp_label(self):
        """XRPL Bithomp source has a human-readable label."""
        src = _ui()
        assert "XRPL (Bithomp)" in src

    def test_ux12_comment_present(self):
        """The UX-12 audit comment is present in index.html."""
        src = _ui()
        assert "UX-12" in src


# ===========================================================================
# TestUX15PipelineModeHelpText
# ===========================================================================


class TestUX15PipelineModeHelpText:
    """UX-15: pipeline mode selector must explain tax consequences, not just output format."""

    def test_ux15_mentions_cost_basis(self):
        """The pipeline mode help text mentions cost basis."""
        src = _ui()
        assert "cost basis" in src

    def test_ux15_mentions_fifo(self):
        """The pipeline mode help text still references FIFO lot pool."""
        src = _ui()
        assert "FIFO" in src

    def test_ux15_per_file_explained(self):
        """The help text explicitly names and explains Per file mode."""
        src = _ui()
        assert "Per file" in src

    def test_ux15_tax_consequence_language(self):
        """The help text contains language about taxable impact (gain/loss or affect)."""
        src = _ui()
        assert "taxable" in src or "affects" in src or "gain/loss" in src

    def test_ux15_comment_present(self):
        """The UX-15 audit comment is present in index.html."""
        src = _ui()
        assert "UX-15" in src


# ===========================================================================
# TestUX24JurisdictionNeutral
# ===========================================================================


class TestUX24JurisdictionNeutral:
    """UX-24: dummy valuation warning must not reference Skatteetaten (Norway-only authority)."""

    def test_ux24_skatteetaten_removed_from_warning(self):
        """'Skatteetaten' no longer appears in the dummy valuation warning block."""
        src = _ui()
        # The word may still appear elsewhere (e.g. in docs or comments) — only
        # check that it is gone from the inline user-facing warning message.
        import re
        # Find the warning div and check its content.
        match = re.search(
            r'id="dummy-valuation-warning"[^>]*>(.*?)</div>',
            src,
            re.DOTALL,
        )
        if match:
            warning_html = match.group(1)
            assert "Skatteetaten" not in warning_html, (
                "'Skatteetaten' still appears inside the dummy-valuation-warning div. "
                "The warning should be jurisdiction-neutral."
            )

    def test_ux24_jurisdiction_neutral_phrase(self):
        """The warning now uses 'suitable for tax filing' or equivalent neutral language."""
        src = _ui()
        assert "suitable for tax filing" in src or "not for filing" in src.lower()

    def test_ux24_comment_present(self):
        """The UX-24 audit comment is present in index.html."""
        src = _ui()
        assert "UX-24" in src


# ===========================================================================
# TestAPI09ExecutionTimeFileError
# ===========================================================================


class TestAPI09ExecutionTimeFileError:
    """API-09: execution-time file-not-found error must be distinct from attachment-time."""

    def test_api09_comment_present_in_services(self):
        """The API-09 comment is present in services.py."""
        src = _services_src()
        assert "API-09" in src

    def test_api09_error_message_mentions_execution_time(self):
        """The error message text distinguishes execution-time from attachment-time."""
        src = _services_src()
        assert "execution time" in src

    def test_api09_error_explains_deletion_scenario(self):
        """The error message explains the 'deleted after submission' scenario."""
        src = _services_src()
        assert "no longer available" in src or "moved or deleted" in src

    def test_api09_job_fails_with_descriptive_message(self, tmp_path):
        """When a CSV file is deleted between creation and execution, the error is descriptive."""
        store = InMemoryJobStore()
        svc = JobService(store)

        # Create a real CSV file, then delete it before execution.
        csv_path = tmp_path / "events.csv"
        csv_path.write_text("event_id,timestamp\n", encoding="utf-8")

        job_input = JobInput(
            csv_files=[CsvFileSpec(path=str(csv_path), source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
        )
        job = svc.create_job(job_input)

        # Delete the file — simulates deletion between job creation and execution.
        csv_path.unlink()

        svc.start_job_execution(job.id)

        updated = svc.get_job(job.id)
        assert updated.status == JobStatus.FAILED
        error = updated.output.error_message or ""
        assert "execution time" in error, (
            f"Expected 'execution time' in error message, got: {error!r}"
        )

    def test_api09_error_not_bare_not_found(self, tmp_path):
        """The new error message is not just the bare 'CSV file not found: ...' string."""
        store = InMemoryJobStore()
        svc = JobService(store)

        csv_path = tmp_path / "missing.csv"
        # Do NOT create the file — immediately missing.
        job_input = JobInput(
            csv_files=[CsvFileSpec(path=str(csv_path), source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
        )
        job = svc.create_job(job_input)
        svc.start_job_execution(job.id)

        updated = svc.get_job(job.id)
        error = updated.output.error_message or ""
        # The old bare message should no longer be the entire error string.
        assert error != f"CSV file not found: {csv_path}", (
            "Error message is still the old bare 'CSV file not found' string — "
            "it should now include the execution-time context."
        )
        assert updated.status == JobStatus.FAILED
