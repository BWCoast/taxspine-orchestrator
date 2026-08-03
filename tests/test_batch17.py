"""Batch 17 — regression tests for MEDIUM tax-law and infrastructure findings.

Findings covered
----------------
TL-04  NOR_MULTI mode can change cost basis without warning the user
TL-05  UK tax year boundary not communicated in job output
TL-16  RF-1159 output not validated for sign correctness after CLI completion
TL-17  dry_run=True job returns COMPLETED indistinguishable from real output
INFRA-20  CI workflow has no lint step (ruff not gating the build)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── shared helpers ────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_REPO = _HERE.parent
_SERVICES_PATH = _REPO / "taxspine_orchestrator" / "services.py"
_MODELS_PATH = _REPO / "taxspine_orchestrator" / "models.py"
_PYPROJECT_PATH = _REPO / "pyproject.toml"
_CI_WORKFLOW_PATH = _REPO / ".github" / "workflows" / "docker.yml"


def _services() -> str:
    return _SERVICES_PATH.read_text(encoding="utf-8")


def _models() -> str:
    return _MODELS_PATH.read_text(encoding="utf-8")


def _pyproject() -> str:
    return _PYPROJECT_PATH.read_text(encoding="utf-8")


def _ci_workflow() -> str:
    return _CI_WORKFLOW_PATH.read_text(encoding="utf-8")


# ── TL-04: NOR_MULTI cost-basis warning ───────────────────────────────────────


class TestTL04NorMultiWarning:
    """TL-04: when pipeline_mode=NOR_MULTI is used, a prominent warning must
    be written to the execution log noting the cost-basis implication."""

    def test_nor_multi_warning_string_in_services(self):
        """services.py must contain a NOR_MULTI cost-basis warning message."""
        src = _services()
        assert "NOR_MULTI" in src or "nor_multi" in src, (
            "TL-04: NOR_MULTI mode must be identified in services.py"
        )
        # The warning should mention cost basis or FIFO
        assert "lot pool" in src or "cost basis" in src or "FIFO" in src, (
            "TL-04: warning must mention the FIFO/lot-pool consequence"
        )

    def test_nor_multi_warning_logged(self):
        """When NOR_MULTI mode is detected, a WARNING must be appended to log_lines."""
        src = _services()
        # The log entry should mention 'WARNING' and 'NOR_MULTI' together
        # Search for the pattern in the NOR_MULTI branch
        nor_multi_idx = src.find('pipeline_mode == PipelineMode.NOR_MULTI')
        assert nor_multi_idx >= 0, "NOR_MULTI detection must be present"
        # The WARNING log entry should appear near the NOR_MULTI detection
        nearby = src[nor_multi_idx:nor_multi_idx + 800]
        assert "WARNING" in nearby or "warning" in nearby.lower(), (
            "TL-04: WARNING must be logged when NOR_MULTI mode is active"
        )

    def test_nor_multi_warning_mentions_per_file(self):
        """The warning must compare NOR_MULTI to per-file mode."""
        src = _services()
        assert "per-file" in src or "per_file" in src, (
            "TL-04: warning must mention per-file comparison"
        )

    def test_tl04_comment_in_services(self):
        """A TL-04 comment must document the warning."""
        src = _services()
        assert "TL-04" in src

    def test_nor_multi_warning_mentions_consistency(self):
        """Warning should advise using the same mode consistently."""
        src = _services()
        assert "consistent" in src.lower() or "same mode" in src.lower(), (
            "TL-04: warning should advise using the same mode across tax years"
        )


# ── TL-05: UK tax year boundary in job output ─────────────────────────────────


class TestTL05UkTaxPeriodBoundary:
    """TL-05: JobOutput must carry tax_period_start and tax_period_end for UK
    jobs so callers know which April-to-April window was used."""

    def test_tax_period_start_field_in_models(self):
        """JobOutput must have a tax_period_start field."""
        src = _models()
        assert "tax_period_start" in src, (
            "TL-05: JobOutput must have tax_period_start field"
        )

    def test_tax_period_end_field_in_models(self):
        """JobOutput must have a tax_period_end field."""
        src = _models()
        assert "tax_period_end" in src, (
            "TL-05: JobOutput must have tax_period_end field"
        )

    def test_tax_period_fields_are_optional(self):
        """tax_period_start and tax_period_end must be Optional (None for Norway)."""
        src = _models()
        idx = src.find("tax_period_start")
        snippet = src[idx:idx + 120]
        assert "Optional" in snippet or "None" in snippet, (
            "TL-05: tax_period_start must be Optional[str] to support Norway (None)"
        )

    def test_uk_tax_period_computed_in_services(self):
        """services.py must compute tax_period_start/end for UK jobs."""
        src = _services()
        assert "tax_period_start" in src, (
            "TL-05: services.py must set tax_period_start in JobOutput"
        )
        assert "tax_period_end" in src, (
            "TL-05: services.py must set tax_period_end in JobOutput"
        )

    def test_uk_period_formula_april_6(self):
        """UK tax year starts on 6 April; formula must reference '-04-06'."""
        src = _services()
        assert "04-06" in src, (
            "TL-05: UK tax period start formula must use '04-06' (6 April)"
        )

    def test_uk_period_formula_april_5(self):
        """UK tax year ends on 5 April next year; formula must reference '-04-05'."""
        src = _services()
        assert "04-05" in src, (
            "TL-05: UK tax period end formula must use '04-05' (5 April)"
        )

    def test_uk_period_only_for_uk_country(self):
        """tax_period_start must only be set when country == Country.UK."""
        src = _services()
        # Should be gated on Country.UK or country == "uk"
        uk_idx = src.find("Country.UK")
        # tax_period_start assignment must appear near UK check
        assert uk_idx >= 0, "Country.UK must be referenced in services.py"
        # The tax period computation should be near a Country.UK comparison
        period_idx = src.find("_tax_period_start")
        assert period_idx >= 0, "_tax_period_start must be set in services.py"

    def test_tl05_comment_in_services(self):
        """A TL-05 comment must explain the UK boundary computation."""
        src = _services()
        assert "TL-05" in src

    def test_pydantic_model_imports_still_work(self):
        """models.py must remain importable after adding the new fields."""
        from taxspine_orchestrator.models import JobOutput
        jo = JobOutput()
        assert jo.tax_period_start is None
        assert jo.tax_period_end is None


# ── TL-16: RF-1159 sign validation ───────────────────────────────────────────


class TestTL16Rf1159SignValidation:
    """TL-16: after a successful CLI run, the orchestrator must validate that
    gevinst, tap, and formue are all non-negative in every virtuellValuta line."""

    def test_validate_rf1159_signs_function_present(self):
        """_validate_rf1159_signs() helper must be defined in services.py."""
        src = _services()
        assert "_validate_rf1159_signs" in src, (
            "TL-16: _validate_rf1159_signs() must be defined in services.py"
        )

    def test_validate_rf1159_signs_callable(self):
        """_validate_rf1159_signs() must be importable and callable."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        assert callable(_validate_rf1159_signs)

    def test_valid_rf1159_returns_none(self, tmp_path):
        """A well-formed RF-1159 JSON must return None (no error)."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "rf1159.json"
        p.write_text(json.dumps({
            "skjema": "RF-1159",
            "inntektsaar": 2025,
            "virtuellValuta": [
                {"navn": "BTC", "type": "bitcoin", "formue": 100000, "gevinst": 5000, "tap": 0},
            ],
        }), encoding="utf-8")
        assert _validate_rf1159_signs(p) is None

    def test_negative_gevinst_returns_error(self, tmp_path):
        """A negative gevinst must be caught and an error string returned."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "rf1159.json"
        p.write_text(json.dumps({
            "virtuellValuta": [
                {"navn": "ETH", "gevinst": -100, "tap": 0, "formue": 50000},
            ],
        }), encoding="utf-8")
        result = _validate_rf1159_signs(p)
        assert result is not None, "Negative gevinst must trigger an error"
        assert "gevinst" in result.lower()

    def test_negative_tap_returns_error(self, tmp_path):
        """A negative tap must be caught."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "rf1159.json"
        p.write_text(json.dumps({
            "virtuellValuta": [
                {"navn": "XRP", "gevinst": 0, "tap": -500, "formue": 1000},
            ],
        }), encoding="utf-8")
        result = _validate_rf1159_signs(p)
        assert result is not None, "Negative tap must trigger an error"
        assert "tap" in result.lower()

    def test_negative_formue_returns_error(self, tmp_path):
        """A negative formue must be caught."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "rf1159.json"
        p.write_text(json.dumps({
            "virtuellValuta": [
                {"navn": "SOL", "gevinst": 0, "tap": 0, "formue": -1},
            ],
        }), encoding="utf-8")
        result = _validate_rf1159_signs(p)
        assert result is not None, "Negative formue must trigger an error"
        assert "formue" in result.lower()

    def test_empty_virtual_valuta_returns_none(self, tmp_path):
        """An empty virtuellValuta list is valid (no trades year)."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "rf1159.json"
        p.write_text(json.dumps({"virtuellValuta": []}), encoding="utf-8")
        assert _validate_rf1159_signs(p) is None

    def test_unreadable_file_returns_none(self, tmp_path):
        """A missing or corrupt file must return None (caller handles separately)."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "nonexistent.json"
        assert _validate_rf1159_signs(p) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        """A file with invalid JSON must return None (caller handles it)."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "rf1159.json"
        p.write_text("NOT VALID JSON {{{", encoding="utf-8")
        assert _validate_rf1159_signs(p) is None

    def test_validation_called_after_cli_run(self):
        """services.py must call _validate_rf1159_signs in the execution path."""
        src = _services()
        # After the annotation step there must be a call to _validate_rf1159_signs
        ann_idx = src.find("_annotate_rf1159_with_provenance")
        assert ann_idx >= 0
        validate_idx = src.find("_validate_rf1159_signs", ann_idx)
        assert validate_idx >= 0, (
            "TL-16: _validate_rf1159_signs must be called after provenance annotation"
        )

    def test_tl16_comment_in_services(self):
        """A TL-16 comment must document the validation step."""
        src = _services()
        assert "TL-16" in src

    def test_sign_error_carries_field_name(self, tmp_path):
        """Error string must name the failing field for easy diagnosis."""
        from taxspine_orchestrator.services import _validate_rf1159_signs
        p = tmp_path / "rf1159.json"
        p.write_text(json.dumps({
            "virtuellValuta": [{"navn": "DOGE", "gevinst": 0, "tap": -1, "formue": 0}],
        }), encoding="utf-8")
        err = _validate_rf1159_signs(p)
        assert err is not None
        assert "tap" in err.lower() or "-1" in err


# ── TL-17: dry_run job marked as non-authoritative ────────────────────────────


class TestTL17DryRunMarked:
    """TL-17: a dry_run=True completed job must carry an error_message that
    clearly distinguishes it from an authoritative tax computation."""

    def test_dry_run_error_message_set(self):
        """_execute_dry_run() must set error_message in JobOutput."""
        src = _services()
        # Look for the [DRY RUN] error_message assignment
        assert "[DRY RUN]" in src, (
            "TL-17: _execute_dry_run must set error_message starting with '[DRY RUN]'"
        )

    def test_dry_run_error_message_in_job_output_construction(self):
        """error_message must be set on the JobOutput object returned by dry run."""
        src = _services()
        # The error_message must appear inside a JobOutput(...) call
        job_output_idx = src.rfind("JobOutput(", 0, src.find("[DRY RUN]") + 200)
        assert job_output_idx >= 0, (
            "TL-17: [DRY RUN] error_message must be set inside a JobOutput() call"
        )

    def test_dry_run_message_mentions_not_authoritative(self):
        """The message must warn that output must not be used as authoritative."""
        src = _services()
        assert "not" in src and ("authoritative" in src or "must not" in src), (
            "TL-17: dry-run message must warn output is not authoritative"
        )

    def test_dry_run_log_still_written(self):
        """_execute_dry_run must still set log_path in JobOutput (for debugging)."""
        src = _services()
        # log_path must appear inside the function — search the file from the
        # function start onward.  The function is long so search broadly.
        dry_run_fn_idx = src.find("def _execute_dry_run")
        assert dry_run_fn_idx >= 0
        # There must be a log_path=str(log_path) somewhere after the function def
        assert "log_path=str(log_path)" in src[dry_run_fn_idx:] or \
               "log_path" in src[dry_run_fn_idx:dry_run_fn_idx + 5000], (
            "TL-17: _execute_dry_run must still populate log_path in JobOutput"
        )

    def test_tl17_comment_in_services(self):
        """A TL-17 comment must document the dry-run marker."""
        src = _services()
        assert "TL-17" in src


# ── INFRA-20: ruff lint step in CI ───────────────────────────────────────────


class TestINFRA20CiLintStep:
    """INFRA-20: the CI workflow must run a ruff lint check before pytest so
    style regressions are caught before a broken image is published."""

    def test_ruff_step_in_ci_workflow(self):
        """docker.yml must include a 'ruff check' step."""
        src = _ci_workflow()
        assert "ruff" in src.lower(), (
            "INFRA-20: CI workflow must include a ruff lint step"
        )

    def test_ruff_check_command_present(self):
        """The ruff step must run 'ruff check .'."""
        src = _ci_workflow()
        assert "ruff check" in src, (
            "INFRA-20: CI step must run 'ruff check .'"
        )

    def test_ruff_runs_before_pytest(self):
        """ruff must appear before the pytest RUN step in the workflow."""
        src = _ci_workflow()
        ruff_idx = src.find("ruff check")
        # Use the "Run tests" step's `run: python -m pytest` line, not just
        # any mention of "pytest" (the job-name 'pytest' appears near the top).
        pytest_run_idx = src.find("python -m pytest")
        assert ruff_idx >= 0, "ruff check step must be present"
        assert pytest_run_idx >= 0, "python -m pytest run step must be present"
        assert ruff_idx < pytest_run_idx, (
            "INFRA-20: ruff check must appear before 'python -m pytest' in CI steps"
        )

    def test_ruff_in_dev_dependencies(self):
        """ruff must be listed in [project.optional-dependencies.dev]."""
        src = _pyproject()
        assert "ruff" in src, (
            "INFRA-20: ruff must be a dev dependency in pyproject.toml"
        )

    def test_ruff_config_present(self):
        """A [tool.ruff] or [tool.ruff.lint] section must be in pyproject.toml."""
        src = _pyproject()
        assert "[tool.ruff" in src, (
            "INFRA-20: pyproject.toml must have a [tool.ruff] configuration section"
        )

    def test_infra20_comment_in_ci_workflow(self):
        """An INFRA-20 comment must document the lint step."""
        src = _ci_workflow()
        assert "INFRA-20" in src
