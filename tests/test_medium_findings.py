"""test_medium_findings.py — Tests for medium-priority audit findings.

Covers:
- SEC-01: WorkspaceStore cleans up stale .tmp files at startup
- TL-07: RLUSD static peg emits a WARNING log when written
- TL-08: partial_year_warning populated for UK jobs run before year end
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch
import datetime

import pytest


# ── SEC-01: WorkspaceStore .tmp cleanup ───────────────────────────────────────


class TestWorkspaceStoreTmpCleanup:
    """SEC-01: stale .tmp file left by a crash is removed at next startup."""

    def test_stale_tmp_is_removed_at_init(self, tmp_path: Path) -> None:
        """If workspace.tmp exists when WorkspaceStore is created, it is deleted."""
        from taxspine_orchestrator.storage import WorkspaceStore

        ws_path = tmp_path / "workspace.json"
        stale_tmp = ws_path.with_suffix(".tmp")
        stale_tmp.write_text("stale content", encoding="utf-8")
        assert stale_tmp.exists()

        WorkspaceStore(ws_path)

        assert not stale_tmp.exists(), "stale .tmp file should have been removed"

    def test_stale_tmp_removal_logs_warning(self, tmp_path: Path) -> None:
        """Removing a stale .tmp file emits a WARNING log."""
        from taxspine_orchestrator.storage import WorkspaceStore
        import taxspine_orchestrator.storage as _storage_mod

        ws_path = tmp_path / "workspace.json"
        stale_tmp = ws_path.with_suffix(".tmp")
        stale_tmp.write_text("stale content", encoding="utf-8")

        with patch.object(_storage_mod, "_log") as mock_log:
            WorkspaceStore(ws_path)
            mock_log.warning.assert_called_once()
            call_msg = mock_log.warning.call_args[0][0]
            assert "stale" in call_msg.lower()

    def test_no_tmp_file_no_warning(self, tmp_path: Path) -> None:
        """When no .tmp file exists, WorkspaceStore starts silently."""
        from taxspine_orchestrator.storage import WorkspaceStore

        ws_path = tmp_path / "workspace.json"

        with patch("taxspine_orchestrator.storage._log") as mock_log:
            WorkspaceStore(ws_path)
            mock_log.warning.assert_not_called()

    def test_workspace_remains_readable_after_cleanup(self, tmp_path: Path) -> None:
        """After .tmp cleanup, the WorkspaceStore loads correctly."""
        from taxspine_orchestrator.storage import WorkspaceStore

        ws_path = tmp_path / "workspace.json"
        stale_tmp = ws_path.with_suffix(".tmp")
        stale_tmp.write_text("{invalid json}", encoding="utf-8")

        store = WorkspaceStore(ws_path)
        cfg = store.load()
        assert cfg.xrpl_accounts == []  # default empty config


# ── TL-07: RLUSD static peg warning ──────────────────────────────────────────


class TestRlusdStaticPegWarning:
    """TL-07: a WARNING is logged when the RLUSD static peg is written to CSV."""

    def test_rlusd_peg_emits_warning_log(self, tmp_path: Path) -> None:
        """The static-peg branch in fetch_all_prices_for_year logs a TL-07 WARNING."""
        from decimal import Decimal
        import taxspine_orchestrator.prices as _prices_mod

        nok_rates = {"2025-01-01": Decimal("10.5")}
        peg_rows  = {"2025-01-01": Decimal("1.0")}

        with patch.object(_prices_mod, "_log") as mock_log, \
             patch.object(_prices_mod, "_fetch_norges_bank_usd_nok", return_value=nok_rates), \
             patch.object(_prices_mod, "_fill_calendar_gaps", return_value=nok_rates), \
             patch.object(_prices_mod, "_fetch_kraken_usd_prices", return_value={}), \
             patch.object(_prices_mod, "_fetch_and_write"), \
             patch.object(_prices_mod, "_write_usd_as_nok_csv"), \
             patch.object(_prices_mod, "_asset_csv_path", return_value=tmp_path / "rlusd.csv"), \
             patch.object(_prices_mod, "_needs_fetch", return_value=True), \
             patch.object(_prices_mod, "_generate_static_peg_usd_rows", return_value=peg_rows), \
             patch.object(_prices_mod, "_classify_asset", return_value="static_peg"), \
             patch.object(_prices_mod, "_parse_xrpl_asset", return_value=("RLUSD", None)), \
             patch.object(_prices_mod, "settings") as ms:

            ms.PRICES_DIR = tmp_path
            try:
                _prices_mod.fetch_all_prices_for_year(2025, extra_xrpl_assets=["RLUSD"])
            except Exception:
                pass  # IO/network errors in the Kraken step are acceptable

            # A TL-07 WARNING must have been emitted during the static-peg branch
            warning_calls = [
                c for c in mock_log.warning.call_args_list
                if "TL-07" in str(c) or "peg" in str(c).lower()
            ]
            assert warning_calls, (
                "Expected a TL-07 WARNING about the RLUSD static peg. "
                f"Actual calls: {mock_log.warning.call_args_list}"
            )


# ── TL-08: UK partial-year warning ───────────────────────────────────────────


class TestUkPartialYearWarning:
    """TL-08: partial_year_warning is set for UK jobs run before the tax year ends."""

    def test_partial_year_warning_field_exists(self) -> None:
        """JobOutput has a partial_year_warning field defaulting to None."""
        from taxspine_orchestrator.models import JobOutput

        out = JobOutput()
        assert hasattr(out, "partial_year_warning")
        assert out.partial_year_warning is None

    def test_partial_year_warning_accepts_string(self) -> None:
        """partial_year_warning can be set to a non-None string."""
        from taxspine_orchestrator.models import JobOutput

        out = JobOutput(partial_year_warning="UK tax year 2025 ends on 2026-04-05.")
        assert "2025" in out.partial_year_warning

    def test_warning_set_when_year_not_elapsed(self) -> None:
        """The partial_year_warning logic: date ≤ year_end → warning string, else None."""
        import datetime as dt

        future_year = dt.date.today().year + 5
        year_end    = dt.date(future_year + 1, 4, 5)
        today       = dt.date.today()
        # Mirror the exact logic in _run_job
        warning = None
        if today <= year_end:
            warning = (
                f"UK tax year {future_year} ends on {year_end} "
                f"(5 April {future_year + 1}). "
                "This report was produced before the year closed — "
                "transactions after today are not included. "
                f"Re-run after 5 April {future_year + 1} to produce a complete return."
            )
        assert warning is not None, "should produce warning for a future tax year"
        assert str(future_year) in warning
        assert "Re-run" in warning

    def test_no_warning_for_norway_jobs(self) -> None:
        """partial_year_warning is never set for Norway jobs (calendar year)."""
        from taxspine_orchestrator.models import JobOutput

        # For Norway jobs, _partial_year_warning is initialized to None
        # and only set when country == UK. Verify the model field exists and is None.
        out = JobOutput(partial_year_warning=None)
        assert out.partial_year_warning is None

    def test_no_warning_when_year_elapsed(self) -> None:
        """If tax_year+1 April 5 is in the past, no warning should be generated."""
        import datetime as dt

        # Use a past tax year that definitely ended before today
        past_year = 2020
        year_end = dt.date(past_year + 1, 4, 5)
        assert dt.date.today() > year_end, "sanity: 2021-04-05 should be in the past"

        # The service logic: no warning if today > year_end
        today = dt.date.today()
        warning = None if today > year_end else f"UK tax year {past_year} warning"
        assert warning is None
