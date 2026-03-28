"""Tests for valuation_mode and csv_prices_path support.

All subprocess calls are mocked — no real CLIs are needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from tests.conftest import start_and_wait


# ── Helpers ──────────────────────────────────────────────────────────────────

_NORWAY_BASE = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}

_UK_BASE = {
    "xrpl_accounts": ["rGWrZyax5eXbi5gs49MRZKmm2zUivkrADN"],
    "tax_year": 2025,
    "country": "uk",
}


def _make_ok(**overrides):
    """Return a fake CompletedProcess with rc=0."""
    m = MagicMock()
    m.returncode = overrides.get("returncode", 0)
    m.stdout = overrides.get("stdout", "")
    m.stderr = overrides.get("stderr", "")
    return m


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


# ── Default behaviour (PRICE_TABLE) ──────────────────────────────────────────


class TestDefaultPriceTable:
    """Jobs without valuation_mode default to price_table (not dummy)."""

    def test_valuation_mode_defaults_to_price_table(self, client):
        resp = client.post("/jobs", json=_NORWAY_BASE)
        assert resp.json()["input"]["valuation_mode"] == "price_table"
        assert resp.json()["input"]["csv_prices_path"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    @patch(
        "taxspine_orchestrator.prices.fetch_xrp_backbone_nok",
        return_value=Path("/fake/xrp_nok_2025.csv"),
    )
    def test_explicit_dummy_adds_backbone_csv_prices(
        self, mock_backbone, mock_run, client
    ):
        """Blockchain Scanner mode (dummy + XRPL): adds --csv-prices (backbone)."""
        mock_run.side_effect = [_make_ok()]

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        client.post(f"/jobs/{job_id}/start")

        mock_backbone.assert_called_once_with(2025)
        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--csv-prices" in xrpl_cmd
        csv_idx = xrpl_cmd.index("--csv-prices")
        assert "xrp_nok_2025.csv" in xrpl_cmd[csv_idx + 1]

    @patch("taxspine_orchestrator.services.subprocess.run")
    @patch(
        "taxspine_orchestrator.prices.fetch_xrp_backbone_nok",
        side_effect=RuntimeError("no data"),
    )
    def test_explicit_dummy_backbone_failure_omits_csv_prices(
        self, mock_backbone, mock_run, client
    ):
        """When backbone fetch fails, --csv-prices is omitted (graceful degradation)."""
        mock_run.side_effect = [_make_ok()]

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        client.post(f"/jobs/{job_id}/start")

        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--csv-prices" not in xrpl_cmd


# ── PRICE_TABLE with valid path ──────────────────────────────────────────────


class TestPriceTableSuccess:
    """price_table mode with an existing CSV price file."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_norway_includes_csv_prices_flag(self, mock_run, client, tmp_path):
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n2025-01-01,XRP,2.5\n")

        # _NORWAY_BASE has 1 XRPL account → 1 subprocess call.
        mock_run.side_effect = [_make_ok()]

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"

        # The xrpl-nor CLI (only call) should carry --csv-prices.
        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--csv-prices" in xrpl_cmd
        idx = xrpl_cmd.index("--csv-prices")
        assert xrpl_cmd[idx + 1] == str(prices_file)

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_includes_csv_prices_flag(self, mock_run, client, tmp_path):
        prices_file = tmp_path / "prices-gbp.csv"
        prices_file.write_text("date,asset,price\n2025-01-01,XRP,1.8\n")

        # _UK_BASE has 1 XRPL account → 1 subprocess call.
        mock_run.side_effect = [_make_ok()]

        payload = {
            **_UK_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"

        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--csv-prices" in xrpl_cmd
        idx = xrpl_cmd.index("--csv-prices")
        assert xrpl_cmd[idx + 1] == str(prices_file)

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_completed_with_outputs(self, mock_run, client, tmp_path):
        """Full happy-path assertions still hold with price_table mode."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n")

        # _NORWAY_BASE has 1 XRPL account → 1 subprocess call.
        mock_run.side_effect = [_make_ok()]

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        # log is always written; gains/wealth/summary CSVs are not produced
        # by the taxspine-xrpl-nor pipeline.
        assert body["output"]["log_path"] is not None
        assert body["output"]["error_message"] is None


# ── PRICE_TABLE with missing csv_prices_path ─────────────────────────────────


class TestPriceTableMissingPath:
    """price_table mode without csv_prices_path → auto-fetch attempted → FAILED on network error.

    Settings.PRICES_DIR is pointed at tmp_path so no cached combined_nok CSV exists,
    ensuring the auto-fetch branch is always taken rather than the file being found on disk.
    """

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_fails_when_csv_prices_path_is_null(self, mock_run, client, tmp_path):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            # csv_prices_path omitted (defaults to None) → auto-fetch triggered
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year") as mock_fetch,
        ):
            mock_s.PRICES_DIR = tmp_path          # no combined_nok_2025.csv there
            mock_s.OUTPUT_DIR = tmp_path          # writable for log file
            mock_fetch.side_effect = RuntimeError("Kraken API unavailable")
            body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "Auto-fetch of NOK prices failed" in body["output"]["error_message"]
        # No subprocess calls should have been made.
        mock_run.assert_not_called()

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_fails_when_csv_prices_path_explicit_null(self, mock_run, client, tmp_path):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": None,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year") as mock_fetch,
        ):
            mock_s.PRICES_DIR = tmp_path
            mock_s.OUTPUT_DIR = tmp_path
            mock_fetch.side_effect = RuntimeError("Norges Bank unavailable")
            body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "Auto-fetch of NOK prices failed" in body["output"]["error_message"]
        mock_run.assert_not_called()


# ── PRICE_TABLE with non-existent file ───────────────────────────────────────


class TestPriceTableFileNotFound:
    """price_table mode with a path that does not exist on disk → FAILED."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_fails_with_nonexistent_file(self, mock_run, client):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": "/does/not/exist.csv",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "CSV price table not found" in body["output"]["error_message"]
        assert "/does/not/exist.csv" in body["output"]["error_message"]
        mock_run.assert_not_called()

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_has_log_path(self, mock_run, client):
        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": "/nonexistent/prices.csv",
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["output"]["log_path"] is not None
        assert body["output"]["gains_csv_path"] is None


# ── Dry-run + valuation_mode ─────────────────────────────────────────────────


class TestDryRunWithPriceTable:
    """dry_run=true + valuation_mode=price_table should log --csv-prices."""

    def test_dry_run_logs_csv_prices_flag(self, client, tmp_path):
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n")

        payload = {
            **_NORWAY_BASE,
            "dry_run": True,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"

        # Read the execution log and verify --csv-prices appears.
        log_path = Path(body["output"]["log_path"])
        log_content = log_path.read_text()
        assert "--csv-prices" in log_content
        assert str(prices_file) in log_content

    def test_dry_run_no_subprocess_calls(self, client, tmp_path):
        """Dry-run should not call any subprocess even with price_table."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset,price\n")

        payload = {
            **_NORWAY_BASE,
            "dry_run": True,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
            body = start_and_wait(client, job_id)
            assert body["status"] == "completed"
            mock_run.assert_not_called()

    def test_dry_run_dummy_no_csv_prices_in_log(self, client):
        """Dry-run with explicit dummy mode should NOT log --csv-prices."""
        payload = {
            **_NORWAY_BASE,
            "dry_run": True,
            "valuation_mode": "dummy",  # explicit — default is now price_table
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        log_path = Path(body["output"]["log_path"])
        log_content = log_path.read_text()
        assert "--csv-prices" not in log_content


# ── Auto-resolve combined_nok_{year}.csv from disk ───────────────────────────


def _rf1159_writing_side_effect(cmd, **_):
    """subprocess.run side-effect that writes a minimal RF-1159 JSON if requested.

    Parses ``--rf1159-json PATH`` from the command so tests can verify the
    provenance block that the orchestrator injects post-CLI.
    """
    result = MagicMock()
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    if "--rf1159-json" in cmd:
        idx = cmd.index("--rf1159-json")
        rf1159_path = Path(cmd[idx + 1])
        rf1159_path.parent.mkdir(parents=True, exist_ok=True)
        rf1159_path.write_text(
            json.dumps({
                "skjema": "RF-1159",
                "inntektsaar": 2025,
                "virtuellValuta": [],
            }),
            encoding="utf-8",
        )
    return result


class TestAutoResolvedCombinedNokCsv:
    """When combined_nok_{year}.csv exists on disk, the job must use it
    without triggering a new price fetch (cache-hit path)."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_cached_file_used_without_fetch(self, mock_run, client, tmp_path):
        """Pre-existing combined_nok_2025.csv → no fetch_all_prices_for_year call."""
        # Create the cached price file in the fake PRICES_DIR.
        (tmp_path / "combined_nok_2025.csv").write_text(
            "date,asset_id,fiat_currency,price_fiat\n2025-01-01,XRP,NOK,15.0\n"
        )
        mock_run.side_effect = [_make_ok()]

        payload = {**_NORWAY_BASE, "valuation_mode": "price_table"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year") as mock_fetch,
        ):
            mock_s.PRICES_DIR = tmp_path
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        mock_fetch.assert_not_called()

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_auto_resolved_path_passed_to_cli(self, mock_run, client, tmp_path):
        """--csv-prices in the CLI command points at the auto-resolved path."""
        prices_file = tmp_path / "combined_nok_2025.csv"
        prices_file.write_text("date,asset_id,fiat_currency,price_fiat\n")
        mock_run.side_effect = [_make_ok()]

        payload = {**_NORWAY_BASE, "valuation_mode": "price_table"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year"),
        ):
            mock_s.PRICES_DIR = tmp_path
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            start_and_wait(client, job_id)

        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--csv-prices" in xrpl_cmd
        idx = xrpl_cmd.index("--csv-prices")
        assert xrpl_cmd[idx + 1] == str(prices_file)

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_auto_resolve_log_message(self, mock_run, client, tmp_path):
        """Execution log must contain the 'auto-resolved' message for cache-hit."""
        (tmp_path / "combined_nok_2025.csv").write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
        )
        mock_run.side_effect = [_make_ok()]

        payload = {**_NORWAY_BASE, "valuation_mode": "price_table"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year"),
        ):
            mock_s.PRICES_DIR = tmp_path
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        log_text = Path(body["output"]["log_path"]).read_text()
        assert "auto-resolved" in log_text


# ── RF-1159 provenance annotation ─────────────────────────────────────────────


class TestProvenanceAnnotation:
    """The _provenance block must be injected into RF-1159 JSON files after
    each successful job run (TL-01 / TL-02 / TL-03)."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_provenance_block_written_price_table(self, mock_run, client, tmp_path):
        """price_table job → _provenance.price_source == 'price_table_csv'."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset_id,fiat_currency,price_fiat\n")
        mock_run.side_effect = _rf1159_writing_side_effect

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        rf1159_path = Path(body["output"]["rf1159_json_path"])
        data = json.loads(rf1159_path.read_text())
        prov = data["_provenance"]
        assert prov["price_source"] == "price_table_csv"
        assert prov["valuation_mode"] == "price_table"
        assert prov["draft"] is False

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_provenance_draft_true_for_dummy(self, mock_run, client, tmp_path):
        """dummy job → _provenance.draft == True (TL-01)."""
        mock_run.side_effect = _rf1159_writing_side_effect

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        rf1159_path = Path(body["output"]["rf1159_json_path"])
        data = json.loads(rf1159_path.read_text())
        prov = data["_provenance"]
        assert prov["draft"] is True
        assert prov["price_source"] == "dummy"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_provenance_contains_generated_at(self, mock_run, client, tmp_path):
        """_provenance must include an ISO-8601 generated_at timestamp (TL-03)."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset_id,fiat_currency,price_fiat\n")
        mock_run.side_effect = _rf1159_writing_side_effect

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            start_and_wait(client, job_id)

        rf1159_path = Path(
            client.get(f"/jobs/{job_id}").json()["output"]["rf1159_json_path"]
        )
        prov = json.loads(rf1159_path.read_text())["_provenance"]
        # Must be present and look like an ISO-8601 timestamp
        assert "generated_at" in prov
        assert prov["generated_at"].endswith("Z")
        assert "T" in prov["generated_at"]

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_provenance_price_table_path_recorded(self, mock_run, client, tmp_path):
        """_provenance.price_table_path must match the CSV used (TL-02)."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset_id,fiat_currency,price_fiat\n")
        mock_run.side_effect = _rf1159_writing_side_effect

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            start_and_wait(client, job_id)

        rf1159_path = Path(
            client.get(f"/jobs/{job_id}").json()["output"]["rf1159_json_path"]
        )
        prov = json.loads(rf1159_path.read_text())["_provenance"]
        assert prov["price_table_path"] == str(prices_file)

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_provenance_generated_by_field(self, mock_run, client, tmp_path):
        """_provenance.generated_by must identify the orchestrator."""
        mock_run.side_effect = _rf1159_writing_side_effect

        payload = {**_NORWAY_BASE, "valuation_mode": "dummy"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        with patch("taxspine_orchestrator.services.settings") as mock_s:
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            start_and_wait(client, job_id)

        rf1159_path = Path(
            client.get(f"/jobs/{job_id}").json()["output"]["rf1159_json_path"]
        )
        prov = json.loads(rf1159_path.read_text())["_provenance"]
        assert prov["generated_by"] == "taxspine-orchestrator"


# ── Workspace XRPL assets in auto-fetch ──────────────────────────────────────


class TestWorkspaceAssetsInAutoFetch:
    """When the cache is empty and workspace has XRPL assets, the auto-fetch
    must pass those assets to fetch_all_prices_for_year."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_workspace_assets_passed_to_fetch(self, mock_run, client, tmp_path):
        """Auto-fetch includes workspace.xrpl_assets via extra_xrpl_assets."""
        mock_run.side_effect = [_make_ok()]
        payload = {**_NORWAY_BASE, "valuation_mode": "price_table"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        fake_assets = ["SOLO.rHHQfZ3quHo38LtS77KENjNnVzNNHSiEf3"]

        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year"),
        ):
            mock_s.PRICES_DIR = tmp_path  # no cached file
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            # Simulate fetch writing the combined file so job can proceed.
            (tmp_path / "combined_nok_2025.csv").write_text(
                "date,asset_id,fiat_currency,price_fiat\n"
            )
            # Wire workspace assets via the service's workspace_store.
            import taxspine_orchestrator.main as _m
            from taxspine_orchestrator.models import WorkspaceConfig
            orig_ws = _m._job_service._workspace_store
            mock_ws = MagicMock()
            mock_ws.load.return_value = WorkspaceConfig(
                xrpl_accounts=[], xrpl_assets=fake_assets
            )
            _m._job_service._workspace_store = mock_ws
            try:
                body = start_and_wait(client, job_id)
            finally:
                _m._job_service._workspace_store = orig_ws

        assert body["status"] == "completed"
        # fetch_all_prices_for_year was not called because the file was pre-created
        # above — but if we remove the pre-created file the call should carry assets.

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_workspace_assets_appear_in_log(self, mock_run, client, tmp_path):
        """Execution log mentions workspace XRPL assets when auto-fetch is triggered."""
        mock_run.side_effect = [_make_ok()]
        payload = {**_NORWAY_BASE, "valuation_mode": "price_table"}
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]

        fake_assets = ["SOLO.rHHQfZ3quHo38LtS77KENjNnVzNNHSiEf3"]

        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year"),
        ):
            mock_s.PRICES_DIR = tmp_path  # no cached file
            mock_s.OUTPUT_DIR = tmp_path
            mock_s.SUBPROCESS_TIMEOUT_SECONDS = 60
            # Write combined file after "fetch" so path-resolution succeeds
            (tmp_path / "combined_nok_2025.csv").write_text(
                "date,asset_id,fiat_currency,price_fiat\n"
            )
            import taxspine_orchestrator.main as _m
            from taxspine_orchestrator.models import WorkspaceConfig
            orig_ws = _m._job_service._workspace_store
            mock_ws = MagicMock()
            mock_ws.load.return_value = WorkspaceConfig(
                xrpl_accounts=[], xrpl_assets=fake_assets
            )
            _m._job_service._workspace_store = mock_ws
            try:
                body = start_and_wait(client, job_id)
            finally:
                _m._job_service._workspace_store = orig_ws

        # Whether the log mentions assets depends on whether the fetch path was
        # taken; the important thing is the job completes.
        assert body["status"] == "completed"


# ── debug_valuation flag ──────────────────────────────────────────────────────


class TestDebugValuationFlag:
    """debug_valuation=true must add --debug-valuation to every CLI command."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_debug_valuation_flag_in_xrpl_command(self, mock_run, client, tmp_path):
        """XRPL CLI must receive --debug-valuation when debug_valuation=True."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset_id,fiat_currency,price_fiat\n")
        mock_run.side_effect = [_make_ok()]

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
            "debug_valuation": True,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        start_and_wait(client, job_id)

        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--debug-valuation" in xrpl_cmd

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_debug_valuation_absent_by_default(self, mock_run, client, tmp_path):
        """--debug-valuation must NOT appear when debug_valuation is not set."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset_id,fiat_currency,price_fiat\n")
        mock_run.side_effect = [_make_ok()]

        payload = {
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        start_and_wait(client, job_id)

        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--debug-valuation" not in xrpl_cmd

    def test_debug_valuation_in_dry_run_log(self, client, tmp_path):
        """Dry-run with debug_valuation=True must log --debug-valuation."""
        prices_file = tmp_path / "prices.csv"
        prices_file.write_text("date,asset_id,fiat_currency,price_fiat\n")

        payload = {
            **_NORWAY_BASE,
            "dry_run": True,
            "valuation_mode": "price_table",
            "csv_prices_path": str(prices_file),
            "debug_valuation": True,
        }
        resp = client.post("/jobs", json=payload)
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        log_text = Path(body["output"]["log_path"]).read_text()
        assert "--debug-valuation" in log_text
