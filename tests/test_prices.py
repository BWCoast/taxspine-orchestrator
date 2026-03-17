"""Tests for the price fetching service and auto-wiring.

Covers:
- GET /prices  — lists cached price CSVs in PRICES_DIR.
- POST /prices/fetch — validates year range; delegates to fetch_all_prices_for_year.
- Auto-wiring in services.py: when valuation_mode=price_table and csv_prices_path is
  None, a combined_nok_{year}.csv in PRICES_DIR is auto-resolved and the job succeeds.
  When no such file exists the job fails with a hint pointing to POST /prices/fetch.
"""

from __future__ import annotations

import datetime
import io
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.prices import (
    _asset_csv_path,
    _combined_csv_path,
    _fetch_kraken_usd_prices,
    _fetch_norges_bank_usd_nok,
    _fill_calendar_gaps,
    _fetch_and_write,
    _needs_fetch,
    fetch_all_prices_for_year,
)
from tests.conftest import start_and_wait


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


_NORWAY_BASE = {
    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "tax_year": 2025,
    "country": "norway",
}


# ── TestListPricesEndpoint ────────────────────────────────────────────────────


class TestListPricesEndpoint:
    def test_empty_prices_dir_returns_empty_list(self, client, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_combined_file_appears_in_list(self, client, tmp_path):
        combined = tmp_path / "combined_nok_2025.csv"
        combined.write_text("date,asset_id,fiat_currency,price_fiat\n2025-01-01,XRP,NOK,7.5\n")
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["asset"] == "COMBINED"
        assert items[0]["year"] == 2025
        assert items[0]["rows"] == 1

    def test_per_asset_file_appears_in_list(self, client, tmp_path):
        xrp_file = tmp_path / "xrp_nok_2024.csv"
        xrp_file.write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
            "2024-01-01,XRP,NOK,6.2\n"
            "2024-01-02,XRP,NOK,6.4\n"
        )
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["asset"] == "XRP"
        assert items[0]["year"] == 2024
        assert items[0]["rows"] == 2

    def test_non_matching_filenames_skipped(self, client, tmp_path):
        (tmp_path / "random.csv").write_text("header\nrow\n")
        (tmp_path / "xrp_usd_2025.csv").write_text("header\nrow\n")  # not _nok_
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_multiple_files_all_listed(self, client, tmp_path):
        for name in ["xrp_nok_2025.csv", "btc_nok_2025.csv", "combined_nok_2025.csv"]:
            (tmp_path / name).write_text("date,asset_id,fiat_currency,price_fiat\n2025-01-01,X,NOK,1\n")
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_age_hours_is_float(self, client, tmp_path):
        (tmp_path / "combined_nok_2025.csv").write_text("date,asset_id,fiat_currency,price_fiat\n")
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        assert isinstance(resp.json()[0]["age_hours"], float)

    def test_path_is_absolute_string(self, client, tmp_path):
        (tmp_path / "xrp_nok_2025.csv").write_text("date,asset_id,fiat_currency,price_fiat\n")
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        assert Path(resp.json()[0]["path"]).is_absolute()


# ── TestFetchPricesEndpointValidation ─────────────────────────────────────────


class TestFetchPricesEndpointValidation:
    def test_year_below_2013_returns_400(self, client):
        resp = client.post("/prices/fetch", json={"year": 2012})
        assert resp.status_code == 400
        assert "2013" in resp.json()["detail"]

    def test_year_1900_returns_400(self, client):
        resp = client.post("/prices/fetch", json={"year": 1900})
        assert resp.status_code == 400

    def test_year_far_future_returns_400(self, client):
        future = datetime.date.today().year + 1
        resp = client.post("/prices/fetch", json={"year": future})
        assert resp.status_code == 400

    def test_missing_year_returns_422(self, client):
        resp = client.post("/prices/fetch", json={})
        assert resp.status_code == 422

    def test_valid_past_year_attempts_fetch(self, client, tmp_path):
        """A valid past year triggers fetch_all_prices_for_year (mocked)."""
        mock_resp = MagicMock()
        mock_resp.asset = "COMBINED"
        mock_resp.year = 2023
        mock_resp.path = str(tmp_path / "combined_nok_2023.csv")
        mock_resp.rows = 365
        mock_resp.age_hours = 0.0
        mock_resp.cached = False
        mock_resp.unsupported_assets = []

        with patch("taxspine_orchestrator.prices.fetch_all_prices_for_year", return_value=mock_resp):
            resp = client.post("/prices/fetch", json={"year": 2023})
        assert resp.status_code == 200

    def test_network_failure_returns_502(self, client):
        with patch(
            "taxspine_orchestrator.prices.fetch_all_prices_for_year",
            side_effect=RuntimeError("Network error"),
        ):
            resp = client.post("/prices/fetch", json={"year": 2023})
        assert resp.status_code == 502
        assert "Network error" in resp.json()["detail"]


# ── TestNeedsRefetch ──────────────────────────────────────────────────────────


class TestNeedsRefetch:
    def test_missing_file_always_needs_fetch(self, tmp_path):
        missing = tmp_path / "no_such.csv"
        assert _needs_fetch(missing, 2025) is True

    def test_past_year_existing_file_never_refetched(self, tmp_path):
        existing = tmp_path / "old.csv"
        existing.write_text("x")
        past_year = datetime.date.today().year - 1
        assert _needs_fetch(existing, past_year) is False

    def test_current_year_fresh_file_not_refetched(self, tmp_path):
        fresh = tmp_path / "fresh.csv"
        fresh.write_text("x")
        current = datetime.date.today().year
        # File was just written so age < 24 h.
        assert _needs_fetch(fresh, current) is False

    def test_current_year_stale_file_needs_refetch(self, tmp_path):
        stale = tmp_path / "stale.csv"
        stale.write_text("x")
        current = datetime.date.today().year
        # Fake a 25-hour-old mtime.
        import time
        old_mtime = time.time() - 25 * 3600
        import os
        os.utime(stale, (old_mtime, old_mtime))
        assert _needs_fetch(stale, current) is True


# ── TestPricesAutoWiring ──────────────────────────────────────────────────────


class TestPricesAutoWiring:
    """Auto-resolve combined_nok_{year}.csv when price_table + csv_prices_path=None."""

    def _make_ok(self):
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    @patch("taxspine_orchestrator.services.subprocess.run")
    def test_auto_resolved_when_combined_csv_exists(self, mock_run, client, tmp_path):
        """Job succeeds and --csv-prices is injected from the cached combined file."""
        combined = tmp_path / "combined_nok_2025.csv"
        combined.write_text("date,asset_id,fiat_currency,price_fiat\n2025-01-01,XRP,NOK,7.5\n")

        mock_run.side_effect = [self._make_ok()]

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            mock_settings.LOT_STORE_DB = Path("/data/lots.db")
            mock_settings.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_settings.OUTPUT_DIR = Path(client.app.state.__dict__.get("_output_dir", tmp_path))

            resp = client.post("/jobs", json={**_NORWAY_BASE, "valuation_mode": "price_table"})
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"

        xrpl_cmd = mock_run.call_args_list[0][0][0]
        assert "--csv-prices" in xrpl_cmd
        idx = xrpl_cmd.index("--csv-prices")
        assert xrpl_cmd[idx + 1] == str(combined)

    def test_auto_resolve_logged(self, client, tmp_path):
        """When auto-resolved, the execution log mentions the resolved path."""
        combined = tmp_path / "combined_nok_2025.csv"
        combined.write_text("date,asset_id,fiat_currency,price_fiat\n")

        with patch("taxspine_orchestrator.services.settings") as mock_settings, \
             patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
            mock_settings.PRICES_DIR = tmp_path
            mock_settings.LOT_STORE_DB = Path("/data/lots.db")
            mock_settings.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_settings.OUTPUT_DIR = Path(tmp_path)
            mock_run.return_value = self._make_ok()

            resp = client.post("/jobs", json={**_NORWAY_BASE, "valuation_mode": "price_table"})
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "auto-resolved" in log
        assert "combined_nok_2025.csv" in log

    def test_fails_when_no_combined_csv_and_no_explicit_path(self, client, tmp_path):
        """price_table + no csv_prices_path + no cached file → FAILED, hint in error."""
        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path  # empty dir — no combined CSV
            mock_settings.LOT_STORE_DB = Path("/data/lots.db")
            mock_settings.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_settings.OUTPUT_DIR = Path(tmp_path)

            resp = client.post("/jobs", json={**_NORWAY_BASE, "valuation_mode": "price_table"})
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        err = body["output"]["error_message"]
        assert "price_table" in err
        assert "/prices/fetch" in err

    def test_explicit_csv_prices_path_takes_precedence(self, client, tmp_path):
        """When csv_prices_path is set, it is used as-is (no auto-resolution)."""
        # Create both a combined file AND an explicit prices file.
        combined = tmp_path / "combined_nok_2025.csv"
        combined.write_text("date,asset_id,fiat_currency,price_fiat\n")
        explicit = tmp_path / "my_prices.csv"
        explicit.write_text("date,asset_id,fiat_currency,price_fiat\n")

        with patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
            mock_run.return_value = self._make_ok()
            resp = client.post("/jobs", json={
                **_NORWAY_BASE,
                "valuation_mode": "price_table",
                "csv_prices_path": str(explicit),
            })
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        xrpl_cmd = mock_run.call_args_list[0][0][0]
        idx = xrpl_cmd.index("--csv-prices")
        assert xrpl_cmd[idx + 1] == str(explicit)

    def test_explicit_path_missing_still_fails(self, client):
        """An explicit csv_prices_path that does not exist → FAILED (unchanged behavior)."""
        resp = client.post("/jobs", json={
            **_NORWAY_BASE,
            "valuation_mode": "price_table",
            "csv_prices_path": "/does/not/exist.csv",
        })
        job_id = resp.json()["id"]
        body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "not found" in body["output"]["error_message"]

    def test_dry_run_auto_resolved_logs_csv_prices(self, client, tmp_path):
        """Dry-run also benefits from auto-wiring: --csv-prices appears in log."""
        combined = tmp_path / "combined_nok_2025.csv"
        combined.write_text("date,asset_id,fiat_currency,price_fiat\n")

        with patch("taxspine_orchestrator.services.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            mock_settings.LOT_STORE_DB = Path("/data/lots.db")
            mock_settings.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_settings.OUTPUT_DIR = Path(tmp_path)

            resp = client.post("/jobs", json={
                **_NORWAY_BASE,
                "valuation_mode": "price_table",
                "dry_run": True,
            })
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"
        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "--csv-prices" in log

    def test_dummy_mode_ignores_missing_combined_csv(self, client, tmp_path):
        """dummy mode never looks for combined_nok_*.csv — no auto-wiring applied."""
        # tmp_path has no combined CSV, but dummy mode should succeed.
        with patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            resp = client.post("/jobs", json={
                **_NORWAY_BASE,
                "valuation_mode": "dummy",
                "dry_run": True,
            })
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "completed"


# ── TL-12: Decimal arithmetic in price helpers ────────────────────────────────


def _make_kraken_response(pair: str, year: int, close: str = "100.50") -> bytes:
    """Minimal Kraken OHLC JSON response for one candle."""
    import datetime as _dt
    ts = int(_dt.datetime(year, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
    body = {
        "error": [],
        "result": {
            pair: [[ts, "99.0", "101.0", "98.0", close, "100.0", "50.0", 100]],
            "last": ts,
        },
    }
    return json.dumps(body).encode()


def _make_norges_bank_response(year: int, rate: str = "10.50") -> bytes:
    """Minimal Norges Bank SDMX-JSON response for one date."""
    date_str = f"{year}-06-01"
    body = {
        "data": {
            "structure": {
                "dimensions": {
                    "observation": [
                        {"values": [{"id": date_str}]}
                    ]
                }
            },
            "dataSets": [
                {
                    "series": {
                        "0:0:0:0": {
                            "observations": {"0": [rate]}
                        }
                    }
                }
            ],
        }
    }
    return json.dumps(body).encode()


def _urlopen_ctx(raw: bytes):
    """Build a mock context manager for urllib.request.urlopen returning *raw*."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestKrakenPricesReturnDecimal:
    """TL-12 — _fetch_kraken_usd_prices must return dict[str, Decimal]."""

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_returns_decimal_values(self, mock_urlopen):
        mock_urlopen.return_value = _urlopen_ctx(_make_kraken_response("XRPUSD", 2025, "100.50"))
        result = _fetch_kraken_usd_prices("XRPUSD", 2025)
        assert result
        for price in result.values():
            assert isinstance(price, Decimal), f"Expected Decimal, got {type(price).__name__}"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_no_float_rounding_for_exact_value(self, mock_urlopen):
        """The string '0.1' is exactly representable as Decimal but not as float."""
        mock_urlopen.return_value = _urlopen_ctx(_make_kraken_response("XRPUSD", 2025, "0.1"))
        result = _fetch_kraken_usd_prices("XRPUSD", 2025)
        assert list(result.values())[0] == Decimal("0.1")

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_raises_on_api_error(self, mock_urlopen):
        body = json.dumps({"error": ["EQuery:Unknown asset pair"]}).encode()
        mock_urlopen.return_value = _urlopen_ctx(body)
        with pytest.raises(RuntimeError, match="Kraken API error"):
            _fetch_kraken_usd_prices("XRPUSD", 2025)

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_raises_when_no_candle_data_key(self, mock_urlopen):
        body = json.dumps({"error": [], "result": {"last": 0}}).encode()
        mock_urlopen.return_value = _urlopen_ctx(body)
        with pytest.raises(RuntimeError):
            _fetch_kraken_usd_prices("XRPUSD", 2025)


class TestNorgesBankPricesReturnDecimal:
    """TL-12 — _fetch_norges_bank_usd_nok must return dict[str, Decimal]."""

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_returns_decimal_values(self, mock_urlopen):
        mock_urlopen.return_value = _urlopen_ctx(_make_norges_bank_response(2025, "10.50"))
        result = _fetch_norges_bank_usd_nok(2025)
        assert result
        for rate in result.values():
            assert isinstance(rate, Decimal), f"Expected Decimal, got {type(rate).__name__}"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_no_float_rounding_for_exact_rate(self, mock_urlopen):
        mock_urlopen.return_value = _urlopen_ctx(_make_norges_bank_response(2025, "10.3"))
        result = _fetch_norges_bank_usd_nok(2025)
        assert list(result.values())[0] == Decimal("10.3")

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_raises_on_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("Connection refused")
        with pytest.raises(RuntimeError, match="Norges Bank"):
            _fetch_norges_bank_usd_nok(2025)


class TestFillCalendarGapsDecimal:
    """TL-12 — _fill_calendar_gaps must accept and return Decimal values."""

    def test_propagates_decimal_type(self):
        rates: dict[str, Decimal] = {"2025-01-02": Decimal("10.5")}  # Thursday
        filled = _fill_calendar_gaps(rates, 2025)
        assert "2025-01-04" in filled
        assert isinstance(filled["2025-01-04"], Decimal)

    def test_filled_value_equals_source(self):
        rates: dict[str, Decimal] = {"2025-01-02": Decimal("10.5")}
        filled = _fill_calendar_gaps(rates, 2025)
        assert filled["2025-01-04"] == Decimal("10.5")

    def test_empty_before_first_rate(self):
        rates: dict[str, Decimal] = {"2025-03-01": Decimal("11.0")}
        filled = _fill_calendar_gaps(rates, 2025)
        assert "2025-01-01" not in filled

    def test_covers_full_year_after_first_rate(self):
        rates: dict[str, Decimal] = {"2025-01-01": Decimal("10.0")}
        filled = _fill_calendar_gaps(rates, 2025)
        assert "2025-12-31" in filled
        assert len(filled) == 365  # 2025 is not a leap year


class TestFetchAndWriteDecimalOutput:
    """TL-12 — _fetch_and_write must use Decimal multiplication (no float)."""

    def _side_effect_factory(self, kraken_body: bytes, nb_body: bytes):
        """Return a urlopen side-effect that yields Kraken first, NB second."""
        call_count = [0]

        def side_effect(req, timeout=None):
            call_count[0] += 1
            ctx = MagicMock()
            raw = kraken_body if call_count[0] == 1 else nb_body
            ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        return side_effect

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_output_csv_has_four_decimal_places(self, mock_urlopen, tmp_path):
        """Written price_fiat column must have exactly 4 decimal places."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
        kraken_body = json.dumps({
            "error": [],
            "result": {
                "XRPUSD": [[ts, "1.0", "1.0", "1.0", "1.5", "1.0", "1.0", 1]],
                "last": ts,
            },
        }).encode()
        nb_body = json.dumps({
            "data": {
                "structure": {"dimensions": {"observation": [{"values": [{"id": "2025-06-01"}]}]}},
                "dataSets": [{"series": {"0:0:0:0": {"observations": {"0": ["10.3333"]}}}}],
            }
        }).encode()

        mock_urlopen.side_effect = self._side_effect_factory(kraken_body, nb_body)

        dest = tmp_path / "xrp_nok_2025.csv"
        _fetch_and_write("XRPUSD", "XRP", 2025, dest)

        lines = dest.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 2
        price_str = lines[1].split(",")[-1]
        assert "." in price_str
        decimal_part = price_str.split(".")[1]
        assert len(decimal_part) == 4, f"Expected 4 decimal places, got: {price_str!r}"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_decimal_multiplication_no_float_drift(self, mock_urlopen, tmp_path):
        """0.1 USD × 10.0 NOK/USD = 1.0000 NOK — exact with Decimal, drifts with float."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
        kraken_body = json.dumps({
            "error": [],
            "result": {
                "XRPUSD": [[ts, "0.1", "0.1", "0.1", "0.1", "0.1", "1.0", 1]],
                "last": ts,
            },
        }).encode()
        nb_body = json.dumps({
            "data": {
                "structure": {"dimensions": {"observation": [{"values": [{"id": "2025-06-01"}]}]}},
                "dataSets": [{"series": {"0:0:0:0": {"observations": {"0": ["10.0"]}}}}],
            }
        }).encode()

        mock_urlopen.side_effect = self._side_effect_factory(kraken_body, nb_body)

        dest = tmp_path / "xrp_nok_2025.csv"
        _fetch_and_write("XRPUSD", "XRP", 2025, dest)

        lines = dest.read_text(encoding="utf-8").splitlines()
        price_str = lines[1].split(",")[-1]
        # 0.1 * 10.0 = 1.0 exactly; stored as "1.0000"
        assert price_str == "1.0000", f"Expected '1.0000', got {price_str!r}"
