"""Tests for the job execution pipeline (services.py).

Pipeline summary (current implementation):
- XRPL accounts → one ``taxspine-xrpl-nor`` call per account.
- CSV files      → one ``taxspine-nor-report`` (or UK equivalent) call per file.
- No blockchain-reader step; taxspine-xrpl-nor handles the full XRPL pipeline.

All subprocess calls are mocked — no real CLIs are needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# TestCarryForwardLots patches tax_spine.pipeline.lot_store.LotPersistenceStore
# directly, which requires tax_spine to be importable at patch __enter__ time.
# Guard identically to test_dedup_api.py so CI without tax-nor stays green.
try:
    import tax_spine.pipeline.lot_store as _ts_lot  # noqa: F401
    _TAX_SPINE_AVAILABLE = True
except ImportError:
    _TAX_SPINE_AVAILABLE = False
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from tests.conftest import start_and_wait

# ── Helpers ───────────────────────────────────────────────────────────────────

# Norway job with TWO accounts → two taxspine-xrpl-nor calls.
_NORWAY_INPUT = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe"],
    "tax_year": 2025,
    "country": "norway",
}

# UK job with one account → one taxspine-xrpl-nor call (XRPL→NOR only).
_UK_INPUT = {
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


def _make_fail(rc: int = 1, stderr: str = "something broke"):
    return _make_ok(returncode=rc, stderr=stderr)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


# ── Successful execution ──────────────────────────────────────────────────────


class TestSuccessNorway:
    """Full happy path for a Norway XRPL job (two accounts)."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_completed_with_outputs(self, mock_run, client):
        # Two accounts → two CLI calls succeed.
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        # log is always written; report_html_path is None unless the CLI
        # actually creates the file on disk (it doesn't in this mock).
        assert body["output"]["log_path"] is not None
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_calls_xrpl_nor_per_account(self, mock_run, client):
        """One taxspine-xrpl-nor call is made for each XRPL account."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Two accounts → exactly two subprocess calls.
        assert mock_run.call_count == 2

        # Both calls use the xrpl-nor CLI.
        cmd0 = mock_run.call_args_list[0][0][0]
        cmd1 = mock_run.call_args_list[1][0][0]
        assert cmd0[0] == "taxspine-xrpl-nor"
        assert cmd1[0] == "taxspine-xrpl-nor"

        # Each call targets its respective account.
        assert "--account" in cmd0
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" in cmd0
        assert "--account" in cmd1
        assert "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe" in cmd1

        # Both carry the correct year flag.
        assert "--year" in cmd0
        assert "2025" in cmd0
        assert "--year" in cmd1
        assert "2025" in cmd1

        # Each call requests an HTML report output.
        assert "--html-output" in cmd0
        assert "--html-output" in cmd1


class TestSuccessUK:
    """Happy path for a UK XRPL job (one account).

    XRPL → tax pipeline uses taxspine-xrpl-nor regardless of country,
    because the CLI handles the full chain internally.
    """

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_completed_with_outputs(self, mock_run, client):
        mock_run.side_effect = [_make_ok()]

        resp = client.post("/jobs", json=_UK_INPUT)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        assert body["output"]["log_path"] is not None
        assert body["output"]["error_message"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_calls_xrpl_nor_cli(self, mock_run, client):
        """XRPL jobs use taxspine-xrpl-nor (not a UK-specific binary)."""
        mock_run.side_effect = [_make_ok()]

        resp = client.post("/jobs", json=_UK_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0][0][0]
        assert cmd[0] == "taxspine-xrpl-nor"
        assert "--account" in cmd
        assert "rGWrZyax5eXbi5gs49MRZKmm2zUivkrADN" in cmd
        assert "--year" in cmd
        assert "2025" in cmd


# ── Failing XRPL CLI ──────────────────────────────────────────────────────────


class TestFailXrplNor:
    """taxspine-xrpl-nor returns non-zero → job FAILED."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_xrpl_failure_marks_failed(self, mock_run, client):
        # First account fails immediately.
        mock_run.side_effect = [_make_fail(rc=1, stderr="connection refused")]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "taxspine-xrpl-nor failed" in body["output"]["error_message"]
        assert "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh" in body["output"]["error_message"]
        assert body["output"]["log_path"] is not None
        # No report artefact since job failed.
        assert body["output"]["report_html_path"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_xrpl_failure_stops_pipeline(self, mock_run, client):
        """When the first account fails, remaining accounts are not processed."""
        mock_run.side_effect = [_make_fail()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Only one call — failed immediately after the first account.
        assert mock_run.call_count == 1


# ── Failing second account ────────────────────────────────────────────────────


class TestFailSecondAccount:
    """First account succeeds, second fails → job FAILED after 2 calls."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_second_account_failure_marks_failed(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_fail(rc=2, stderr="bad schema")]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "taxspine-xrpl-nor failed" in body["output"]["error_message"]
        assert "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe" in body["output"]["error_message"]
        assert body["output"]["log_path"] is not None
        assert body["output"]["report_html_path"] is None

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_both_accounts_attempted_before_failure(self, mock_run, client):
        """First account call is made; second call fails → exactly 2 total calls."""
        mock_run.side_effect = [_make_ok(), _make_fail()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        assert mock_run.call_count == 2


# ── Idempotency ───────────────────────────────────────────────────────────────


class TestIdempotency:
    """Starting a job that is not PENDING returns the current state without re-running."""

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_start_completed_job_is_noop(self, mock_run, client):
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        # First start → completes (2 accounts → 2 CLI calls).
        start_and_wait(client, job_id)
        # Second start → returns same completed state (200), no extra calls.
        resp2 = client.post(f"/jobs/{job_id}/start")

        assert resp2.json()["status"] == "completed"
        assert mock_run.call_count == 2

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_start_failed_job_is_noop(self, mock_run, client):
        mock_run.side_effect = [_make_fail()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]

        start_and_wait(client, job_id)
        # Second start → returns current (failed) state (200), no extra calls.
        resp2 = client.post(f"/jobs/{job_id}/start")

        assert resp2.json()["status"] == "failed"
        # Only one call despite two start attempts.
        assert mock_run.call_count == 1


# ── API-01: --rf1159-json flag in XRPL command builder ───────────────────────


class TestXrplRf1159Flag:
    """Verify that _build_xrpl_command emits --rf1159-json for Norway jobs.

    API-01 (CRITICAL): the flag was previously accepted as a parameter but
    never appended to the command list, so XRPL jobs never produced an
    RF-1159 export.
    """

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_norway_xrpl_command_contains_rf1159_json_flag(self, mock_run, client):
        """Norway XRPL job → command includes --rf1159-json."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        # Both account commands should carry --rf1159-json.
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert "--rf1159-json" in cmd, (
                f"Expected --rf1159-json in XRPL command, got: {cmd}"
            )

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_norway_xrpl_rf1159_path_uses_correct_suffix(self, mock_run, client):
        """Two-account Norway job → each account gets its own indexed rf1159 path."""
        mock_run.side_effect = [_make_ok(), _make_ok()]

        resp = client.post("/jobs", json=_NORWAY_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd0 = mock_run.call_args_list[0][0][0]
        cmd1 = mock_run.call_args_list[1][0][0]

        idx0 = cmd0.index("--rf1159-json")
        idx1 = cmd1.index("--rf1159-json")

        path0 = cmd0[idx0 + 1]
        path1 = cmd1[idx1 + 1]

        # First account → rf1159_0.json; second → rf1159_1.json.
        assert path0.endswith("rf1159_0.json"), f"Unexpected path: {path0}"
        assert path1.endswith("rf1159_1.json"), f"Unexpected path: {path1}"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_uk_xrpl_command_omits_rf1159_json_flag(self, mock_run, client):
        """UK XRPL job → --rf1159-json must NOT be emitted (Norway-only form)."""
        mock_run.side_effect = [_make_ok()]

        resp = client.post("/jobs", json=_UK_INPUT)
        job_id = resp.json()["id"]
        client.post(f"/jobs/{job_id}/start")

        cmd = mock_run.call_args_list[0][0][0]
        assert "--rf1159-json" not in cmd, (
            f"--rf1159-json must not appear for UK jobs, got: {cmd}"
        )

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_dry_run_norway_xrpl_log_contains_rf1159_json(self, mock_run, client):
        """Dry-run Norway XRPL job: execution log shows --rf1159-json in preview."""
        # dry_run skips actual subprocess — mock should NOT be called.
        mock_run.side_effect = []

        resp = client.post("/jobs", json={**_NORWAY_INPUT, "dry_run": True})
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        log_path = body["output"]["log_path"]
        assert log_path is not None
        from pathlib import Path
        log_text = Path(log_path).read_text(encoding="utf-8")
        assert "--rf1159-json" in log_text, (
            "Dry-run log must show --rf1159-json in the [would run] preview"
        )


# ── TL-11: Reject non-GENERIC_EVENTS CSVs in mixed XRPL+CSV jobs ─────────────


class TestMixedJobSourceTypeGuard:
    """TL-11 — mixed XRPL+CSV jobs must fail fast for non-generic-events CSVs.

    taxspine-xrpl-nor only accepts --generic-events-csv.  Previously,
    Coinbase and Firi CSVs would silently be skipped; now the job fails
    with a clear error so the user submits a separate CSV-only job.
    """

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_coinbase_csv_in_xrpl_job_fails(self, mock_run, client, tmp_path):
        """XRPL + Coinbase CSV -> immediate FAILED, no subprocess called."""
        csv_file = tmp_path / "coinbase.csv"
        csv_file.write_text("dummy\n")

        resp = client.post("/jobs", json={
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [{"path": str(csv_file), "source_type": "coinbase_csv"}],
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert mock_run.call_count == 0, "No subprocess must run for rejected mixed job"

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_firi_csv_in_xrpl_job_fails(self, mock_run, client, tmp_path):
        """XRPL + Firi CSV -> immediate FAILED."""
        csv_file = tmp_path / "firi.csv"
        csv_file.write_text("dummy\n")

        resp = client.post("/jobs", json={
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [{"path": str(csv_file), "source_type": "firi_csv"}],
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert mock_run.call_count == 0

    def test_error_message_names_unsupported_type_and_path(self, client, tmp_path):
        """Error message must include the source_type and file path."""
        csv_file = tmp_path / "coinbase_export.csv"
        csv_file.write_text("dummy\n")

        resp = client.post("/jobs", json={
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [{"path": str(csv_file), "source_type": "coinbase_csv"}],
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        err = body["output"]["error_message"]
        assert "coinbase_csv" in err
        assert "coinbase_export.csv" in err

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_generic_events_csv_in_xrpl_job_is_allowed(self, mock_run, client, tmp_path):
        """XRPL + generic-events CSV -> must NOT be rejected (guard allows it)."""
        mock_run.return_value = _make_ok()
        csv_file = tmp_path / "events.csv"
        csv_file.write_text(
            "event_id,timestamp,event_type,source,account,"
            "asset_in,amount_in,asset_out,amount_out,"
            "fee_asset,fee_amount,tx_hash,exchange_tx_id,label,"
            "complex_tax_treatment,note\n"
        )

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_settings.OUTPUT_DIR = tmp_path
            mock_settings.PRICES_DIR = tmp_path
            mock_settings.LOT_STORE_DB = tmp_path / "no_lots.db"

            resp = client.post("/jobs", json={
                "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
                "tax_year": 2025,
                "country": "norway",
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
            })
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"

    def test_multiple_unsupported_types_all_listed_in_error(self, client, tmp_path):
        """When multiple files are unsupported, all are named in the error."""
        coinbase_file = tmp_path / "cb.csv"
        firi_file = tmp_path / "firi.csv"
        coinbase_file.write_text("x\n")
        firi_file.write_text("x\n")

        resp = client.post("/jobs", json={
            "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
            "tax_year": 2025,
            "country": "norway",
            "csv_files": [
                {"path": str(coinbase_file), "source_type": "coinbase_csv"},
                {"path": str(firi_file), "source_type": "firi_csv"},
            ],
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        err = body["output"]["error_message"]
        assert "coinbase_csv" in err
        assert "firi_csv" in err


# ── TL-13: Lot carry-forward synthetic CSV ────────────────────────────────────


class TestCarryForwardLots:
    """TL-13 -- prior-year FIFO lots are injected as synthetic TRADE events.

    _maybe_write_carry_forward_csv reads the lot persistence store and
    writes a generic-events CSV that the CLI treats as opening positions
    for the current tax year.

    Skipped when ``tax_spine`` is not installed (CI without tax-nor).
    patch() targets ``tax_spine.pipeline.lot_store.LotPersistenceStore``
    which requires the package to be importable at context-manager entry.
    """

    pytestmark = pytest.mark.skipif(
        not _TAX_SPINE_AVAILABLE,
        reason="tax_spine not installed — skipping carry-forward lot tests",
    )

    def _make_lot(self, asset: str, qty: str, basis_nok, lot_id: str = "lot1"):
        lot = MagicMock()
        lot.lot_id = lot_id
        lot.asset = asset
        lot.remaining_quantity = qty
        lot.remaining_cost_basis_nok = basis_nok
        return lot

    def test_no_lot_store_file_returns_none(self, tmp_path):
        """When LOT_STORE_DB does not exist, returns None."""
        from taxspine_orchestrator.services import JobService

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.LOT_STORE_DB = tmp_path / "nonexistent.db"
            result = JobService._maybe_write_carry_forward_csv(tmp_path, 2025)

        assert result is None

    def test_no_prior_year_in_store_returns_none(self, tmp_path):
        """When there are no lots for tax_year-1, returns None."""
        from taxspine_orchestrator.services import JobService

        db = tmp_path / "lots.db"
        db.write_text("")

        mock_store = MagicMock()
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.list_years.return_value = []

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.LOT_STORE_DB = db
            with patch(
                "tax_spine.pipeline.lot_store.LotPersistenceStore",
                return_value=mock_store,
            ):
                result = JobService._maybe_write_carry_forward_csv(tmp_path, 2025)

        assert result is None

    def test_lots_with_resolved_basis_are_written(self, tmp_path):
        """Lots with resolved NOK basis -> synthetic TRADE rows written."""
        from taxspine_orchestrator.services import JobService

        db = tmp_path / "lots.db"
        db.write_text("")

        lot = self._make_lot("BTC", "0.5", "500000")
        mock_store = MagicMock()
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.list_years.return_value = [2024]
        mock_store.load_carry_forward.return_value = [lot]

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.LOT_STORE_DB = db
            with patch(
                "tax_spine.pipeline.lot_store.LotPersistenceStore",
                return_value=mock_store,
            ):
                result = JobService._maybe_write_carry_forward_csv(tmp_path, 2025)

        assert result is not None and result.exists()
        content = result.read_text(encoding="utf-8")
        assert "BTC" in content
        assert "0.5" in content
        assert "500000" in content
        assert "TRADE" in content
        assert "2024-12-31T23:59:59Z" in content

    def test_lots_with_missing_basis_are_skipped(self, tmp_path):
        """Lots without a cost basis are not included in carry-forward CSV."""
        from taxspine_orchestrator.services import JobService

        db = tmp_path / "lots.db"
        db.write_text("")

        lot_good = self._make_lot("ETH", "1.0", "30000", "lot_good")
        lot_bad = self._make_lot("XRP", "100", None, "lot_bad")
        mock_store = MagicMock()
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.list_years.return_value = [2024]
        mock_store.load_carry_forward.return_value = [lot_good, lot_bad]

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.LOT_STORE_DB = db
            with patch(
                "tax_spine.pipeline.lot_store.LotPersistenceStore",
                return_value=mock_store,
            ):
                result = JobService._maybe_write_carry_forward_csv(tmp_path, 2025)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "ETH" in content
        assert "XRP" not in content

    def test_all_lots_missing_basis_returns_none(self, tmp_path):
        """When every lot has missing basis, no CSV is written."""
        from taxspine_orchestrator.services import JobService

        db = tmp_path / "lots.db"
        db.write_text("")

        lot = self._make_lot("BTC", "0.1", None)
        mock_store = MagicMock()
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.list_years.return_value = [2024]
        mock_store.load_carry_forward.return_value = [lot]

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.LOT_STORE_DB = db
            with patch(
                "tax_spine.pipeline.lot_store.LotPersistenceStore",
                return_value=mock_store,
            ):
                result = JobService._maybe_write_carry_forward_csv(tmp_path, 2025)

        assert result is None

    def test_carry_forward_csv_has_generic_events_header(self, tmp_path):
        """Output CSV must use the standard generic-events CSV header."""
        from taxspine_orchestrator.services import JobService

        db = tmp_path / "lots.db"
        db.write_text("")

        mock_store = MagicMock()
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.list_years.return_value = [2024]
        mock_store.load_carry_forward.return_value = [self._make_lot("BTC", "1.0", "1000000")]

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.LOT_STORE_DB = db
            with patch(
                "tax_spine.pipeline.lot_store.LotPersistenceStore",
                return_value=mock_store,
            ):
                result = JobService._maybe_write_carry_forward_csv(tmp_path, 2025)

        header = result.read_text(encoding="utf-8").splitlines()[0]
        for col in ("event_id", "timestamp", "event_type", "asset_in", "amount_in"):
            assert col in header, f"Expected column {col!r} in header: {header}"

    def test_carry_forward_csv_filename_uses_prior_year(self, tmp_path):
        """Output filename encodes prior year for easy identification."""
        from taxspine_orchestrator.services import JobService

        db = tmp_path / "lots.db"
        db.write_text("")

        mock_store = MagicMock()
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.list_years.return_value = [2024]
        mock_store.load_carry_forward.return_value = [self._make_lot("BTC", "1.0", "1000000")]

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.LOT_STORE_DB = db
            with patch(
                "tax_spine.pipeline.lot_store.LotPersistenceStore",
                return_value=mock_store,
            ):
                result = JobService._maybe_write_carry_forward_csv(tmp_path, 2025)

        assert "2024" in result.name
