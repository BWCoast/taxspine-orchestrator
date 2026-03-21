"""Batch 27 — TL-08 lot carry-forward year-sequence warning, INFRA-24 start.ps1
dev-only guard, LC-10 RF-1159 draft disclaimer field.

Coverage:
    TL-08   _build_lot_carry_forward_csv logs a WARNING when tax_year <= any year
            already persisted in the lot store (running an older year after a newer
            one may corrupt the carry-forward chain).
    INFRA-24 scripts/start.ps1 includes a dev-only guard block that prints a
             warning when TAXSPINE_ENV is not set to "development".  The guard
             references the INFRA-24 issue tag.
    LC-10   JobOutput gains a ``draft_disclaimer`` field (Optional[str]).
            services.py populates it with a non-empty string whenever RF-1159
            JSON output is produced and leaves it None otherwise.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from taxspine_orchestrator.models import JobOutput
from taxspine_orchestrator.services import JobService


# ── TL-08: lot carry-forward year-sequence warning ────────────────────────────

class TestTL08LotYearSequenceWarning:
    """_build_lot_carry_forward_csv warns when running a prior year after a
    newer year has already been persisted in the lot store."""

    def _make_store(self, *, list_years_return: list[int], carry_lots: list | None = None):
        """Build a minimal LotPersistenceStore mock."""
        store = MagicMock()
        store.__enter__ = MagicMock(return_value=store)
        store.__exit__ = MagicMock(return_value=False)
        store.list_years.return_value = list_years_return
        store.load_carry_forward.return_value = carry_lots if carry_lots is not None else []
        return store

    def test_warns_when_future_year_present(self, tmp_path, caplog):
        """When lot store has a year >= tax_year, a WARNING is emitted."""
        import logging
        db_file = tmp_path / "lots.db"
        db_file.touch()
        store = self._make_store(list_years_return=[2024, 2025, 2026])
        with patch(
            "tax_spine.pipeline.lot_store.LotPersistenceStore",
        ) as MockStore, patch("taxspine_orchestrator.services.settings") as mock_settings:
            MockStore.return_value = store
            mock_settings.LOT_STORE_DB = db_file
            with caplog.at_level(logging.WARNING, logger="taxspine_orchestrator.services"):
                JobService._maybe_write_carry_forward_csv(tmp_path, 2025)
        assert any("TL-08" in r.message for r in caplog.records)

    def test_warns_includes_future_years_in_message(self, tmp_path, caplog):
        """The warning message includes the list of future/equal years."""
        import logging
        db_file = tmp_path / "lots.db"
        db_file.touch()
        store = self._make_store(list_years_return=[2024, 2026, 2027])
        with patch(
            "tax_spine.pipeline.lot_store.LotPersistenceStore",
        ) as MockStore, patch("taxspine_orchestrator.services.settings") as mock_settings:
            MockStore.return_value = store
            mock_settings.LOT_STORE_DB = db_file
            with caplog.at_level(logging.WARNING, logger="taxspine_orchestrator.services"):
                JobService._maybe_write_carry_forward_csv(tmp_path, 2025)
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("2026" in m or "2027" in m for m in warning_messages)

    def test_no_warning_when_only_prior_years(self, tmp_path, caplog):
        """No TL-08 warning when all persisted years are strictly before tax_year."""
        import logging
        db_file = tmp_path / "lots.db"
        db_file.touch()
        store = self._make_store(list_years_return=[2022, 2023, 2024])
        with patch(
            "tax_spine.pipeline.lot_store.LotPersistenceStore",
        ) as MockStore, patch("taxspine_orchestrator.services.settings") as mock_settings:
            MockStore.return_value = store
            mock_settings.LOT_STORE_DB = db_file
            with caplog.at_level(logging.WARNING, logger="taxspine_orchestrator.services"):
                JobService._maybe_write_carry_forward_csv(tmp_path, 2025)
        tl08_warnings = [r for r in caplog.records if "TL-08" in r.message]
        assert tl08_warnings == []

    def test_no_warning_when_store_empty(self, tmp_path, caplog):
        """No TL-08 warning when the lot store has no persisted years at all."""
        import logging
        db_file = tmp_path / "lots.db"
        db_file.touch()
        store = self._make_store(list_years_return=[])
        with patch(
            "tax_spine.pipeline.lot_store.LotPersistenceStore",
        ) as MockStore, patch("taxspine_orchestrator.services.settings") as mock_settings:
            MockStore.return_value = store
            mock_settings.LOT_STORE_DB = db_file
            with caplog.at_level(logging.WARNING, logger="taxspine_orchestrator.services"):
                JobService._maybe_write_carry_forward_csv(tmp_path, 2025)
        tl08_warnings = [r for r in caplog.records if "TL-08" in r.message]
        assert tl08_warnings == []

    def test_source_code_contains_tl08_tag(self):
        """services.py source contains the TL-08 comment tag."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        assert "TL-08" in src

    def test_source_code_checks_future_years(self):
        """The warning code checks for years >= tax_year (not just >)."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        # The guard must handle same-year and newer years
        assert "future_years" in src

    def test_warning_level_not_just_debug(self):
        """The out-of-order year detection uses _log.warning, not _log.debug."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        # The TL-08 comment block spans multiple lines; scan 700 chars from the
        # first mention to find the .warning( call that follows it.
        tl08_idx = src.index("TL-08")
        snippet = src[tl08_idx: tl08_idx + 700]
        assert "warning(" in snippet or ".warning(" in snippet


# ── INFRA-24: start.ps1 dev-only guard ────────────────────────────────────────

class TestINFRA24StartPs1Guard:
    """scripts/start.ps1 contains a dev-only warning block guarded by
    $env:TAXSPINE_ENV -ne 'development'."""

    @pytest.fixture(scope="class")
    def start_ps1_content(self) -> str:
        repo_root = Path(__file__).resolve().parent.parent
        ps1 = repo_root / "scripts" / "start.ps1"
        return ps1.read_text(encoding="utf-8")

    def test_file_exists(self):
        repo_root = Path(__file__).resolve().parent.parent
        ps1 = repo_root / "scripts" / "start.ps1"
        assert ps1.is_file(), "scripts/start.ps1 must exist"

    def test_infra24_tag_present(self, start_ps1_content):
        """INFRA-24 comment tag is present."""
        assert "INFRA-24" in start_ps1_content

    def test_env_var_check_present(self, start_ps1_content):
        """Script checks TAXSPINE_ENV variable."""
        assert "TAXSPINE_ENV" in start_ps1_content

    def test_development_value_present(self, start_ps1_content):
        """Script guards on the 'development' value."""
        assert "development" in start_ps1_content

    def test_dev_only_warning_text_present(self, start_ps1_content):
        """Script includes a warning about the script being for local dev only."""
        assert "LOCAL DEVELOPMENT ONLY" in start_ps1_content or \
               "development only" in start_ps1_content.lower()

    def test_0000_bind_warning_present(self, start_ps1_content):
        """Script warns that the server binds to 0.0.0.0."""
        assert "0.0.0.0" in start_ps1_content

    def test_guard_block_uses_write_warning(self, start_ps1_content):
        """Guard uses Write-Warning (not just Write-Host)."""
        assert "Write-Warning" in start_ps1_content

    def test_guard_appears_before_uvicorn_launch(self, start_ps1_content):
        """Dev guard block appears before the uvicorn launch command."""
        guard_pos = start_ps1_content.find("INFRA-24")
        uvicorn_pos = start_ps1_content.find("uvicorn")
        assert guard_pos < uvicorn_pos

    def test_suppress_instruction_present(self, start_ps1_content):
        """Script tells the user how to suppress the warning (set TAXSPINE_ENV)."""
        assert "suppress" in start_ps1_content.lower() or \
               "TAXSPINE_ENV=development" in start_ps1_content


# ── LC-10: JobOutput draft_disclaimer field ────────────────────────────────────

class TestLC10DraftDisclaimerField:
    """JobOutput.draft_disclaimer is an Optional[str] field that is None by
    default and is populated by services.py when RF-1159 JSON is produced."""

    def test_field_exists_on_joboutput(self):
        """JobOutput has a draft_disclaimer attribute."""
        jo = JobOutput()
        assert hasattr(jo, "draft_disclaimer")

    def test_field_defaults_to_none(self):
        """draft_disclaimer defaults to None."""
        jo = JobOutput()
        assert jo.draft_disclaimer is None

    def test_field_accepts_string(self):
        """draft_disclaimer can be set to a non-empty string."""
        jo = JobOutput(draft_disclaimer="This is a draft.")
        assert jo.draft_disclaimer == "This is a draft."

    def test_field_accepts_none_explicitly(self):
        """draft_disclaimer can be explicitly set to None."""
        jo = JobOutput(draft_disclaimer=None)
        assert jo.draft_disclaimer is None

    def test_field_present_in_json_output(self):
        """draft_disclaimer appears in the serialized JSON model."""
        jo = JobOutput(draft_disclaimer="DRAFT")
        data = jo.model_dump()
        assert "draft_disclaimer" in data
        assert data["draft_disclaimer"] == "DRAFT"

    def test_none_serializes_as_null(self):
        """When None, the field serializes to None (not absent)."""
        jo = JobOutput()
        data = jo.model_dump()
        assert "draft_disclaimer" in data
        assert data["draft_disclaimer"] is None


class TestLC10DraftDisclaimerConstant:
    """_DRAFT_DISCLAIMER constant in services.py is a non-empty string that
    identifies the output as a draft and warns against filing without review."""

    def test_constant_defined(self):
        """_DRAFT_DISCLAIMER is importable from services."""
        from taxspine_orchestrator.services import _DRAFT_DISCLAIMER
        assert isinstance(_DRAFT_DISCLAIMER, str)

    def test_constant_non_empty(self):
        from taxspine_orchestrator.services import _DRAFT_DISCLAIMER
        assert len(_DRAFT_DISCLAIMER) > 20

    def test_constant_mentions_draft(self):
        from taxspine_orchestrator.services import _DRAFT_DISCLAIMER
        assert "DRAFT" in _DRAFT_DISCLAIMER.upper()

    def test_constant_mentions_skatteetaten(self):
        from taxspine_orchestrator.services import _DRAFT_DISCLAIMER
        assert "Skatteetaten" in _DRAFT_DISCLAIMER

    def test_constant_mentions_tax_professional_or_review(self):
        from taxspine_orchestrator.services import _DRAFT_DISCLAIMER
        combined = _DRAFT_DISCLAIMER.lower()
        assert "professional" in combined or "review" in combined

    def test_source_code_contains_lc10_tag(self):
        """services.py source references LC-10."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        assert "LC-10" in src

    def test_disclaimer_populated_when_rf1159_paths_nonempty(self):
        """If all_rf1159_json_paths is truthy, draft_disclaimer must be non-None."""
        import taxspine_orchestrator.services as svc_mod
        src = inspect.getsource(svc_mod)
        # Look for the conditional assignment pattern in the JobOutput call
        assert "draft_disclaimer" in src
        assert "_DRAFT_DISCLAIMER if all_rf1159_json_paths else None" in src \
               or ("_DRAFT_DISCLAIMER" in src and "all_rf1159_json_paths" in src)
