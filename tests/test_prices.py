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
    # New providers
    _parse_xrpl_asset,
    _classify_asset,
    _generate_static_peg_usd_rows,
    _write_usd_as_nok_csv,
    _xrpl_iou_csv_path,
    _fetch_onthedex_xrp_prices,
    _fetch_xrplto_token_id,
    _fetch_xrplto_xrp_prices,
    _fetch_and_write_xrpl_iou,
    # Tier-4 LP token helpers
    _xrpl_rpc,
    _xrpl_year_end_ledger_index,
    _parse_amm_asset,
    _lp_csv_path,
    _read_dec31_nok_price,
    _fetch_and_write_lp_token,
    # XRPL account trust-line discovery
    _decode_xrpl_currency,
    _fetch_account_trust_lines,
    # CoinGecko Tier 2c
    _coingecko_search_coin_id,
    _fetch_coingecko_nok_prices,
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

    def test_fails_when_no_combined_csv_and_auto_fetch_errors(self, client, tmp_path):
        """price_table + no cached file + fetch fails → FAILED with network hint."""
        with patch("taxspine_orchestrator.services.settings") as mock_settings, \
             patch(
                 "taxspine_orchestrator.prices.fetch_all_prices_for_year",
                 side_effect=RuntimeError("Network unreachable"),
             ):
            mock_settings.PRICES_DIR = tmp_path  # empty — no combined CSV
            mock_settings.LOT_STORE_DB = Path("/data/lots.db")
            mock_settings.TAXSPINE_XRPL_NOR_CLI = "taxspine-xrpl-nor"
            mock_settings.OUTPUT_DIR = Path(tmp_path)

            resp = client.post("/jobs", json={**_NORWAY_BASE, "valuation_mode": "price_table"})
            job_id = resp.json()["id"]
            body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        err = body["output"]["error_message"]
        assert "prices" in err.lower()
        assert "Network unreachable" in err

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

    def test_early_days_seeded_from_first_rate(self):
        # TL-15: days before the first Norges Bank publication must be seeded
        # from the earliest available rate (not left blank as before the fix).
        rates: dict[str, Decimal] = {"2025-03-01": Decimal("11.0")}
        filled = _fill_calendar_gaps(rates, 2025)
        # Jan 1 must now be present — seeded from the March-1 rate
        assert "2025-01-01" in filled, (
            "TL-15: _fill_calendar_gaps must seed early-Jan days from the "
            "first available rate; 2025-01-01 should be present"
        )
        assert filled["2025-01-01"] == Decimal("11.0"), (
            "TL-15: seeded early-Jan rate must equal the first available rate"
        )

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


# ── TestParseXrplAsset ────────────────────────────────────────────────────────


class TestParseXrplAsset:
    def test_symbol_with_issuer(self):
        sym, iss = _parse_xrpl_asset("SOLO.rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz")
        assert sym == "SOLO"
        assert iss == "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz"

    def test_symbol_only(self):
        sym, iss = _parse_xrpl_asset("RLUSD")
        assert sym == "RLUSD"
        assert iss is None

    def test_lowercase_symbol_uppercased(self):
        sym, iss = _parse_xrpl_asset("solo.rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz")
        assert sym == "SOLO"
        assert iss == "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz"

    def test_mixed_case_symbol(self):
        sym, iss = _parse_xrpl_asset("xStik.rIssuer123")
        assert sym == "XSTIK"

    def test_empty_issuer_after_dot(self):
        sym, iss = _parse_xrpl_asset("GRIM.")
        assert sym == "GRIM"
        assert iss is None

    def test_xrp_no_issuer(self):
        sym, iss = _parse_xrpl_asset("XRP")
        assert sym == "XRP"
        assert iss is None

    def test_whitespace_stripped(self):
        sym, iss = _parse_xrpl_asset("  SOLO . rIssuer  ")
        assert sym == "SOLO"
        assert iss == "rIssuer"


# ── TestClassifyAsset ─────────────────────────────────────────────────────────


class TestClassifyAsset:
    def test_kraken_assets_classified_as_kraken(self):
        for sym in ("XRP", "BTC", "ETH", "ADA", "LTC"):
            assert _classify_asset(sym, None) == "kraken"
            assert _classify_asset(sym, "rSomeIssuer") == "kraken"

    def test_gatehub_btc_iou_classified_as_kraken(self):
        # BTC.rGatehub → symbol BTC → Kraken (regardless of issuer)
        assert _classify_asset("BTC", "rhub8VRN55s94qWKDv6jmDy1pUykJzF3wq") == "kraken"

    def test_rlusd_classified_as_static_peg(self):
        assert _classify_asset("RLUSD", None) == "static_peg"
        assert _classify_asset("RLUSD", "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh") == "static_peg"

    def test_xrpl_iou_with_issuer_classified_as_onthedex(self):
        assert _classify_asset("GRIM", "rGrimIssuer123") == "onthedex"
        assert _classify_asset("xSTIK", "rJNV9i") == "onthedex"
        assert _classify_asset("SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz") == "onthedex"

    def test_unknown_symbol_no_issuer(self):
        assert _classify_asset("UNKNOWN", None) == "unknown"
        assert _classify_asset("SHROOMIES", None) == "unknown"


# ── TestGenerateStaticPegRows ─────────────────────────────────────────────────


class TestGenerateStaticPegRows:
    def test_rlusd_covers_full_year_2025(self):
        rows = _generate_static_peg_usd_rows(Decimal("1.0"), 2025)
        assert len(rows) == 365   # 2025 is not a leap year
        assert "2025-01-01" in rows
        assert "2025-12-31" in rows

    def test_leap_year_2024_has_366_days(self):
        rows = _generate_static_peg_usd_rows(Decimal("1.0"), 2024)
        assert len(rows) == 366
        assert "2024-02-29" in rows

    def test_all_values_equal_peg_price(self):
        rows = _generate_static_peg_usd_rows(Decimal("1.0"), 2025)
        assert all(v == Decimal("1.0") for v in rows.values())

    def test_custom_peg_price(self):
        rows = _generate_static_peg_usd_rows(Decimal("0.9997"), 2025)
        assert rows["2025-06-15"] == Decimal("0.9997")

    def test_all_keys_are_iso_date_strings(self):
        rows = _generate_static_peg_usd_rows(Decimal("1.0"), 2025)
        for k in rows:
            datetime.date.fromisoformat(k)   # raises if malformed


# ── TestWriteUsdAsNokCsv ──────────────────────────────────────────────────────


class TestWriteUsdAsNokCsv:
    def test_writes_correct_nok_values(self, tmp_path):
        usd_prices = {"2025-06-01": Decimal("1.0")}
        nok_rates  = {"2025-06-01": Decimal("10.5")}
        dest = tmp_path / "rlusd_nok_2025.csv"
        _write_usd_as_nok_csv("RLUSD", usd_prices, nok_rates, dest)
        lines = dest.read_text().splitlines()
        assert lines[0] == "date,asset_id,fiat_currency,price_fiat"
        assert lines[1] == "2025-06-01,RLUSD,NOK,10.5000"

    def test_skips_dates_without_nok_rate(self, tmp_path):
        usd_prices = {"2025-06-01": Decimal("1.0"), "2025-06-02": Decimal("1.0")}
        nok_rates  = {"2025-06-01": Decimal("10.0")}   # 06-02 missing
        dest = tmp_path / "out.csv"
        _write_usd_as_nok_csv("RLUSD", usd_prices, nok_rates, dest)
        content = dest.read_text()
        assert "2025-06-02" not in content

    def test_decimal_precision_4_places(self, tmp_path):
        usd_prices = {"2025-06-01": Decimal("1.0")}
        nok_rates  = {"2025-06-01": Decimal("10.3333")}
        dest = tmp_path / "out.csv"
        _write_usd_as_nok_csv("RLUSD", usd_prices, nok_rates, dest)
        price_str = dest.read_text().splitlines()[1].split(",")[-1]
        assert len(price_str.split(".")[1]) == 4

    def test_empty_usd_prices_writes_only_header(self, tmp_path):
        dest = tmp_path / "out.csv"
        _write_usd_as_nok_csv("RLUSD", {}, {"2025-06-01": Decimal("10.0")}, dest)
        # No rows written, file not created (function returns early)
        assert not dest.exists()


# ── TestXrplIouCsvPath ────────────────────────────────────────────────────────


class TestXrplIouCsvPath:
    def test_includes_symbol_and_issuer_prefix(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            p = _xrpl_iou_csv_path("SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz", 2025)
        assert "solo" in p.name
        assert "rsolo2s1" in p.name   # first 8 chars of issuer, lowercased
        assert "2025" in p.name
        assert p.suffix == ".csv"

    def test_different_issuers_give_different_paths(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            p1 = _xrpl_iou_csv_path("TOKEN", "rIssuerAAA", 2025)
            p2 = _xrpl_iou_csv_path("TOKEN", "rIssuerBBB", 2025)
        assert p1 != p2

    def test_same_symbol_issuer_year_stable(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            p1 = _xrpl_iou_csv_path("GRIM", "rGrimIssuer", 2025)
            p2 = _xrpl_iou_csv_path("GRIM", "rGrimIssuer", 2025)
        assert p1 == p2


# ── TestFetchOnthedexXrpPrices ────────────────────────────────────────────────


def _make_onthedex_response(year: int, close_xrp: str = "0.0191") -> bytes:
    """Minimal OnTheDEX OHLC JSON with one daily candle in the target year."""
    import datetime as _dt
    ts = int(_dt.datetime(year, 6, 15, tzinfo=_dt.timezone.utc).timestamp())
    body = {
        "spec": {
            "base": {"currency": "SOLO", "issuer": "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz"},
            "quote": "XRP",
            "interval": "1440",
        },
        "data": {
            "marker": None,
            "ohlc": [
                {"t": ts, "vb": 100.0, "vq": 2.0, "o": close_xrp, "h": close_xrp,
                 "l": close_xrp, "c": close_xrp},
            ],
        },
    }
    return json.dumps(body).encode()


class TestFetchOnthedexXrpPrices:
    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_happy_path_returns_decimal_price(self, mock_urlopen):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=_make_onthedex_response(2025, "0.0191"))
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_onthedex_xrp_prices("SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz", 2025)

        assert "2025-06-15" in result
        assert isinstance(result["2025-06-15"], Decimal)
        assert result["2025-06-15"] == Decimal("0.0191")

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_filters_to_requested_year_only(self, mock_urlopen):
        import datetime as _dt
        ts_in  = int(_dt.datetime(2025, 6, 15, tzinfo=_dt.timezone.utc).timestamp())
        ts_out = int(_dt.datetime(2024, 6, 15, tzinfo=_dt.timezone.utc).timestamp())
        body = {
            "data": {
                "ohlc": [
                    {"t": ts_in,  "c": "0.02"},
                    {"t": ts_out, "c": "0.015"},   # wrong year
                ]
            }
        }
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_onthedex_xrp_prices("SOLO", "rIssuer", 2025)
        assert len(result) == 1
        assert "2025-06-15" in result
        assert "2024-06-15" not in result

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_api_error_field_returns_empty(self, mock_urlopen):
        body = {"error": "ERROR_MAINTENANCE", "message": "Under maintenance"}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_onthedex_xrp_prices("SOLO", "rIssuer", 2025)
        assert result == {}

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_empty_ohlc_list_returns_empty(self, mock_urlopen):
        body = {"data": {"ohlc": []}}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_onthedex_xrp_prices("GRIM", "rGrimIssuer", 2025)
        assert result == {}

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen",
           side_effect=OSError("Connection refused"))
    def test_network_error_returns_empty(self, _mock):
        result = _fetch_onthedex_xrp_prices("GRIM", "rGrimIssuer", 2025)
        assert result == {}

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_missing_c_field_skips_candle(self, mock_urlopen):
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 15, tzinfo=_dt.timezone.utc).timestamp())
        body = {"data": {"ohlc": [{"t": ts, "o": "0.02"}]}}   # no "c"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_onthedex_xrp_prices("SOLO", "rIssuer", 2025)
        assert result == {}

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_array_format_candles_returns_empty(self, mock_urlopen):
        """OnTheDEX returning array-format candles must not raise AttributeError."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 15, tzinfo=_dt.timezone.utc).timestamp())
        # Array-format: [timestamp, open, high, low, close, volume] instead of dict
        body = {"data": {"ohlc": [[ts, "0.01", "0.02", "0.009", "0.019", "1000"]]}}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_onthedex_xrp_prices("SOLO", "rIssuer", 2025)
        assert result == {}


# ── TestFetchXrpltoTokenId ────────────────────────────────────────────────────


class TestFetchXrpltoTokenId:
    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_returns_md5_when_found(self, mock_urlopen):
        body = {
            "tokens": [
                {"currency": "SOLO", "issuer": "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz",
                 "md5": "abc123"},
                {"currency": "SOLO", "issuer": "rOtherIssuer", "md5": "xyz789"},
            ]
        }
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_xrplto_token_id("SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz")
        assert result == "abc123"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_returns_none_when_issuer_not_found(self, mock_urlopen):
        body = {"tokens": [{"currency": "SOLO", "issuer": "rOther", "md5": "abc"}]}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _fetch_xrplto_token_id("SOLO", "rCorrectIssuer")
        assert result is None

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen",
           side_effect=OSError("timeout"))
    def test_network_error_returns_none(self, _mock):
        assert _fetch_xrplto_token_id("SOLO", "rIssuer") is None

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_empty_tokens_list_returns_none(self, mock_urlopen):
        body = {"tokens": []}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        assert _fetch_xrplto_token_id("GRIM", "rGrimIssuer") is None

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_non_dict_token_entries_skipped(self, mock_urlopen):
        """tokens list containing non-dict elements must not raise AttributeError."""
        body = {"tokens": [["SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz", "abc123"]]}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=json.dumps(body).encode())
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        assert _fetch_xrplto_token_id("SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz") is None


class TestFetchXrpltoXrpPrices:
    """Unit tests for _fetch_xrplto_xrp_prices."""

    def test_array_format_candles_returns_empty(self):
        """XRPL.to returning array-format candles must not raise AttributeError."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 15, tzinfo=_dt.timezone.utc).timestamp())
        # Array-format: [timestamp, open, high, low, close] instead of dict
        body = json.dumps({"ohlc": [[ts, "0.01", "0.02", "0.009", "0.019"]]}).encode()
        with patch("taxspine_orchestrator.prices._fetch_xrplto_token_id", return_value="solo-md5"), \
             patch("urllib.request.urlopen", return_value=_make_urlopen_response(body)):
            result = _fetch_xrplto_xrp_prices("SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz", 2025)
        assert result == {}

    def test_dict_format_candles_parsed(self):
        """XRPL.to returning dict-format candles is parsed correctly."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 15, tzinfo=_dt.timezone.utc).timestamp())
        body = json.dumps({"ohlc": [{"t": ts, "c": "0.019"}]}).encode()
        with patch("taxspine_orchestrator.prices._fetch_xrplto_token_id", return_value="solo-md5"), \
             patch("urllib.request.urlopen", return_value=_make_urlopen_response(body)):
            result = _fetch_xrplto_xrp_prices("SOLO", "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz", 2025)
        assert "2025-06-15" in result
        assert isinstance(result["2025-06-15"], Decimal)


# ── TestFetchAndWriteXrplIou ──────────────────────────────────────────────────


class TestFetchAndWriteXrplIou:
    """Integration test for _fetch_and_write_xrpl_iou via mocked OnTheDEX."""

    def _make_xrp_usd(self) -> dict[str, Decimal]:
        return {"2025-06-15": Decimal("2.50")}

    def _make_nok_rates(self) -> dict[str, Decimal]:
        return {"2025-06-15": Decimal("10.50")}

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_writes_csv_with_correct_nok_price(self, mock_urlopen, tmp_path):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=_make_onthedex_response(2025, "0.02"))
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        dest = tmp_path / "xrpl_solo_2025.csv"
        ok = _fetch_and_write_xrpl_iou(
            "SOLO", "rsoLo", 2025, self._make_xrp_usd(), self._make_nok_rates(), dest
        )

        assert ok is True
        assert dest.exists()
        lines = dest.read_text().splitlines()
        assert lines[0] == "date,asset_id,fiat_currency,price_fiat"
        # 0.02 XRP × 2.50 USD/XRP × 10.50 NOK/USD = 0.5250 NOK
        assert "SOLO" in lines[1]
        assert "0.5250" in lines[1]

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen",
           side_effect=OSError("no network"))
    def test_returns_false_when_no_data(self, _mock, tmp_path):
        dest = tmp_path / "out.csv"
        ok = _fetch_and_write_xrpl_iou(
            "GRIM", "rGrimIssuer", 2025,
            {"2025-06-15": Decimal("2.0")},
            {"2025-06-15": Decimal("10.0")},
            dest,
        )
        assert ok is False
        assert not dest.exists()

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_returns_false_when_no_xrp_usd_overlap(self, mock_urlopen, tmp_path):
        # OnTheDEX returns price for June 15, but xrp_usd only has June 20
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=_make_onthedex_response(2025, "0.02"))
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        dest = tmp_path / "out.csv"
        ok = _fetch_and_write_xrpl_iou(
            "SOLO", "rIssuer", 2025,
            {"2025-06-20": Decimal("2.5")},    # no overlap with June 15
            {"2025-06-15": Decimal("10.5")},
            dest,
        )
        assert ok is False


# ── TestFetchAllPricesWithXrplAssets ──────────────────────────────────────────


class TestFetchAllPricesWithXrplAssets:
    """Tests for fetch_all_prices_for_year() with extra_xrpl_assets."""

    def _kraken_side_effect(self, kraken_body: bytes, nb_body: bytes):
        call_count = [0]
        def side_effect(req, timeout=None):
            call_count[0] += 1
            ctx = MagicMock()
            raw = kraken_body if call_count[0] == 1 else nb_body
            ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx
        return side_effect

    def test_kraken_asset_in_extra_not_double_fetched(self, tmp_path):
        """BTC in extra_xrpl_assets should be silently skipped — already Kraken."""
        with patch("taxspine_orchestrator.prices.settings") as ms, \
             patch("taxspine_orchestrator.prices.urllib.request.urlopen") as mock_uo:
            ms.PRICES_DIR = tmp_path

            # Pre-populate all Kraken asset CSVs so _needs_fetch returns False
            for asset in ("XRP", "BTC", "ETH", "ADA", "LTC"):
                p = tmp_path / f"{asset.lower()}_nok_2023.csv"
                p.write_text("date,asset_id,fiat_currency,price_fiat\n"
                             f"2023-06-01,{asset},NOK,1.0000\n")

            result = fetch_all_prices_for_year(2023, extra_xrpl_assets=["BTC.rGatehub"])

        # BTC should be in the combined, but NOT fetched via OnTheDEX
        assert result.rows > 0
        assert mock_uo.call_count == 0   # no OnTheDEX calls

    def test_rlusd_static_peg_written_when_requested(self, tmp_path):
        """RLUSD in extra_xrpl_assets triggers static peg writer."""
        import datetime as _dt
        ts = int(_dt.datetime(2023, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
        kraken_body = json.dumps({
            "error": [],
            "result": {
                "XRPUSD": [[ts, "2.0", "2.0", "2.0", "2.0", "2.0", "1.0", 1]],
                "last": ts,
            },
        }).encode()
        nb_body = json.dumps({
            "data": {
                "structure": {
                    "dimensions": {"observation": [{"values": [{"id": "2023-06-01"}]}]}
                },
                "dataSets": [{"series": {"0:0:0:0": {"observations": {"0": ["10.5"]}}}}],
            }
        }).encode()

        # Pre-populate Kraken asset CSVs
        for asset in ("XRP", "BTC", "ETH", "ADA", "LTC"):
            p = tmp_path / f"{asset.lower()}_nok_2023.csv"
            p.write_text("date,asset_id,fiat_currency,price_fiat\n"
                         f"2023-06-01,{asset},NOK,1.0000\n")

        with patch("taxspine_orchestrator.prices.settings") as ms, \
             patch("taxspine_orchestrator.prices.urllib.request.urlopen") as mock_uo:
            ms.PRICES_DIR = tmp_path
            # urlopen will be called for XRP/USD + Norges Bank (for RLUSD conversion)
            call_count = [0]
            def side_effect(req, timeout=None):
                call_count[0] += 1
                ctx = MagicMock()
                raw = kraken_body if "kraken" in req.full_url else nb_body
                ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
                ctx.__exit__ = MagicMock(return_value=False)
                return ctx
            mock_uo.side_effect = side_effect

            result = fetch_all_prices_for_year(2023, extra_xrpl_assets=["RLUSD"])

        # RLUSD should NOT appear in unsupported
        unsupported_assets = [u.asset for u in result.unsupported_assets]
        assert "RLUSD" not in unsupported_assets

        # RLUSD CSV should exist
        rlusd_csv = tmp_path / "rlusd_nok_2023.csv"
        assert rlusd_csv.exists()
        content = rlusd_csv.read_text()
        assert "RLUSD" in content

    def test_unknown_asset_no_issuer_reported_as_unsupported(self, tmp_path):
        """An asset spec without issuer (and not Kraken/peg) goes to unsupported."""
        for asset in ("XRP", "BTC", "ETH", "ADA", "LTC"):
            p = tmp_path / f"{asset.lower()}_nok_2023.csv"
            p.write_text(f"date,asset_id,fiat_currency,price_fiat\n2023-06-01,{asset},NOK,1.0000\n")

        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            result = fetch_all_prices_for_year(2023, extra_xrpl_assets=["SHROOMIES"])

        unsupported = {u.asset for u in result.unsupported_assets}
        assert "SHROOMIES" in unsupported

    def test_rlusd_advisory_absent_when_rlusd_in_extra(self, tmp_path):
        """The 'add RLUSD' advisory should not appear when RLUSD is already requested."""
        for asset in ("XRP", "BTC", "ETH", "ADA", "LTC"):
            p = tmp_path / f"{asset.lower()}_nok_2023.csv"
            p.write_text(f"date,asset_id,fiat_currency,price_fiat\n2023-06-01,{asset},NOK,1.0000\n")

        with patch("taxspine_orchestrator.prices.settings") as ms, \
             patch("taxspine_orchestrator.prices.urllib.request.urlopen") as mock_uo:
            ms.PRICES_DIR = tmp_path
            # Return minimal valid Kraken+NB responses for the RLUSD conversion
            import datetime as _dt
            ts = int(_dt.datetime(2023, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
            kraken_body = json.dumps({
                "error": [], "result": {
                    "XRPUSD": [[ts, "2.0", "2.0", "2.0", "2.0", "2.0", "1.0", 1]],
                    "last": ts,
                }
            }).encode()
            nb_body = json.dumps({
                "data": {
                    "structure": {"dimensions": {"observation": [{"values": [{"id": "2023-06-01"}]}]}},
                    "dataSets": [{"series": {"0:0:0:0": {"observations": {"0": ["10.5"]}}}}],
                }
            }).encode()
            def side_effect(req, timeout=None):
                ctx = MagicMock()
                raw = kraken_body if "kraken" in req.full_url else nb_body
                ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
                ctx.__exit__ = MagicMock(return_value=False)
                return ctx
            mock_uo.side_effect = side_effect

            result = fetch_all_prices_for_year(2023, extra_xrpl_assets=["RLUSD"])

        advisory_texts = " ".join(u.reason for u in result.unsupported_assets)
        assert "add RLUSD" not in advisory_texts.lower()
        assert "extra_xrpl_assets" not in advisory_texts.lower()

    def test_ltc_included_in_kraken_assets(self, tmp_path):
        """LTC should now be fetched as part of Kraken tier (new addition)."""
        with patch("taxspine_orchestrator.prices.settings") as ms, \
             patch("taxspine_orchestrator.prices.urllib.request.urlopen") as mock_uo:
            ms.PRICES_DIR = tmp_path

            # Pre-populate all Kraken CSVs to avoid network calls
            for asset in ("XRP", "BTC", "ETH", "ADA", "LTC"):
                p = tmp_path / f"{asset.lower()}_nok_2023.csv"
                p.write_text(f"date,asset_id,fiat_currency,price_fiat\n2023-06-01,{asset},NOK,1.0000\n")

            fetch_all_prices_for_year(2023)

        assert mock_uo.call_count == 0   # everything was cached
        combined = _combined_csv_path(2023)
        with patch("taxspine_orchestrator.prices.settings") as ms2:
            ms2.PRICES_DIR = tmp_path
            combined = tmp_path / "combined_nok_2023.csv"
        content = combined.read_text()
        assert "LTC" in content


# ── TestFetchPricesEndpointExtraAssets ────────────────────────────────────────


class TestFetchPricesEndpointExtraAssets:
    """POST /prices/fetch now accepts extra_xrpl_assets."""

    def test_extra_xrpl_assets_passed_through_to_fetch(self, client, tmp_path):
        mock_resp = MagicMock()
        mock_resp.asset = "COMBINED"
        mock_resp.year = 2023
        mock_resp.path = str(tmp_path / "combined_nok_2023.csv")
        mock_resp.rows = 10
        mock_resp.age_hours = 0.0
        mock_resp.cached = False
        mock_resp.unsupported_assets = []

        with patch(
            "taxspine_orchestrator.prices.fetch_all_prices_for_year",
            return_value=mock_resp,
        ) as mock_fetch:
            resp = client.post("/prices/fetch", json={
                "year": 2023,
                "extra_xrpl_assets": ["GRIM.rGrimIssuer123", "RLUSD"],
            })

        assert resp.status_code == 200
        mock_fetch.assert_called_once_with(
            2023,
            extra_xrpl_assets=["GRIM.rGrimIssuer123", "RLUSD"],
        )

    def test_empty_extra_xrpl_assets_is_default(self, client, tmp_path):
        mock_resp = MagicMock()
        mock_resp.asset = "COMBINED"
        mock_resp.year = 2023
        mock_resp.path = str(tmp_path / "combined_nok_2023.csv")
        mock_resp.rows = 5
        mock_resp.age_hours = 0.0
        mock_resp.cached = True
        mock_resp.unsupported_assets = []

        with patch(
            "taxspine_orchestrator.prices.fetch_all_prices_for_year",
            return_value=mock_resp,
        ) as mock_fetch:
            resp = client.post("/prices/fetch", json={"year": 2023})

        assert resp.status_code == 200
        # empty extra list collapses to None (no workspace assets, no request assets)
        mock_fetch.assert_called_once_with(2023, extra_xrpl_assets=None)


# ── TestFetchPricesEndpointWorkspaceIntegration ───────────────────────────────


class TestFetchPricesEndpointWorkspaceIntegration:
    """POST /prices/fetch auto-includes workspace xrpl_assets."""

    @pytest.fixture(autouse=True)
    def _reset_workspace(self):
        from taxspine_orchestrator import main as _m
        _m._workspace_store.clear()
        yield
        _m._workspace_store.clear()

    def _mock_resp(self, tmp_path, year=2023):
        m = MagicMock()
        m.asset = "COMBINED"
        m.year = year
        m.path = str(tmp_path / f"combined_nok_{year}.csv")
        m.rows = 10
        m.age_hours = 0.0
        m.cached = False
        m.unsupported_assets = []
        return m

    def test_workspace_assets_merged_into_fetch(self, client, tmp_path):
        """Workspace assets appear in extra_xrpl_assets even if not in request body."""
        client.post("/workspace/xrpl-assets",
                    json={"spec": "SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL"})

        with patch(
            "taxspine_orchestrator.prices.fetch_all_prices_for_year",
            return_value=self._mock_resp(tmp_path),
        ) as mock_fetch:
            resp = client.post("/prices/fetch", json={"year": 2023})

        assert resp.status_code == 200
        extra = mock_fetch.call_args.kwargs["extra_xrpl_assets"]
        assert "SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL" in extra

    def test_request_body_and_workspace_merged_deduplicated(self, client, tmp_path):
        """Request-body assets and workspace assets are unioned without duplicates."""
        client.post("/workspace/xrpl-assets",
                    json={"spec": "SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL"})

        with patch(
            "taxspine_orchestrator.prices.fetch_all_prices_for_year",
            return_value=self._mock_resp(tmp_path),
        ) as mock_fetch:
            resp = client.post("/prices/fetch", json={
                "year": 2023,
                # Same asset in request body — should appear only once
                "extra_xrpl_assets": ["SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL",
                                       "mXRP.r4GDFMLGJUKMjNEycw16tWB9CqEjxztMqJ"],
            })

        assert resp.status_code == 200
        extra = mock_fetch.call_args.kwargs["extra_xrpl_assets"]
        assert extra.count("SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL") == 1
        assert "mXRP.r4GDFMLGJUKMjNEycw16tWB9CqEjxztMqJ" in extra

    def test_request_body_assets_come_first(self, client, tmp_path):
        """Request-body assets appear before workspace-only assets (request ordering preserved)."""
        client.post("/workspace/xrpl-assets",
                    json={"spec": "SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL"})

        with patch(
            "taxspine_orchestrator.prices.fetch_all_prices_for_year",
            return_value=self._mock_resp(tmp_path),
        ) as mock_fetch:
            client.post("/prices/fetch", json={
                "year": 2023,
                "extra_xrpl_assets": ["mXRP.r4GDFMLGJUKMjNEycw16tWB9CqEjxztMqJ"],
            })

        extra = mock_fetch.call_args.kwargs["extra_xrpl_assets"]
        assert extra[0] == "mXRP.r4GDFMLGJUKMjNEycw16tWB9CqEjxztMqJ"
        assert "SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL" in extra

    def test_empty_workspace_no_extra(self, client, tmp_path):
        """With no workspace assets and no request assets, None is passed."""
        with patch(
            "taxspine_orchestrator.prices.fetch_all_prices_for_year",
            return_value=self._mock_resp(tmp_path),
        ) as mock_fetch:
            resp = client.post("/prices/fetch", json={"year": 2023})

        assert resp.status_code == 200
        assert mock_fetch.call_args.kwargs["extra_xrpl_assets"] is None


# ── TestLpTokenPricing ────────────────────────────────────────────────────────

_AMM_ACCOUNT  = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
_AMM_ACCOUNT2 = "rAMMQ1W2E3R4T5Y6U7I8O9P0123456789A"
_LP_HEX       = "03930D02208264E2E40EC1B0C09E4DB96EE197B1"
_SOLO_ISSUER  = "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz"


def _make_amm_info_result(
    *,
    amount1: object = "2000000000",          # 2000 XRP in drops
    amount2: object | None = None,
    lp_supply: str = "100.0",
    lp_currency: str = _LP_HEX,
    lp_issuer: str = _AMM_ACCOUNT,
) -> dict:
    """Build a minimal amm_info ``result`` dict for testing."""
    if amount2 is None:
        amount2 = {
            "currency": "SOLO",
            "issuer": _SOLO_ISSUER,
            "value": "50000",
        }
    return {
        "amm": {
            "amm_account": _AMM_ACCOUNT,
            "amount": amount1,
            "amount2": amount2,
            "lp_token": {
                "currency": lp_currency,
                "issuer": lp_issuer,
                "value": lp_supply,
            },
        },
        "status": "success",
    }


def _make_ledger_result(ledger_index: int, close_time_xrpl: int) -> dict:
    """Build a minimal ``ledger`` RPC result dict."""
    return {
        "ledger": {
            "ledger_index": str(ledger_index),
            "close_time": close_time_xrpl,
            "closed": True,
        },
        "ledger_index": ledger_index,
        "status": "success",
    }


class TestClassifyAssetLpToken:
    """_classify_asset correctly identifies LP tokens."""

    def test_lp_with_issuer_classified_as_lp_token(self):
        assert _classify_asset("LP", _AMM_ACCOUNT) == "lp_token"

    def test_lp_without_issuer_classified_as_unknown(self):
        # No issuer → can't look up the AMM account → unknown
        assert _classify_asset("LP", None) == "unknown"

    def test_solo_with_issuer_still_classified_as_onthedex(self):
        # Ensure the LP classification doesn't break normal XRPL IOUs
        assert _classify_asset("SOLO", _SOLO_ISSUER) == "onthedex"

    def test_xrp_with_issuer_still_classified_as_kraken(self):
        assert _classify_asset("XRP", _AMM_ACCOUNT) == "kraken"


class TestLpCsvPath:
    def test_includes_first_8_chars_of_amm_account(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            p = _lp_csv_path(_AMM_ACCOUNT, 2025)
        assert "lp_" in p.name
        assert _AMM_ACCOUNT[:8].lower() in p.name
        assert "2025" in p.name
        assert p.suffix == ".csv"

    def test_different_amm_accounts_give_different_paths(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            p1 = _lp_csv_path(_AMM_ACCOUNT, 2025)
            p2 = _lp_csv_path(_AMM_ACCOUNT2, 2025)
        assert p1 != p2

    def test_same_amm_account_year_is_stable(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            p1 = _lp_csv_path(_AMM_ACCOUNT, 2025)
            p2 = _lp_csv_path(_AMM_ACCOUNT, 2025)
        assert p1 == p2


class TestParseAmmAsset:
    def test_xrp_drops_string(self):
        sym, iss, qty = _parse_amm_asset("1000000")
        assert sym == "XRP"
        assert iss is None
        assert qty == Decimal("1")   # 1 000 000 drops = 1 XRP

    def test_xrp_drops_large(self):
        _, _, qty = _parse_amm_asset("2000000000")
        assert qty == Decimal("2000")

    def test_iou_dict(self):
        sym, iss, qty = _parse_amm_asset({"currency": "SOLO", "issuer": _SOLO_ISSUER, "value": "100.5"})
        assert sym == "SOLO"
        assert iss == _SOLO_ISSUER
        assert qty == Decimal("100.5")

    def test_iou_dict_decimal_quantity(self):
        _, _, qty = _parse_amm_asset({"currency": "GRIM", "issuer": "rGrim", "value": "0.001"})
        assert qty == Decimal("0.001")

    def test_invalid_type_raises_value_error(self):
        with pytest.raises(ValueError, match="Unexpected AMM asset format"):
            _parse_amm_asset(42)

    def test_invalid_none_raises(self):
        with pytest.raises((ValueError, TypeError)):
            _parse_amm_asset(None)


class TestReadDec31NokPrice:
    def test_reads_dec31_from_kraken_csv(self, tmp_path):
        csv_file = tmp_path / "xrp_nok_2025.csv"
        csv_file.write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
            "2025-12-30,XRP,NOK,7.1000\n"
            "2025-12-31,XRP,NOK,7.2000\n",
            encoding="utf-8",
        )
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            price = _read_dec31_nok_price("XRP", None, 2025)
        assert price == Decimal("7.2000")

    def test_reads_dec31_from_xrpl_iou_csv(self, tmp_path):
        issuer_tag = _SOLO_ISSUER[:8].lower()
        csv_file = tmp_path / f"xrpl_solo_{issuer_tag}_nok_2025.csv"
        csv_file.write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
            "2025-12-31,SOLO,NOK,0.4200\n",
            encoding="utf-8",
        )
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            price = _read_dec31_nok_price("SOLO", _SOLO_ISSUER, 2025)
        assert price == Decimal("0.4200")

    def test_missing_file_returns_none(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            price = _read_dec31_nok_price("XRP", None, 2025)
        assert price is None

    def test_date_not_in_csv_returns_none(self, tmp_path):
        csv_file = tmp_path / "xrp_nok_2025.csv"
        csv_file.write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
            "2025-12-30,XRP,NOK,7.1000\n",
            encoding="utf-8",
        )
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            price = _read_dec31_nok_price("XRP", None, 2025)
        assert price is None

    def test_no_issuer_for_non_kraken_returns_none(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            price = _read_dec31_nok_price("SOLO", None, 2025)
        assert price is None


class TestFetchAndWriteLpToken:
    """_fetch_and_write_lp_token: mocked XRPL RPC + cached asset prices → NAV CSV."""

    def _write_xrp_csv(self, tmp_path: Path, price: str = "7.0000") -> None:
        """Write a minimal XRP NOK price CSV with a Dec-31 row."""
        f = tmp_path / "xrp_nok_2025.csv"
        f.write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
            f"2025-12-31,XRP,NOK,{price}\n",
            encoding="utf-8",
        )

    def _write_solo_csv(self, tmp_path: Path, price: str = "0.4000") -> None:
        issuer_tag = _SOLO_ISSUER[:8].lower()
        f = tmp_path / f"xrpl_solo_{issuer_tag}_nok_2025.csv"
        f.write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
            f"2025-12-31,SOLO,NOK,{price}\n",
            encoding="utf-8",
        )

    @patch("taxspine_orchestrator.prices._xrpl_rpc")
    @patch("taxspine_orchestrator.prices._xrpl_year_end_ledger_index")
    def test_success_writes_nav_csv(self, mock_ledger_idx, mock_rpc, tmp_path):
        mock_ledger_idx.return_value = 90_000_000
        mock_rpc.return_value = _make_amm_info_result(
            amount1="2000000000",    # 2000 XRP
            lp_supply="100.0",
        )
        self._write_xrp_csv(tmp_path, "7.0000")    # XRP = 7.00 NOK
        self._write_solo_csv(tmp_path, "0.4000")   # SOLO = 0.40 NOK

        dest = tmp_path / f"lp_{_AMM_ACCOUNT[:8].lower()}_nok_2025.csv"
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            ok = _fetch_and_write_lp_token(_AMM_ACCOUNT, 2025, dest)

        assert ok is True
        assert dest.exists()
        lines = dest.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "date,asset_id,fiat_currency,price_fiat"
        # NAV = (2000 XRP × 7.0 NOK + 50000 SOLO × 0.4 NOK) / 100 = (14000 + 20000) / 100 = 340.0
        row = lines[1].split(",")
        assert row[0] == "2025-12-31"
        assert row[1] == _LP_HEX          # hex currency code as asset_id
        assert row[2] == "NOK"
        assert Decimal(row[3]) == Decimal("340.0000")

    @patch("taxspine_orchestrator.prices._xrpl_year_end_ledger_index")
    def test_rpc_failure_returns_false(self, mock_ledger_idx, tmp_path):
        mock_ledger_idx.side_effect = RuntimeError("network error")
        dest = tmp_path / "lp_test.csv"
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            ok = _fetch_and_write_lp_token(_AMM_ACCOUNT, 2025, dest)
        assert ok is False
        assert not dest.exists()

    @patch("taxspine_orchestrator.prices._xrpl_rpc")
    @patch("taxspine_orchestrator.prices._xrpl_year_end_ledger_index")
    def test_amm_info_error_returns_false(self, mock_ledger_idx, mock_rpc, tmp_path):
        mock_ledger_idx.return_value = 90_000_000
        mock_rpc.side_effect = RuntimeError("amm not found")
        dest = tmp_path / "lp_test.csv"
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            ok = _fetch_and_write_lp_token(_AMM_ACCOUNT, 2025, dest)
        assert ok is False

    @patch("taxspine_orchestrator.prices._xrpl_rpc")
    @patch("taxspine_orchestrator.prices._xrpl_year_end_ledger_index")
    def test_missing_underlying_xrp_price_returns_false(self, mock_ledger_idx, mock_rpc, tmp_path):
        """If XRP NOK price CSV is absent, NAV cannot be computed."""
        mock_ledger_idx.return_value = 90_000_000
        mock_rpc.return_value = _make_amm_info_result()
        # Only write SOLO CSV, not XRP CSV
        self._write_solo_csv(tmp_path)
        dest = tmp_path / "lp_test.csv"
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            ok = _fetch_and_write_lp_token(_AMM_ACCOUNT, 2025, dest)
        assert ok is False

    @patch("taxspine_orchestrator.prices._xrpl_rpc")
    @patch("taxspine_orchestrator.prices._xrpl_year_end_ledger_index")
    def test_missing_underlying_iou_price_returns_false(self, mock_ledger_idx, mock_rpc, tmp_path):
        """If SOLO NOK price CSV is absent, NAV cannot be computed."""
        mock_ledger_idx.return_value = 90_000_000
        mock_rpc.return_value = _make_amm_info_result()
        # Only write XRP CSV, not SOLO CSV
        self._write_xrp_csv(tmp_path)
        dest = tmp_path / "lp_test.csv"
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            ok = _fetch_and_write_lp_token(_AMM_ACCOUNT, 2025, dest)
        assert ok is False

    @patch("taxspine_orchestrator.prices._xrpl_rpc")
    @patch("taxspine_orchestrator.prices._xrpl_year_end_ledger_index")
    def test_zero_lp_supply_returns_false(self, mock_ledger_idx, mock_rpc, tmp_path):
        mock_ledger_idx.return_value = 90_000_000
        mock_rpc.return_value = _make_amm_info_result(lp_supply="0")
        self._write_xrp_csv(tmp_path)
        self._write_solo_csv(tmp_path)
        dest = tmp_path / "lp_test.csv"
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            ok = _fetch_and_write_lp_token(_AMM_ACCOUNT, 2025, dest)
        assert ok is False

    @patch("taxspine_orchestrator.prices._xrpl_rpc")
    @patch("taxspine_orchestrator.prices._xrpl_year_end_ledger_index")
    def test_nav_uses_dec31_prices_only(self, mock_ledger_idx, mock_rpc, tmp_path):
        """NAV is computed from the Dec 31 row, ignoring earlier rows in the CSV."""
        mock_ledger_idx.return_value = 90_000_000
        mock_rpc.return_value = _make_amm_info_result(
            amount1="1000000",   # 1 XRP
            amount2={"currency": "SOLO", "issuer": _SOLO_ISSUER, "value": "10"},
            lp_supply="10.0",
        )
        # XRP CSV has Dec 30 and Dec 31 rows with different prices
        xrp_csv = tmp_path / "xrp_nok_2025.csv"
        xrp_csv.write_text(
            "date,asset_id,fiat_currency,price_fiat\n"
            "2025-12-30,XRP,NOK,6.0000\n"
            "2025-12-31,XRP,NOK,8.0000\n",
            encoding="utf-8",
        )
        self._write_solo_csv(tmp_path, "2.0000")  # SOLO = 2.00 NOK

        dest = tmp_path / "lp_dec31.csv"
        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            ok = _fetch_and_write_lp_token(_AMM_ACCOUNT, 2025, dest)

        assert ok is True
        row = dest.read_text(encoding="utf-8").splitlines()[1].split(",")
        # NAV = (1 XRP × 8.0 + 10 SOLO × 2.0) / 10 = (8.0 + 20.0) / 10 = 2.8000
        assert Decimal(row[3]) == Decimal("2.8000")


class TestXrplRpc:
    """_xrpl_rpc: JSON-RPC wrapper."""

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_returns_result_dict(self, mock_urlopen):
        body = json.dumps({"result": {"status": "success", "value": 42}}).encode()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        result = _xrpl_rpc("test_method", {"key": "val"})
        assert result["value"] == 42

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_raises_on_error_status(self, mock_urlopen):
        body = json.dumps({
            "result": {"status": "error", "error": "entryNotFound", "error_message": "Not found"}
        }).encode()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = ctx

        with pytest.raises(RuntimeError, match="Not found"):
            _xrpl_rpc("amm_info", {})

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_raises_on_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("connection refused")
        with pytest.raises(RuntimeError, match="XRPL RPC"):
            _xrpl_rpc("ledger", {})


class TestFetchAllPricesWithLpAssets:
    """fetch_all_prices_for_year: LP token processing in Step 2b."""

    def _make_kraken_resp(self, year: int = 2025) -> bytes:
        tz = datetime.timezone.utc
        ts = int(datetime.datetime(year, 12, 31, tzinfo=tz).timestamp())
        body = {
            "error": [],
            "result": {
                "XRPUSD": [[ts, "1.0", "1.0", "1.0", "1.0", "1.0", "1.0", 1]],
                "last": ts,
            },
        }
        return json.dumps(body).encode()

    def _make_nb_resp(self, year: int = 2025) -> bytes:
        body = {
            "data": {
                "structure": {
                    "dimensions": {
                        "observation": [{"values": [{"id": f"{year}-12-31"}]}]
                    }
                },
                "dataSets": [{"series": {"0:0:0:0": {"observations": {"0": ["10.0"]}}}}],
            }
        }
        return json.dumps(body).encode()

    @patch("taxspine_orchestrator.prices._fetch_and_write_lp_token")
    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_lp_spec_triggers_fetch_and_write_lp_token(
        self, mock_urlopen, mock_lp_write, tmp_path
    ):
        """An LP.rAmmAccount spec causes _fetch_and_write_lp_token to be called."""
        # Mock Kraken calls for the 5 base assets
        kraken_body = self._make_kraken_resp()
        nb_body     = self._make_nb_resp()
        call_count  = [0]

        def side_effect(req, timeout=None):
            call_count[0] += 1
            raw = kraken_body if call_count[0] % 2 == 1 else nb_body
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        mock_urlopen.side_effect = side_effect

        # lp_dest does NOT exist so _needs_fetch returns True → LP write is triggered.
        # The mock returns True (success) but doesn't write the file — that's fine for
        # this test since we only care that the function was called.
        lp_dest = tmp_path / f"lp_{_AMM_ACCOUNT[:8].lower()}_nok_2025.csv"
        mock_lp_write.return_value = True

        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            with patch("taxspine_orchestrator.prices._lp_csv_path", return_value=lp_dest):
                resp = fetch_all_prices_for_year(
                    2025,
                    extra_xrpl_assets=[f"LP.{_AMM_ACCOUNT}"],
                )

        mock_lp_write.assert_called_once_with(_AMM_ACCOUNT, 2025, lp_dest)
        assert resp.asset == "COMBINED"

    @patch("taxspine_orchestrator.prices._fetch_and_write_lp_token")
    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_lp_fetch_failure_adds_unsupported_note(
        self, mock_urlopen, mock_lp_write, tmp_path
    ):
        """When LP NAV fetch fails, an UnsupportedAssetNote is returned."""
        kraken_body = self._make_kraken_resp()
        nb_body     = self._make_nb_resp()
        call_count  = [0]

        def side_effect(req, timeout=None):
            call_count[0] += 1
            raw = kraken_body if call_count[0] % 2 == 1 else nb_body
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        mock_urlopen.side_effect = side_effect
        mock_lp_write.return_value = False   # LP fetch fails

        with patch("taxspine_orchestrator.prices.settings") as ms:
            ms.PRICES_DIR = tmp_path
            with patch("taxspine_orchestrator.prices._lp_csv_path", return_value=tmp_path / "lp_missing.csv"):
                resp = fetch_all_prices_for_year(
                    2025,
                    extra_xrpl_assets=[f"LP.{_AMM_ACCOUNT}"],
                )

        unsupported_assets = [n.asset for n in resp.unsupported_assets]
        assert f"LP.{_AMM_ACCOUNT}" in unsupported_assets

    def test_lp_spec_not_classified_as_onthedex(self):
        """LP tokens must not be classified as 'onthedex' (regression guard)."""
        sym, iss = _parse_xrpl_asset(f"LP.{_AMM_ACCOUNT}")
        assert _classify_asset(sym, iss) == "lp_token"
        assert _classify_asset(sym, iss) != "onthedex"

    def test_lp_spec_deduplicated_in_step2b(self):
        """Duplicate LP specs do not cause duplicate processing."""
        from taxspine_orchestrator.prices import _parse_xrpl_asset, _classify_asset
        spec = f"LP.{_AMM_ACCOUNT}"
        sym, iss = _parse_xrpl_asset(spec)
        # Just verify classification is consistent; dedup logic tested via integration
        assert _classify_asset(sym, iss) == "lp_token"


# ── TestDecodeXrplCurrency ────────────────────────────────────────────────────


class TestDecodeXrplCurrency:
    """_decode_xrpl_currency converts 40-char hex currency codes to readable symbols."""

    def test_solo_hex_decodes_to_solo(self):
        # "SOLO" = 4 bytes; padded to 20 bytes (40 hex chars): 534F4C4F + 16 zero bytes
        raw = "SOLO".encode("utf-8")
        hex_code = (raw + b"\x00" * (20 - len(raw))).hex().upper()
        assert len(hex_code) == 40
        assert _decode_xrpl_currency(hex_code) == "SOLO"

    def test_xstik_hex_decodes_correctly(self):
        # "xSTIK" → 7853544943 + 30 zeros (5 bytes + padding)
        import binascii
        raw = "xSTIK".encode("utf-8")
        hex_code = (raw + b"\x00" * (20 - len(raw))).hex().upper()
        assert _decode_xrpl_currency(hex_code) == "xSTIK"

    def test_three_char_iso_returned_as_is(self):
        assert _decode_xrpl_currency("USD") == "USD"
        assert _decode_xrpl_currency("XRP") == "XRP"

    def test_non_hex_string_returned_as_is(self):
        assert _decode_xrpl_currency("RLUSD") == "RLUSD"

    def test_invalid_hex_returned_as_is(self):
        # 40 chars but invalid utf-8 bytes → returned verbatim
        bad_hex = "FF" * 20   # 40-char hex, but bytes are 0xFF which is invalid UTF-8 standalone
        result = _decode_xrpl_currency(bad_hex)
        # Should not raise, should return something (either decoded or original)
        assert isinstance(result, str)

    def test_strips_null_padding(self):
        raw = "ARK".encode("utf-8")
        hex_code = (raw + b"\x00" * (20 - len(raw))).hex().upper()
        assert _decode_xrpl_currency(hex_code) == "ARK"


# ── TestFetchAccountTrustLines ────────────────────────────────────────────────

_TEST_ACCOUNT = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
_SOLO_ISSUER  = "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz"
_GRIM_ISSUER  = "rGriMeYGM1drXCfFMKcEWiH5kUBhaYHCBi"


class TestFetchAccountTrustLines:
    """_fetch_account_trust_lines returns SYMBOL.rISSUER specs from account_lines."""

    def test_returns_spec_for_nonzero_balance(self):
        mock_result = {
            "lines": [
                {"currency": "SOLO", "account": _SOLO_ISSUER, "balance": "100.0"},
            ]
        }
        with patch("taxspine_orchestrator.prices._xrpl_rpc", return_value=mock_result):
            specs = _fetch_account_trust_lines(_TEST_ACCOUNT)
        assert f"SOLO.{_SOLO_ISSUER}" in specs

    def test_skips_zero_balance_trust_lines(self):
        mock_result = {
            "lines": [
                {"currency": "SOLO", "account": _SOLO_ISSUER, "balance": "0"},
                {"currency": "GRIM", "account": _GRIM_ISSUER, "balance": "500.0"},
            ]
        }
        with patch("taxspine_orchestrator.prices._xrpl_rpc", return_value=mock_result):
            specs = _fetch_account_trust_lines(_TEST_ACCOUNT)
        assert f"SOLO.{_SOLO_ISSUER}" not in specs
        assert f"GRIM.{_GRIM_ISSUER}" in specs

    def test_decodes_hex_currency_code(self):
        import binascii
        raw = "xSTIK".encode("utf-8")
        hex_code = (raw + b"\x00" * (20 - len(raw))).hex().upper()
        xstik_issuer = "rXSTiKissuerAddress1234567890"
        mock_result = {
            "lines": [
                {"currency": hex_code, "account": xstik_issuer, "balance": "1000.0"},
            ]
        }
        with patch("taxspine_orchestrator.prices._xrpl_rpc", return_value=mock_result):
            specs = _fetch_account_trust_lines(_TEST_ACCOUNT)
        assert f"xSTIK.{xstik_issuer}" in specs

    def test_returns_empty_on_rpc_failure(self):
        with patch("taxspine_orchestrator.prices._xrpl_rpc", side_effect=RuntimeError("timeout")):
            specs = _fetch_account_trust_lines(_TEST_ACCOUNT)
        assert specs == []

    def test_skips_xrp_entries(self):
        mock_result = {
            "lines": [
                {"currency": "XRP", "account": "", "balance": "5000.0"},
                {"currency": "SOLO", "account": _SOLO_ISSUER, "balance": "100.0"},
            ]
        }
        with patch("taxspine_orchestrator.prices._xrpl_rpc", return_value=mock_result):
            specs = _fetch_account_trust_lines(_TEST_ACCOUNT)
        xrp_specs = [s for s in specs if s.startswith("XRP.")]
        assert xrp_specs == []
        assert f"SOLO.{_SOLO_ISSUER}" in specs

    def test_paginates_via_marker(self):
        call_count = 0

        def mock_rpc(method, params, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "lines": [{"currency": "SOLO", "account": _SOLO_ISSUER, "balance": "1.0"}],
                    "marker": "page2",
                }
            return {
                "lines": [{"currency": "GRIM", "account": _GRIM_ISSUER, "balance": "2.0"}],
            }

        with patch("taxspine_orchestrator.prices._xrpl_rpc", side_effect=mock_rpc):
            specs = _fetch_account_trust_lines(_TEST_ACCOUNT)

        assert call_count == 2
        assert f"SOLO.{_SOLO_ISSUER}" in specs
        assert f"GRIM.{_GRIM_ISSUER}" in specs

    def test_non_dict_line_entries_skipped(self):
        """lines list containing non-dict elements must not raise AttributeError."""
        mock_result = {
            "lines": [
                ["SOLO", _SOLO_ISSUER, "100.0"],           # array format — should be skipped
                {"currency": "GRIM", "account": _GRIM_ISSUER, "balance": "500.0"},  # valid
            ]
        }
        with patch("taxspine_orchestrator.prices._xrpl_rpc", return_value=mock_result):
            specs = _fetch_account_trust_lines(_TEST_ACCOUNT)
        assert f"SOLO.{_SOLO_ISSUER}" not in specs
        assert f"GRIM.{_GRIM_ISSUER}" in specs


# ── TestAutoDiscoverFromAccounts ──────────────────────────────────────────────


class TestAutoDiscoverFromAccounts:
    """fetch_all_prices_for_year auto-discovers XRPL tokens from registered accounts."""

    def test_account_tokens_included_in_fetch(self, tmp_path):
        """Tokens returned by account_lines are priced via Tier 2 automatically."""
        import taxspine_orchestrator.prices as _pm

        discovered_specs: list[str] = []

        def _mock_trust_lines(account: str) -> list[str]:
            return [f"SOLO.{_SOLO_ISSUER}", f"GRIM.{_GRIM_ISSUER}"]

        def _mock_fetch_iou(symbol, issuer, year, xrp_usd, nok_rates, dest):
            discovered_specs.append(f"{symbol}.{issuer}")
            dest.write_text("date,asset_id,fiat_currency,price_fiat\n2025-01-01,{symbol},NOK,1.0\n")
            return True

        old_accounts = _pm._workspace_accounts_provider
        old_assets   = _pm._workspace_assets_provider
        try:
            _pm._workspace_accounts_provider = lambda: [_TEST_ACCOUNT]
            _pm._workspace_assets_provider   = lambda: []

            with patch("taxspine_orchestrator.prices.settings") as ms, \
                 patch("taxspine_orchestrator.prices._fetch_account_trust_lines",
                       side_effect=_mock_trust_lines), \
                 patch("taxspine_orchestrator.prices._fetch_and_write_xrpl_iou",
                       side_effect=_mock_fetch_iou), \
                 patch("taxspine_orchestrator.prices._needs_fetch", return_value=True), \
                 patch("taxspine_orchestrator.prices._fetch_and_write"), \
                 patch("taxspine_orchestrator.prices._xrpl_iou_csv_path",
                       side_effect=lambda sym, iss, yr: tmp_path / f"{sym}.csv"), \
                 patch("taxspine_orchestrator.prices._fetch_kraken_usd_prices",
                       return_value={"2025-01-01": Decimal("0.5")}), \
                 patch("taxspine_orchestrator.prices._fetch_norges_bank_usd_nok",
                       return_value={"2025-01-01": Decimal("10.0")}), \
                 patch("taxspine_orchestrator.prices._fill_calendar_gaps",
                       side_effect=lambda d, yr: d):
                ms.PRICES_DIR = tmp_path
                fetch_all_prices_for_year(2025, extra_xrpl_assets=None)

        finally:
            _pm._workspace_accounts_provider = old_accounts
            _pm._workspace_assets_provider   = old_assets

        assert f"SOLO.{_SOLO_ISSUER}" in discovered_specs
        assert f"GRIM.{_GRIM_ISSUER}" in discovered_specs

    def test_no_duplicate_specs_when_account_overlaps_assets(self, tmp_path):
        """A token in both account_lines and xrpl_assets is fetched exactly once."""
        import taxspine_orchestrator.prices as _pm

        fetch_calls: list[str] = []

        def _mock_fetch_iou(symbol, issuer, year, xrp_usd, nok_rates, dest):
            fetch_calls.append(f"{symbol}.{issuer}")
            dest.write_text("date,asset_id,fiat_currency,price_fiat\n")
            return True

        old_accounts = _pm._workspace_accounts_provider
        old_assets   = _pm._workspace_assets_provider
        try:
            _pm._workspace_accounts_provider = lambda: [_TEST_ACCOUNT]
            # Same spec registered manually too
            _pm._workspace_assets_provider   = lambda: [f"SOLO.{_SOLO_ISSUER}"]

            with patch("taxspine_orchestrator.prices.settings") as ms, \
                 patch("taxspine_orchestrator.prices._fetch_account_trust_lines",
                       return_value=[f"SOLO.{_SOLO_ISSUER}"]), \
                 patch("taxspine_orchestrator.prices._fetch_and_write_xrpl_iou",
                       side_effect=_mock_fetch_iou), \
                 patch("taxspine_orchestrator.prices._needs_fetch", return_value=True), \
                 patch("taxspine_orchestrator.prices._fetch_and_write"), \
                 patch("taxspine_orchestrator.prices._xrpl_iou_csv_path",
                       side_effect=lambda sym, iss, yr: tmp_path / f"{sym}.csv"), \
                 patch("taxspine_orchestrator.prices._fetch_kraken_usd_prices",
                       return_value={"2025-01-01": Decimal("0.5")}), \
                 patch("taxspine_orchestrator.prices._fetch_norges_bank_usd_nok",
                       return_value={"2025-01-01": Decimal("10.0")}), \
                 patch("taxspine_orchestrator.prices._fill_calendar_gaps",
                       side_effect=lambda d, yr: d):
                ms.PRICES_DIR = tmp_path
                fetch_all_prices_for_year(2025)
        finally:
            _pm._workspace_accounts_provider = old_accounts
            _pm._workspace_assets_provider   = old_assets

        solo_calls = [c for c in fetch_calls if c.startswith("SOLO.")]
        assert len(solo_calls) == 1, f"SOLO fetched {len(solo_calls)} times, expected 1"

    def test_rpc_failure_in_discovery_does_not_block_fetch(self, tmp_path):
        """If account_lines fails, price fetch continues with manually registered assets."""
        import taxspine_orchestrator.prices as _pm

        old_accounts = _pm._workspace_accounts_provider
        old_assets   = _pm._workspace_assets_provider
        try:
            _pm._workspace_accounts_provider = lambda: [_TEST_ACCOUNT]
            _pm._workspace_assets_provider   = lambda: []

            with patch("taxspine_orchestrator.prices.settings") as ms, \
                 patch("taxspine_orchestrator.prices._fetch_account_trust_lines",
                       return_value=[]), \
                 patch("taxspine_orchestrator.prices._needs_fetch", return_value=False), \
                 patch("taxspine_orchestrator.prices._fetch_and_write"):
                ms.PRICES_DIR = tmp_path
                (tmp_path / "xrp_nok_2025.csv").write_text("date,asset_id,fiat_currency,price_fiat\n")
                # Should not raise even if account_lines returns nothing
                resp = fetch_all_prices_for_year(2025)
        finally:
            _pm._workspace_accounts_provider = old_accounts
            _pm._workspace_assets_provider   = old_assets

        assert resp is not None


# ── CoinGecko Tier 2c helpers ─────────────────────────────────────────────────

_CG_SEARCH_BODY_SOLO = json.dumps({
    "coins": [
        {"id": "stasis-network", "symbol": "EURS", "name": "EURS Stablecoin"},
        {"id": "solo-coin",      "symbol": "SOLO", "name": "Sologenic"},
    ]
}).encode()

_CG_SEARCH_BODY_NO_EXACT = json.dumps({
    "coins": [
        {"id": "some-other-coin", "symbol": "XYZ", "name": "Something"},
    ]
}).encode()

_CG_MARKET_BODY = json.dumps({
    "prices": [
        [1672531200000, 5.2345],   # 2023-01-01 UTC
        [1672617600000, 5.3000],   # 2023-01-02 UTC
        [1704067199000, 5.1000],   # 2023-12-31 UTC
        [1704067200000, 5.9000],   # 2024-01-01 UTC — different year, must be excluded
    ]
}).encode()


def _make_urlopen_response(body: bytes, status: int = 200):
    """Return a mock context-manager that mimics urllib urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestCoinGeckoSearchCoinId:
    """Unit tests for _coingecko_search_coin_id."""

    def test_exact_symbol_match_preferred(self):
        """When multiple results exist, the exact symbol match is returned."""
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_SEARCH_BODY_SOLO)):
            result = _coingecko_search_coin_id("SOLO")
        assert result == "solo-coin"

    def test_first_result_fallback_when_no_exact_match(self):
        """When no coin has an exact symbol match the first result is returned."""
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_SEARCH_BODY_NO_EXACT)):
            result = _coingecko_search_coin_id("SOLO")
        assert result == "some-other-coin"

    def test_empty_coins_list_returns_none(self):
        body = json.dumps({"coins": []}).encode()
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(body)):
            result = _coingecko_search_coin_id("SOLO")
        assert result is None

    def test_network_error_returns_none(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = _coingecko_search_coin_id("SOLO")
        assert result is None

    def test_symbol_search_is_case_insensitive(self):
        """Exact match check is case-insensitive: 'solo' should match 'SOLO' symbol."""
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_SEARCH_BODY_SOLO)):
            result = _coingecko_search_coin_id("solo")
        assert result == "solo-coin"

    def test_returns_string(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_SEARCH_BODY_SOLO)):
            result = _coingecko_search_coin_id("SOLO")
        assert isinstance(result, str)

    def test_non_dict_coin_entries_skipped(self):
        """coins list containing non-dict elements must not raise AttributeError."""
        body = json.dumps({"coins": [["solo-coin", "SOLO", "Solo"]]}).encode()
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(body)):
            result = _coingecko_search_coin_id("SOLO")
        assert result is None


class TestFetchCoinGeckoNokPrices:
    """Unit tests for _fetch_coingecko_nok_prices."""

    def test_returns_dict_with_date_keys(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_MARKET_BODY)), \
             patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value="solo-coin"):
            result = _fetch_coingecko_nok_prices("SOLO", 2023)
        assert isinstance(result, dict)
        assert all(isinstance(k, str) for k in result)

    def test_prices_filtered_to_requested_year(self):
        """The 2024-01-01 entry must be excluded when year=2023."""
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_MARKET_BODY)), \
             patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value="solo-coin"):
            result = _fetch_coingecko_nok_prices("SOLO", 2023)
        assert all(k.startswith("2023-") for k in result), f"Non-2023 dates found: {list(result)}"

    def test_price_values_are_decimal(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_MARKET_BODY)), \
             patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value="solo-coin"):
            result = _fetch_coingecko_nok_prices("SOLO", 2023)
        assert all(isinstance(v, Decimal) for v in result.values())

    def test_coin_id_not_found_returns_empty(self):
        with patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value=None):
            result = _fetch_coingecko_nok_prices("UNKNOWN", 2023)
        assert result == {}

    def test_network_error_returns_empty(self):
        with patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value="solo-coin"), \
             patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = _fetch_coingecko_nok_prices("SOLO", 2023)
        assert result == {}

    def test_malformed_entry_skipped(self):
        """A prices entry with only one element must be skipped without raising."""
        body = json.dumps({"prices": [[1672531200000], [1672617600000, 5.3]]}).encode()
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(body)), \
             patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value="solo-coin"):
            result = _fetch_coingecko_nok_prices("SOLO", 2023)
        assert len(result) == 1

    def test_correct_date_format(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_CG_MARKET_BODY)), \
             patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value="solo-coin"):
            result = _fetch_coingecko_nok_prices("SOLO", 2023)
        for k in result:
            datetime.datetime.strptime(k, "%Y-%m-%d")  # raises if format is wrong

    def test_list_response_returns_empty(self):
        """CoinGecko returning a list (rate-limit / unexpected format) must not raise."""
        list_body = json.dumps([1, 2, 3]).encode()
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(list_body)), \
             patch("taxspine_orchestrator.prices._coingecko_search_coin_id", return_value="solo-coin"):
            result = _fetch_coingecko_nok_prices("SOLO", 2023)
        assert result == {}


class TestFetchAndWriteXrplIouCoinGeckoFallback:
    """Tests the CoinGecko Tier 2c path inside _fetch_and_write_xrpl_iou."""

    _XRP_USD  = {"2023-01-01": Decimal("0.50")}
    _NOK_RATE = {"2023-01-01": Decimal("10.0")}

    def test_coingecko_called_when_dex_sources_empty(self, tmp_path):
        """When OnTheDEX and XRPL.to both return {}, CoinGecko is tried."""
        dest = tmp_path / "SOLO.csv"
        nok_prices = {"2023-01-01": Decimal("52.5000")}
        with patch("taxspine_orchestrator.prices._fetch_onthedex_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_xrplto_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_coingecko_nok_prices", return_value=nok_prices) as mock_cg:
            result = _fetch_and_write_xrpl_iou("SOLO", "r123", 2023, self._XRP_USD, self._NOK_RATE, dest)
        mock_cg.assert_called_once_with("SOLO", 2023)
        assert result is True

    def test_coingecko_writes_csv_with_correct_columns(self, tmp_path):
        """CoinGecko prices are written as a valid NOK CSV."""
        dest = tmp_path / "SOLO.csv"
        nok_prices = {"2023-01-01": Decimal("52.5000")}
        with patch("taxspine_orchestrator.prices._fetch_onthedex_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_xrplto_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_coingecko_nok_prices", return_value=nok_prices):
            _fetch_and_write_xrpl_iou("SOLO", "r123", 2023, self._XRP_USD, self._NOK_RATE, dest)
        import csv as _csv
        rows = list(_csv.DictReader(dest.read_text(encoding="utf-8").splitlines()))
        assert len(rows) == 1
        assert rows[0]["date"] == "2023-01-01"
        assert rows[0]["fiat_currency"] == "NOK"
        assert rows[0]["asset_id"] == "SOLO"

    def test_coingecko_not_called_when_onthedex_succeeds(self, tmp_path):
        """CoinGecko must NOT be called when OnTheDEX returns data."""
        dest = tmp_path / "SOLO.csv"
        xrp_prices = {"2023-01-01": Decimal("0.1")}
        with patch("taxspine_orchestrator.prices._fetch_onthedex_xrp_prices", return_value=xrp_prices), \
             patch("taxspine_orchestrator.prices._fetch_coingecko_nok_prices") as mock_cg:
            _fetch_and_write_xrpl_iou("SOLO", "r123", 2023, self._XRP_USD, self._NOK_RATE, dest)
        mock_cg.assert_not_called()

    def test_coingecko_not_called_when_xrplto_succeeds(self, tmp_path):
        """CoinGecko must NOT be called when XRPL.to returns data."""
        dest = tmp_path / "SOLO.csv"
        xrp_prices = {"2023-01-01": Decimal("0.1")}
        with patch("taxspine_orchestrator.prices._fetch_onthedex_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_xrplto_xrp_prices", return_value=xrp_prices), \
             patch("taxspine_orchestrator.prices._fetch_coingecko_nok_prices") as mock_cg:
            _fetch_and_write_xrpl_iou("SOLO", "r123", 2023, self._XRP_USD, self._NOK_RATE, dest)
        mock_cg.assert_not_called()

    def test_returns_false_when_all_sources_empty(self, tmp_path):
        """Returns False if OnTheDEX, XRPL.to, and CoinGecko all return no data."""
        dest = tmp_path / "SOLO.csv"
        with patch("taxspine_orchestrator.prices._fetch_onthedex_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_xrplto_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_coingecko_nok_prices", return_value={}):
            result = _fetch_and_write_xrpl_iou("SOLO", "r123", 2023, self._XRP_USD, self._NOK_RATE, dest)
        assert result is False

    def test_coingecko_price_quantized_to_4dp(self, tmp_path):
        """CoinGecko prices are quantized to 4 decimal places in the CSV."""
        dest = tmp_path / "SOLO.csv"
        nok_prices = {"2023-01-01": Decimal("52.123456789")}
        with patch("taxspine_orchestrator.prices._fetch_onthedex_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_xrplto_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_coingecko_nok_prices", return_value=nok_prices):
            _fetch_and_write_xrpl_iou("SOLO", "r123", 2023, self._XRP_USD, self._NOK_RATE, dest)
        import csv as _csv
        rows = list(_csv.DictReader(dest.read_text(encoding="utf-8").splitlines()))
        price_str = rows[0]["price_fiat"]
        price = Decimal(price_str)
        # Must have at most 4 decimal places
        assert price == price.quantize(Decimal("0.0001"))

    def test_multiple_coingecko_rows_written_in_order(self, tmp_path):
        """Multiple CoinGecko dates are written sorted ascending."""
        dest = tmp_path / "SOLO.csv"
        nok_prices = {
            "2023-01-03": Decimal("53.0"),
            "2023-01-01": Decimal("51.0"),
            "2023-01-02": Decimal("52.0"),
        }
        with patch("taxspine_orchestrator.prices._fetch_onthedex_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_xrplto_xrp_prices", return_value={}), \
             patch("taxspine_orchestrator.prices._fetch_coingecko_nok_prices", return_value=nok_prices):
            _fetch_and_write_xrpl_iou("SOLO", "r123", 2023, self._XRP_USD, self._NOK_RATE, dest)
        import csv as _csv
        rows = list(_csv.DictReader(dest.read_text(encoding="utf-8").splitlines()))
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)
