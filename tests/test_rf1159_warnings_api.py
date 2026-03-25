"""Tests for TL-09: rf1159_warnings surfaced in JobOutput.

The orchestrator reads the 'warnings' key from each RF-1159 JSON written by
the CLI and aggregates them into JobOutput.rf1159_warnings so callers can
detect incomplete filings without reading the RF-1159 file directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from tests.conftest import start_and_wait


_NORWAY_BASE = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}


def _make_ok(**overrides):
    m = MagicMock()
    m.returncode = overrides.get("returncode", 0)
    m.stdout = overrides.get("stdout", "")
    m.stderr = overrides.get("stderr", "")
    return m


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


def _rf1159_side_effect(warnings: list[str]):
    """Return a subprocess.run side-effect that writes RF-1159 JSON with warnings."""
    def _effect(cmd, **_):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        if "--rf1159-json" in cmd:
            idx = cmd.index("--rf1159-json")
            path = Path(cmd[idx + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            doc = {
                "skjema": "RF-1159",
                "inntektsaar": 2025,
                "virtuellValuta": [],
            }
            if warnings:
                doc["warnings"] = warnings
            path.write_text(json.dumps(doc), encoding="utf-8")
        return result
    return _effect


# ── Warnings present ─────────────────────────────────────────────────────────


class TestRf1159WarningsSurfaced:
    """When the RF-1159 JSON contains a warnings array, it must appear in
    JobOutput.rf1159_warnings (TL-09)."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_warnings_in_job_output(self, mock_run, client, tmp_path):
        """Warnings from RF-1159 JSON appear in output.rf1159_warnings."""
        w = ["UNRESOLVED COST BASIS: XRP. Review before filing."]
        mock_run.side_effect = _rf1159_side_effect(w)

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert body["output"]["rf1159_warnings"] == w

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_multiple_warnings_preserved_in_order(self, mock_run, client, tmp_path):
        """All warning strings are returned in the order the pipeline wrote them."""
        w = [
            "UNRESOLVED COST BASIS: BTC. Review before filing.",
            "UNRESOLVED INCOME: staking rewards not valued.",
        ]
        mock_run.side_effect = _rf1159_side_effect(w)

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["output"]["rf1159_warnings"] == w

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_warnings_via_get_job(self, mock_run, client, tmp_path):
        """GET /jobs/{id} exposes rf1159_warnings in the response."""
        w = ["UNRESOLVED COST BASIS: ETH."]
        mock_run.side_effect = _rf1159_side_effect(w)

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            start_and_wait(client, job_id)

        data = client.get(f"/jobs/{job_id}").json()
        assert data["output"]["rf1159_warnings"] == w


# ── No warnings (clean filing) ───────────────────────────────────────────────


class TestRf1159WarningsClean:
    """When the RF-1159 JSON has no warnings, rf1159_warnings must be an
    empty list (not None) — the job produced RF-1159 output and it was clean."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_empty_warnings_list_for_clean_output(self, mock_run, client, tmp_path):
        """RF-1159 with no warnings key → rf1159_warnings == []."""
        mock_run.side_effect = _rf1159_side_effect([])  # no warnings key written

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["output"]["rf1159_warnings"] == []

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_warnings_none_when_no_rf1159_produced(self, mock_run, client, tmp_path):
        """When no RF-1159 JSON is produced, rf1159_warnings is None."""
        # subprocess succeeds but does NOT write an RF-1159 file → list stays empty
        mock_run.side_effect = [_make_ok()]

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["output"]["rf1159_warnings"] is None


# ── Deduplication ─────────────────────────────────────────────────────────────


class TestRf1159WarningsDedup:
    """When multiple RF-1159 JSONs share the same warning string (e.g. the
    same unresolved-income message across two accounts), it must appear once."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_duplicate_warnings_deduped(self, mock_run, client, tmp_path):
        """Identical warning strings across multiple RF-1159 files appear once."""
        shared_warning = "UNRESOLVED INCOME: staking rewards not valued."
        call_count = [0]

        def _side_effect(cmd, **_):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if "--rf1159-json" in cmd:
                idx = cmd.index("--rf1159-json")
                path = Path(cmd[idx + 1])
                path.parent.mkdir(parents=True, exist_ok=True)
                # Both files carry the same warning.
                path.write_text(
                    json.dumps({
                        "skjema": "RF-1159",
                        "inntektsaar": 2025,
                        "virtuellValuta": [],
                        "warnings": [shared_warning],
                    }),
                    encoding="utf-8",
                )
            call_count[0] += 1
            return result

        # Two XRPL accounts → two CLI invocations → two RF-1159 files.
        payload = {
            "xrpl_accounts": [
                "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "rGWrZyax5eXbi5gs49MRZKmm2zUivkrADN",
            ],
            "tax_year": 2025,
            "country": "norway",
            "valuation_mode": "dummy",
        }
        mock_run.side_effect = _side_effect

        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        # Warning appears exactly once despite two files containing it.
        assert body["output"]["rf1159_warnings"] == [shared_warning]

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_unique_warnings_all_preserved(self, mock_run, client, tmp_path):
        """Different warning strings from multiple files are all included."""
        def _side_effect(cmd, **_):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if "--rf1159-json" in cmd:
                idx = cmd.index("--rf1159-json")
                path = Path(cmd[idx + 1])
                path.parent.mkdir(parents=True, exist_ok=True)
                # Use path stem to distinguish: rf1159_0 vs rf1159_1
                stem = path.stem  # e.g. "rf1159_0" or "rf1159_1"
                if stem.endswith("_0"):
                    warnings = ["Warning A"]
                else:
                    warnings = ["Warning B"]
                path.write_text(
                    json.dumps({
                        "skjema": "RF-1159",
                        "inntektsaar": 2025,
                        "virtuellValuta": [],
                        "warnings": warnings,
                    }),
                    encoding="utf-8",
                )
            return result

        payload = {
            "xrpl_accounts": [
                "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "rGWrZyax5eXbi5gs49MRZKmm2zUivkrADN",
            ],
            "tax_year": 2025,
            "country": "norway",
            "valuation_mode": "dummy",
        }
        mock_run.side_effect = _side_effect

        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert set(body["output"]["rf1159_warnings"]) == {"Warning A", "Warning B"}
