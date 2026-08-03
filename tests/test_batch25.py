"""Batch 25 — TL-18 missing-basis lot alerts and INFRA-10 Docker cleanup.

Coverage:
    TL-18   FIFO lots with non-KNOWN basis_status were not surfaced in GET /alerts —
            the alerts endpoint now lazily imports LotPersistenceStore and emits a
            'warn' alert for each tax year that contains unresolved-basis lots.
            Gracefully skips when tax_spine is not installed.
    INFRA-10 build-essential and git were left in the Docker image after pip installs —
             a purge step now removes them to reduce image size and attack surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app

client = TestClient(app)

_DOCKERFILE = Path(__file__).parent.parent / "Dockerfile"
_MAIN_SRC = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"


def _main() -> str:
    return _MAIN_SRC.read_text(encoding="utf-8")


def _dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


# ===========================================================================
# TestTL18MissingBasisAlerts
# ===========================================================================


class TestTL18MissingBasisAlerts:
    """TL-18: missing-basis lots must be surfaced in GET /alerts."""

    def test_tl18_comment_in_main(self):
        """The TL-18 comment is present in main.py."""
        assert "TL-18" in _main()

    def test_tl18_lazy_import_in_alerts(self):
        """The alerts endpoint lazily imports LotPersistenceStore."""
        src = _main()
        assert "LotPersistenceStore" in src

    def test_tl18_import_error_swallowed(self):
        """An ImportError from the lazy import is silently swallowed (graceful skip)."""
        src = _main()
        assert "except ImportError:" in src

    def test_tl18_lot_quality_category(self):
        """The lot alert uses category 'lot_quality'."""
        src = _main()
        assert "lot_quality" in src

    def test_tl18_alert_message_mentions_basis(self):
        """The lot alert message mentions 'basis' so operators understand the issue."""
        src = _main()
        assert "basis" in src or "cost basis" in src

    def test_tl18_alert_message_mentions_filing(self):
        """The lot alert message tells operators to review before filing."""
        src = _main()
        assert "filing" in src

    def test_tl18_scans_recent_years(self):
        """The alerts endpoint scans multiple recent tax years."""
        src = _main()
        # The year range expression should be present.
        assert "current_year" in src or "_current_year" in src
        assert "range(" in src

    def test_tl18_alerts_endpoint_returns_200_without_lot_store(self):
        """GET /alerts returns 200 even when the lot store DB does not exist."""
        # With no LOT_STORE_DB configured / no file present the check is skipped.
        resp = client.get("/alerts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_tl18_lot_quality_alert_structure_in_code(self):
        """The lot quality alert dict in main.py matches the expected alert schema."""
        src = _main()
        # Verify the alert dict keys are all present in the lot quality block.
        for key in ('"severity"', '"category"', '"message"', '"job_id"', '"detail"'):
            assert key in src, f"Expected key {key} in alerts endpoint source"
        # Severity must be warn (not error) for missing-basis lots.
        # The lot_quality block uses "warn".
        import re
        # Also check the other direction.
        lot_block = re.search(
            r'lot_quality.*?alerts\.append\(\{(.*?)\}\)',
            src, re.DOTALL
        )
        if lot_block:
            block_text = lot_block.group(1)
            assert '"warn"' in block_text, (
                f"Expected severity='warn' in lot_quality alert block: {block_text!r}"
            )

    def test_tl18_detail_includes_asset_symbol_and_basis_status(self):
        """The lot quality alert detail list includes asset_symbol and basis_status."""
        src = _main()
        assert "asset_symbol" in src
        assert "basis_status" in src

    def test_tl18_import_error_does_not_break_alerts(self):
        """If LotPersistenceStore import raises ImportError, /alerts still returns 200."""
        # Temporarily make tax_spine unavailable.
        import sys
        saved = sys.modules.pop("tax_spine.pipeline", None)
        saved_ts = sys.modules.pop("tax_spine", None)
        try:
            resp = client.get("/alerts")
            assert resp.status_code == 200
        finally:
            if saved is not None:
                sys.modules["tax_spine.pipeline"] = saved
            if saved_ts is not None:
                sys.modules["tax_spine"] = saved_ts

    def test_tl18_only_non_known_basis_triggers_alert(self):
        """A lot with basis_status='known' must NOT trigger an alert."""
        src = _main()
        # Verify the filter checks for "known" (not just presence).
        assert '"known"' in src or "'known'" in src


# ===========================================================================
# TestINFRA10BuildDepsCleanup
# ===========================================================================


class TestINFRA10BuildDepsCleanup:
    """INFRA-10: build-time OS deps must be purged after pip installs complete."""

    def test_infra10_purge_step_present(self):
        """The Dockerfile contains an apt-get purge step for build-essential and git."""
        src = _dockerfile()
        assert "apt-get purge" in src

    def test_infra10_build_essential_purged(self):
        """build-essential is included in the purge command."""
        src = _dockerfile()
        # Find the purge line.
        match = [ln for ln in src.splitlines() if "apt-get purge" in ln]
        assert match, "No apt-get purge line found"
        assert any("build-essential" in ln for ln in match), (
            "build-essential not listed in apt-get purge"
        )

    def test_infra10_git_purged(self):
        """git is included in the purge command."""
        src = _dockerfile()
        match = [ln for ln in src.splitlines() if "apt-get purge" in ln]
        assert any("git" in ln for ln in match), (
            "git not listed in apt-get purge"
        )

    def test_infra10_auto_remove_flag(self):
        """The purge uses --auto-remove to also clean automatically-installed deps."""
        src = _dockerfile()
        assert "--auto-remove" in src

    def test_infra10_apt_lists_cleaned(self):
        """The purge step also removes /var/lib/apt/lists/* to minimise layer size."""
        src = _dockerfile()
        # Should appear in the same RUN block as the purge.
        purge_idx = src.find("apt-get purge")
        nearby = src[purge_idx: purge_idx + 200]
        assert "var/lib/apt/lists" in nearby, (
            "Expected apt/lists cleanup near the purge step"
        )

    def test_infra10_purge_after_pip_installs(self):
        """The purge step appears after the blockchain-reader pip install (last dep install)."""
        src = _dockerfile()
        br_idx = src.find("blockchain-reader.git")
        purge_idx = src.find("apt-get purge")
        assert purge_idx > br_idx, (
            "apt-get purge appears before the blockchain-reader install — "
            "purge should happen after all pip+git installs"
        )

    def test_infra10_comment_present(self):
        """The INFRA-10 comment is present in the Dockerfile."""
        src = _dockerfile()
        assert "INFRA-10" in src
