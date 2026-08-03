"""Batch 26 — TL-19 GBP price-fetch endpoint, API-11 output-dir cleanup,
INFRA-22 Dockerfile.local base-image pin.

Coverage:
    TL-19   POST /prices/fetch-gbp endpoint added — fetches Kraken USD prices
            × Bank of England (XUDLUSS) USD/GBP rates → writes GBP price table
            CSVs for UK jobs.  Mirrors the NOK price-fetch flow.
    API-11  DELETE /jobs/{id} now also removes the OUTPUT_DIR/{job_id}/
            directory after deleting individual files, preventing orphaned
            directory accumulation over time.
    INFRA-22 Dockerfile.local changed from floating ``python:3.11-slim`` to
             the pinned ``python:3.11.9-slim`` tag used in the production
             Dockerfile, preventing silent local/production divergence.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.prices import (
    _asset_csv_path_gbp,
    _combined_csv_path_gbp,
    _fetch_bank_of_england_usd_gbp,
    _fetch_and_write_gbp,
    fetch_all_gbp_prices_for_year,
)

client = TestClient(app)

_DOCKERFILE_LOCAL = Path(__file__).parent.parent / "Dockerfile.local"
_PRICES_SRC = Path(__file__).parent.parent / "taxspine_orchestrator" / "prices.py"


def _dockerfile_local() -> str:
    return _DOCKERFILE_LOCAL.read_text(encoding="utf-8")


def _prices_src() -> str:
    return _PRICES_SRC.read_text(encoding="utf-8")


# ===========================================================================
# Helpers — mock HTTP responses
# ===========================================================================


def _make_kraken_response(pair: str, year: int, close: str = "50000.0") -> bytes:
    """Minimal Kraken OHLC JSON response for one candle in June of *year*."""
    import datetime as _dt
    ts = int(_dt.datetime(year, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
    body = {
        "error": [],
        "result": {
            pair: [[ts, close, close, close, close, close, "1.0", 1]],
            "last": ts,
        },
    }
    return json.dumps(body).encode()


def _make_boe_response(year: int, rate: str = "1.2700") -> bytes:
    """Minimal Bank of England IADB CSV response for one date in June of *year*."""
    date_str = f"01 Jun {year}"
    return f"Date,XUDLUSS\n{date_str},{rate}\n".encode()


def _urlopen_ctx(raw: bytes):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ===========================================================================
# TestTL19BankOfEnglandFetch
# ===========================================================================


class TestTL19BankOfEnglandFetch:
    """TL-19: _fetch_bank_of_england_usd_gbp() must return Decimal rates."""

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_returns_decimal_values(self, mock_urlopen):
        from decimal import Decimal
        mock_urlopen.return_value = _urlopen_ctx(_make_boe_response(2025, "1.27"))
        result = _fetch_bank_of_england_usd_gbp(2025)
        assert result
        for rate in result.values():
            assert isinstance(rate, Decimal), f"Expected Decimal, got {type(rate).__name__}"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_date_keys_are_iso_format(self, mock_urlopen):
        """Date keys returned are 'YYYY-MM-DD' strings."""
        mock_urlopen.return_value = _urlopen_ctx(_make_boe_response(2025, "1.27"))
        result = _fetch_bank_of_england_usd_gbp(2025)
        for key in result:
            # Should match YYYY-MM-DD
            import re
            assert re.match(r"\d{4}-\d{2}-\d{2}", key), f"Bad date key: {key!r}"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_raises_on_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("Connection refused")
        with pytest.raises(RuntimeError, match="Bank of England"):
            _fetch_bank_of_england_usd_gbp(2025)

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_raises_when_no_rates_parsed(self, mock_urlopen):
        """Empty or header-only response raises RuntimeError."""
        mock_urlopen.return_value = _urlopen_ctx(b"Date,XUDLUSS\n")
        with pytest.raises(RuntimeError):
            _fetch_bank_of_england_usd_gbp(2025)

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_skips_na_rows(self, mock_urlopen):
        """Rows with 'n/a' values are silently skipped."""
        csv_text = "Date,XUDLUSS\n02 Jan 2025,n/a\n03 Jan 2025,1.2500\n"
        mock_urlopen.return_value = _urlopen_ctx(csv_text.encode())
        result = _fetch_bank_of_england_usd_gbp(2025)
        # Only the row with a real value should be included.
        assert "2025-01-02" not in result
        assert "2025-01-03" in result

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_exact_decimal_value(self, mock_urlopen):
        """0.1 is exactly representable as Decimal — no float rounding."""
        csv_text = "Date,XUDLUSS\n01 Jun 2025,0.1\n"
        mock_urlopen.return_value = _urlopen_ctx(csv_text.encode())
        from decimal import Decimal
        result = _fetch_bank_of_england_usd_gbp(2025)
        assert result["2025-06-01"] == Decimal("0.1")


# ===========================================================================
# TestTL19GbpPathHelpers
# ===========================================================================


class TestTL19GbpPathHelpers:
    """TL-19: GBP path helpers produce the correct filenames."""

    def test_asset_csv_path_gbp_naming(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as m:
            m.PRICES_DIR = tmp_path
            p = _asset_csv_path_gbp("XRP", 2025)
        assert p.name == "xrp_gbp_2025.csv"

    def test_combined_csv_path_gbp_naming(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as m:
            m.PRICES_DIR = tmp_path
            p = _combined_csv_path_gbp(2025)
        assert p.name == "combined_gbp_2025.csv"

    def test_asset_and_combined_are_different(self, tmp_path):
        with patch("taxspine_orchestrator.prices.settings") as m:
            m.PRICES_DIR = tmp_path
            a = _asset_csv_path_gbp("BTC", 2025)
            c = _combined_csv_path_gbp(2025)
        assert a != c


# ===========================================================================
# TestTL19FetchAndWriteGbp
# ===========================================================================


class TestTL19FetchAndWriteGbp:
    """TL-19: _fetch_and_write_gbp() computes GBP prices using division."""

    def _side_effect(self, kraken_body: bytes, boe_body: bytes):
        call_count = [0]

        def side_effect(req, timeout=None):
            call_count[0] += 1
            raw = kraken_body if call_count[0] == 1 else boe_body
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        return side_effect

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_output_csv_fiat_currency_is_gbp(self, mock_urlopen, tmp_path):
        """Written rows use fiat_currency='GBP'."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
        kraken = json.dumps({
            "error": [],
            "result": {
                "XRPUSD": [[ts, "1.0", "1.0", "1.0", "1.0", "1.0", "1.0", 1]],
                "last": ts,
            },
        }).encode()
        boe = b"Date,XUDLUSS\n01 Jun 2025,1.25\n"
        mock_urlopen.side_effect = self._side_effect(kraken, boe)

        dest = tmp_path / "xrp_gbp_2025.csv"
        _fetch_and_write_gbp("XRPUSD", "XRP", 2025, dest)

        lines = dest.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 2
        assert lines[1].split(",")[2] == "GBP"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_gbp_price_uses_division_not_multiplication(self, mock_urlopen, tmp_path):
        """GBP price = USD price / (USD per GBP). 1 USD / 2.0 USD-per-GBP = 0.5 GBP."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
        kraken = json.dumps({
            "error": [],
            "result": {
                "XRPUSD": [[ts, "1.0", "1.0", "1.0", "1.0", "1.0", "1.0", 1]],
                "last": ts,
            },
        }).encode()
        boe = b"Date,XUDLUSS\n01 Jun 2025,2.0\n"  # 1 GBP = 2 USD → 1 USD = 0.5 GBP
        mock_urlopen.side_effect = self._side_effect(kraken, boe)

        dest = tmp_path / "xrp_gbp_2025.csv"
        _fetch_and_write_gbp("XRPUSD", "XRP", 2025, dest)

        lines = dest.read_text(encoding="utf-8").splitlines()
        price_str = lines[1].split(",")[-1]
        assert price_str == "0.5000", f"Expected 0.5000 GBP, got {price_str!r}"

    @patch("taxspine_orchestrator.prices.urllib.request.urlopen")
    def test_four_decimal_places(self, mock_urlopen, tmp_path):
        """Written GBP price has exactly 4 decimal places."""
        import datetime as _dt
        ts = int(_dt.datetime(2025, 6, 1, tzinfo=_dt.timezone.utc).timestamp())
        kraken = json.dumps({
            "error": [],
            "result": {
                "XRPUSD": [[ts, "1.5", "1.5", "1.5", "1.5", "1.5", "1.5", 1]],
                "last": ts,
            },
        }).encode()
        boe = b"Date,XUDLUSS\n01 Jun 2025,1.2700\n"
        mock_urlopen.side_effect = self._side_effect(kraken, boe)

        dest = tmp_path / "xrp_gbp_2025.csv"
        _fetch_and_write_gbp("XRPUSD", "XRP", 2025, dest)

        lines = dest.read_text(encoding="utf-8").splitlines()
        price_str = lines[1].split(",")[-1]
        assert "." in price_str
        decimal_part = price_str.split(".")[1]
        assert len(decimal_part) == 4, f"Expected 4 decimal places, got: {price_str!r}"


# ===========================================================================
# TestTL19FetchPricesGbpEndpoint
# ===========================================================================


class TestTL19FetchPricesGbpEndpoint:
    """TL-19: POST /prices/fetch-gbp endpoint validation and behaviour."""

    def test_endpoint_exists(self):
        """POST /prices/fetch-gbp returns something (not 404/405)."""
        with patch("taxspine_orchestrator.prices.fetch_all_gbp_prices_for_year",
                   side_effect=RuntimeError("mocked")):
            resp = client.post("/prices/fetch-gbp", json={"year": 2023})
        assert resp.status_code != 404
        assert resp.status_code != 405

    def test_year_below_minimum_returns_400(self):
        resp = client.post("/prices/fetch-gbp", json={"year": 2012})
        assert resp.status_code == 400
        assert "2013" in resp.json()["detail"]

    def test_future_year_returns_400(self):
        import datetime
        future = datetime.date.today().year + 1
        resp = client.post("/prices/fetch-gbp", json={"year": future})
        assert resp.status_code == 400

    def test_missing_year_returns_422(self):
        resp = client.post("/prices/fetch-gbp", json={})
        assert resp.status_code == 422

    def test_valid_year_delegates_to_fetch_function(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.asset = "COMBINED"
        mock_resp.year = 2023
        mock_resp.path = str(tmp_path / "combined_gbp_2023.csv")
        mock_resp.rows = 365
        mock_resp.age_hours = 0.0
        mock_resp.cached = False
        mock_resp.unsupported_assets = []

        with patch("taxspine_orchestrator.prices.fetch_all_gbp_prices_for_year",
                   return_value=mock_resp):
            resp = client.post("/prices/fetch-gbp", json={"year": 2023})
        assert resp.status_code == 200

    def test_network_failure_returns_502(self):
        with patch("taxspine_orchestrator.prices.fetch_all_gbp_prices_for_year",
                   side_effect=RuntimeError("BoE unreachable")):
            resp = client.post("/prices/fetch-gbp", json={"year": 2023})
        assert resp.status_code == 502
        assert "BoE unreachable" in resp.json()["detail"]

    def test_response_has_rlusd_unsupported_note(self, tmp_path):
        """RLUSD is always reported as unsupported in the GBP response."""
        mock_resp = MagicMock()
        mock_resp.asset = "COMBINED"
        mock_resp.year = 2023
        mock_resp.path = str(tmp_path / "combined_gbp_2023.csv")
        mock_resp.rows = 200
        mock_resp.age_hours = 0.0
        mock_resp.cached = False
        from taxspine_orchestrator.prices import UnsupportedAssetNote
        mock_resp.unsupported_assets = [
            UnsupportedAssetNote(asset="RLUSD", reason="Not on Kraken")
        ]

        with patch("taxspine_orchestrator.prices.fetch_all_gbp_prices_for_year",
                   return_value=mock_resp):
            resp = client.post("/prices/fetch-gbp", json={"year": 2023})
        assert resp.status_code == 200
        body = resp.json()
        assert any(u["asset"] == "RLUSD" for u in body["unsupported_assets"])


# ===========================================================================
# TestTL19ListPricesIncludesGbp
# ===========================================================================


class TestTL19ListPricesIncludesGbp:
    """TL-19: GET /prices must also list GBP price files."""

    def test_gbp_combined_file_appears_in_list(self, tmp_path):
        combined = tmp_path / "combined_gbp_2025.csv"
        combined.write_text("date,asset_id,fiat_currency,price_fiat\n2025-01-01,XRP,GBP,0.75\n")
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["asset"] == "COMBINED"
        assert items[0]["year"] == 2025
        assert items[0]["rows"] == 1

    def test_gbp_and_nok_files_both_listed(self, tmp_path):
        (tmp_path / "combined_nok_2025.csv").write_text(
            "date,asset_id,fiat_currency,price_fiat\n2025-01-01,XRP,NOK,7.5\n"
        )
        (tmp_path / "combined_gbp_2025.csv").write_text(
            "date,asset_id,fiat_currency,price_fiat\n2025-01-01,XRP,GBP,0.75\n"
        )
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_gbp_per_asset_file_listed(self, tmp_path):
        (tmp_path / "btc_gbp_2024.csv").write_text(
            "date,asset_id,fiat_currency,price_fiat\n2024-06-01,BTC,GBP,30000.0\n"
        )
        with patch("taxspine_orchestrator.prices.settings") as mock_settings:
            mock_settings.PRICES_DIR = tmp_path
            resp = client.get("/prices")
        assert resp.status_code == 200
        items = resp.json()
        assert items[0]["asset"] == "BTC"
        assert items[0]["year"] == 2024


# ===========================================================================
# TestTL19SourceCodeStructure
# ===========================================================================


class TestTL19SourceCodeStructure:
    """TL-19: structural checks on prices.py source."""

    def test_tl19_comment_in_prices(self):
        """The TL-19 comment is present in prices.py."""
        assert "TL-19" in _prices_src()

    def test_bank_of_england_url_in_source(self):
        """The Bank of England IADB URL is present in prices.py."""
        assert "bankofengland.co.uk" in _prices_src()

    def test_xudluss_series_code_referenced(self):
        """The XUDLUSS series code is referenced in prices.py."""
        assert "XUDLUSS" in _prices_src()

    def test_fetch_gbp_endpoint_registered(self):
        """The /fetch-gbp route is registered in the prices router."""
        assert "/fetch-gbp" in _prices_src()

    def test_combined_gbp_filename_pattern(self):
        """The combined_gbp_{year}.csv filename pattern is in source."""
        assert "combined_gbp_" in _prices_src()

    def test_gbp_division_formula_in_source(self):
        """GBP price is computed via division (usd / usd_per_gbp), not multiplication."""
        assert "usd_per_gbp" in _prices_src()

    def test_fetch_all_gbp_function_importable(self):
        """fetch_all_gbp_prices_for_year is importable from prices module."""
        assert callable(fetch_all_gbp_prices_for_year)


# ===========================================================================
# TestAPI11OutputDirCleanup
# ===========================================================================


class TestAPI11OutputDirCleanup:
    """API-11: DELETE /jobs/{id} must also remove the job output directory."""

    def test_api11_comment_in_main(self):
        """The API-11 comment is present in main.py."""
        import taxspine_orchestrator.main as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        assert "API-11" in src

    def test_api11_shutil_rmtree_in_delete_handler(self):
        """shutil.rmtree is called in the delete_job endpoint."""
        import taxspine_orchestrator.main as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        assert "shutil.rmtree" in src

    def test_api11_output_dir_variable_in_delete_handler(self):
        """The output directory variable is constructed in the delete handler."""
        import taxspine_orchestrator.main as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        assert "_job_output_dir" in src

    def test_delete_job_removes_output_dir(self, tmp_path):
        """When delete_files=True, the job output directory is removed."""
        from taxspine_orchestrator.models import (
            Country, CsvFileSpec, CsvSourceType, Job, JobInput, JobOutput, JobStatus,
        )
        from datetime import datetime, timezone

        job_id = "test-api11-cleanup-job"
        out_dir = tmp_path / job_id
        out_dir.mkdir()
        (out_dir / "report.html").write_text("<html></html>", encoding="utf-8")

        now = datetime.now(timezone.utc)
        job = Job(
            id=job_id,
            status=JobStatus.COMPLETED,
            input=JobInput(
                csv_files=[CsvFileSpec(path="dummy.csv", source_type=CsvSourceType.GENERIC_EVENTS)],
                tax_year=2025,
                country=Country.NORWAY,
            ),
            output=JobOutput(report_html_path=str(out_dir / "report.html")),
            created_at=now,
            updated_at=now,
        )

        with patch("taxspine_orchestrator.main._job_service") as mock_svc, \
             patch("taxspine_orchestrator.main._job_store") as mock_store, \
             patch("taxspine_orchestrator.main.settings") as mock_settings:
            mock_store.get.return_value = job
            mock_svc.get_job.return_value = job
            mock_settings.OUTPUT_DIR = tmp_path
            mock_settings.ORCHESTRATOR_KEY = ""

            resp = client.delete(f"/jobs/{job_id}?delete_files=true")

        assert resp.status_code == 200
        assert not out_dir.exists(), (
            f"Expected output directory {out_dir} to be removed, but it still exists"
        )

    def test_delete_job_dir_cleanup_skipped_when_delete_files_false(self, tmp_path):
        """When delete_files=False, the output directory is NOT removed."""
        from taxspine_orchestrator.models import (
            Country, CsvFileSpec, CsvSourceType, Job, JobInput, JobOutput, JobStatus,
        )
        from datetime import datetime, timezone

        job_id = "test-api11-keep-dir-job"
        out_dir = tmp_path / job_id
        out_dir.mkdir()
        (out_dir / "report.html").write_text("<html></html>", encoding="utf-8")

        now = datetime.now(timezone.utc)
        job = Job(
            id=job_id,
            status=JobStatus.COMPLETED,
            input=JobInput(
                csv_files=[CsvFileSpec(path="dummy.csv", source_type=CsvSourceType.GENERIC_EVENTS)],
                tax_year=2025,
                country=Country.NORWAY,
            ),
            output=JobOutput(report_html_path=str(out_dir / "report.html")),
            created_at=now,
            updated_at=now,
        )

        with patch("taxspine_orchestrator.main._job_service") as mock_svc, \
             patch("taxspine_orchestrator.main._job_store") as mock_store, \
             patch("taxspine_orchestrator.main.settings") as mock_settings:
            mock_store.get.return_value = job
            mock_svc.get_job.return_value = job
            mock_settings.OUTPUT_DIR = tmp_path
            mock_settings.ORCHESTRATOR_KEY = ""

            resp = client.delete(f"/jobs/{job_id}?delete_files=false")

        assert resp.status_code == 200
        assert out_dir.exists(), (
            "Output directory should NOT be removed when delete_files=false"
        )


# ===========================================================================
# TestINFRA22DockerfileLocalPin
# ===========================================================================


class TestINFRA22DockerfileLocalPin:
    """INFRA-22: Dockerfile.local must use a pinned Python base image."""

    def test_infra22_pinned_image_not_floating(self):
        """Dockerfile.local does not use the floating python:3.11-slim tag."""
        src = _dockerfile_local()
        lines = src.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "FROM python:" in line:
                assert "python:3.11-slim" not in line or "python:3.11." in line, (
                    f"Dockerfile.local uses floating python:3.11-slim — should be pinned. Line: {line!r}"
                )

    def test_infra22_uses_patch_pinned_tag(self):
        """Dockerfile.local FROM line uses a patch-version-pinned tag (e.g. 3.11.9-slim)."""
        src = _dockerfile_local()
        import re
        match = re.search(r"FROM\s+python:([\d.]+)-slim", src)
        assert match, "No 'FROM python:X.Y.Z-slim' found in Dockerfile.local"
        tag = match.group(1)
        parts = tag.split(".")
        assert len(parts) >= 3, (
            f"Expected a patch-pinned tag like '3.11.9', got '{tag}'"
        )

    def test_infra22_comment_present(self):
        """The INFRA-22 comment is present in Dockerfile.local."""
        assert "INFRA-22" in _dockerfile_local()

    def test_infra22_matches_known_pin(self):
        """Dockerfile.local uses python:3.11.9-slim (matching production)."""
        src = _dockerfile_local()
        assert "python:3.11.9-slim" in src, (
            "Dockerfile.local should use python:3.11.9-slim to match production"
        )
