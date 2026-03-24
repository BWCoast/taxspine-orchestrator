"""test_holdings_prices.py — GET /lots/{year}/portfolio?include_prices=true

Tests the market-value enrichment added to the portfolio endpoint.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

try:
    import tax_spine  # noqa: F401
    _TAX_SPINE_AVAILABLE = True
except ImportError:
    _TAX_SPINE_AVAILABLE = False


def _make_client():
    from taxspine_orchestrator.main import app
    return TestClient(app)


def _write_price_csv(directory: Path, year: int, rows: list[tuple[str, str, str]]) -> Path:
    """Write a combined_nok_{year}.csv with given (date, asset_id, price) rows."""
    p = directory / f"combined_nok_{year}.csv"
    with p.open("w", encoding="utf-8") as f:
        f.write("date,asset_id,fiat_currency,price_fiat\n")
        for date, asset, price in rows:
            f.write(f"{date},{asset},NOK,{price}\n")
    return p


def _mock_lot_store(lots: list[dict]):
    """Return a mock LotPersistenceStore that yields the given lots."""
    mock_lot_cls = MagicMock()
    mock_store = MagicMock()
    mock_store.__enter__ = lambda s: s
    mock_store.__exit__ = MagicMock(return_value=False)
    mock_store.list_years.return_value = [2025]

    fake_lots = []
    for ld in lots:
        lot = MagicMock()
        lot.asset = ld["asset"]
        lot.remaining_quantity = Decimal(ld["qty"])
        lot.remaining_cost_basis_nok = Decimal(ld["basis"]) if ld.get("basis") is not None else None
        lot.basis_status = "resolved" if ld.get("basis") is not None else "missing"
        fake_lots.append(lot)

    mock_store.load_carry_forward.return_value = fake_lots
    mock_lot_cls.return_value = mock_store
    return mock_lot_cls


# ── TestPortfolioWithoutPrices ────────────────────────────────────────────────


@pytest.mark.skipif(not _TAX_SPINE_AVAILABLE, reason="tax_spine not installed")
class TestPortfolioWithoutPrices:
    """Default behaviour (include_prices=false) is unchanged."""

    def test_default_no_price_fields(self):
        client = _make_client()
        mock_cls = _mock_lot_store([
            {"asset": "XRP", "qty": "100", "basis": "500"},
        ])
        with tempfile.TemporaryDirectory() as td:
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio")
        assert r.status_code == 200
        asset = r.json()["assets"][0]
        assert "market_value_nok" not in asset
        assert "year_end_price_nok" not in asset
        assert "unrealized_gain_nok" not in asset

    def test_explicit_false_no_price_fields(self):
        client = _make_client()
        mock_cls = _mock_lot_store([{"asset": "BTC", "qty": "1", "basis": "100000"}])
        with tempfile.TemporaryDirectory() as td:
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=false")
        assert r.status_code == 200
        assert "total_market_value_nok" not in r.json()


# ── TestPortfolioWithPrices ────────────────────────────────────────────────────


@pytest.mark.skipif(not _TAX_SPINE_AVAILABLE, reason="tax_spine not installed")
class TestPortfolioWithPrices:
    """include_prices=true enriches assets with market value and P&L."""

    def test_market_value_computed_correctly(self):
        """market_value = qty × year_end_price; unrealized = market - cost_basis."""
        client = _make_client()
        mock_cls = _mock_lot_store([
            {"asset": "XRP", "qty": "100", "basis": "500.00"},
        ])
        with tempfile.TemporaryDirectory() as td:
            _write_price_csv(Path(td), 2025, [("2025-12-31", "XRP", "10.00")])
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        assert r.status_code == 200
        asset = r.json()["assets"][0]
        assert asset["year_end_price_nok"] == "10.00"
        assert asset["market_value_nok"]   == "1000.00"
        assert asset["unrealized_gain_nok"] == "500.00"
        assert asset["has_missing_price"] is False

    def test_unrealized_loss_negative(self):
        """Unrealized gain is negative when market value < cost basis."""
        client = _make_client()
        mock_cls = _mock_lot_store([
            {"asset": "ETH", "qty": "2", "basis": "40000.00"},
        ])
        with tempfile.TemporaryDirectory() as td:
            _write_price_csv(Path(td), 2025, [("2025-12-31", "ETH", "15000.00")])
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        asset = r.json()["assets"][0]
        assert Decimal(asset["unrealized_gain_nok"]) < 0

    def test_total_market_value_aggregated(self):
        client = _make_client()
        mock_cls = _mock_lot_store([
            {"asset": "BTC", "qty": "1",   "basis": "300000"},
            {"asset": "XRP", "qty": "100", "basis": "500"},
        ])
        with tempfile.TemporaryDirectory() as td:
            _write_price_csv(Path(td), 2025, [
                ("2025-12-31", "BTC", "500000.00"),
                ("2025-12-31", "XRP", "10.00"),
            ])
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        data = r.json()
        # 500000 + 1000 = 501000
        assert Decimal(data["total_market_value_nok"]) == Decimal("501000.00")
        # (500000+1000) - (300000+500) = 200500
        assert Decimal(data["total_unrealized_gain_nok"]) == Decimal("200500.00")
        assert data["prices_partial"] is False

    def test_prices_partial_when_some_assets_missing(self):
        """prices_partial=true when at least one asset has no price."""
        client = _make_client()
        mock_cls = _mock_lot_store([
            {"asset": "BTC",  "qty": "1",   "basis": "300000"},
            {"asset": "GRIM", "qty": "1000","basis": "100"},
        ])
        with tempfile.TemporaryDirectory() as td:
            # Only BTC in price CSV, not GRIM
            _write_price_csv(Path(td), 2025, [("2025-12-31", "BTC", "500000.00")])
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        data = r.json()
        assets = {a["asset"]: a for a in data["assets"]}
        assert assets["BTC"]["has_missing_price"] is False
        assert assets["GRIM"]["has_missing_price"] is True
        assert assets["GRIM"]["market_value_nok"] is None
        assert data["prices_partial"] is True


# ── TestPriceFileNotFound ──────────────────────────────────────────────────────


@pytest.mark.skipif(not _TAX_SPINE_AVAILABLE, reason="tax_spine not installed")
class TestPriceFileNotFound:
    """When no price CSV exists, all assets get has_missing_price=true."""

    def test_no_price_csv_all_missing(self):
        client = _make_client()
        mock_cls = _mock_lot_store([{"asset": "XRP", "qty": "50", "basis": "200"}])
        with tempfile.TemporaryDirectory() as td:
            # No combined_nok_2025.csv written
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        assert r.status_code == 200
        asset = r.json()["assets"][0]
        assert asset["has_missing_price"] is True
        assert asset["market_value_nok"] is None

    def test_no_price_csv_total_market_value_null(self):
        client = _make_client()
        mock_cls = _mock_lot_store([{"asset": "BTC", "qty": "1", "basis": "300000"}])
        with tempfile.TemporaryDirectory() as td:
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        data = r.json()
        assert data["total_market_value_nok"] is None
        assert data["prices_partial"] is True


# ── TestPriceFallback ─────────────────────────────────────────────────────────


@pytest.mark.skipif(not _TAX_SPINE_AVAILABLE, reason="tax_spine not installed")
class TestPriceFallback:
    """Falls back to latest available date when Dec 31 is absent."""

    def test_uses_latest_date_when_dec31_absent(self):
        """For current year, Dec 31 may not exist — use most recent date."""
        client = _make_client()
        mock_cls = _mock_lot_store([{"asset": "XRP", "qty": "10", "basis": "50"}])
        with tempfile.TemporaryDirectory() as td:
            # Only Dec 20 price available (simulates partial current-year data)
            _write_price_csv(Path(td), 2025, [
                ("2025-12-20", "XRP", "8.00"),
                ("2025-12-15", "XRP", "7.50"),
            ])
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        asset = r.json()["assets"][0]
        # Latest date (Dec 20) should be used
        assert asset["year_end_price_nok"] == "8.00"
        assert asset["has_missing_price"] is False

    def test_dec31_preferred_over_later_dates(self):
        """Dec 31 is always used when present, even if later dates exist."""
        client = _make_client()
        mock_cls = _mock_lot_store([{"asset": "BTC", "qty": "1", "basis": "300000"}])
        with tempfile.TemporaryDirectory() as td:
            _write_price_csv(Path(td), 2025, [
                ("2025-12-31", "BTC", "500000.00"),
                ("2025-12-30", "BTC", "490000.00"),
            ])
            with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_cls):
                with patch("taxspine_orchestrator.lots.settings") as ms:
                    ms.LOT_STORE_DB = Path(td) / "lots.db"
                    Path(td, "lots.db").touch()
                    ms.PRICES_DIR = Path(td)
                    r = client.get("/lots/2025/portfolio?include_prices=true")
        assert r.json()["assets"][0]["year_end_price_nok"] == "500000.00"


# ── TestLoadYearEndPricesHelper ───────────────────────────────────────────────


class TestLoadYearEndPricesHelper:
    """Unit tests for the _load_year_end_prices() helper."""

    def test_returns_empty_dict_when_no_file(self):
        from taxspine_orchestrator.lots import _load_year_end_prices
        with tempfile.TemporaryDirectory() as td:
            with patch("taxspine_orchestrator.lots.settings") as ms:
                ms.PRICES_DIR = Path(td)
                result = _load_year_end_prices(2025)
        assert result == {}

    def test_parses_dec31_correctly(self):
        from taxspine_orchestrator.lots import _load_year_end_prices
        with tempfile.TemporaryDirectory() as td:
            _write_price_csv(Path(td), 2025, [
                ("2025-12-31", "XRP", "10.50"),
                ("2025-12-31", "BTC", "500000.00"),
            ])
            with patch("taxspine_orchestrator.lots.settings") as ms:
                ms.PRICES_DIR = Path(td)
                result = _load_year_end_prices(2025)
        assert result["XRP"] == Decimal("10.50")
        assert result["BTC"] == Decimal("500000.00")

    def test_asset_id_uppercased(self):
        """asset_id column is normalised to uppercase."""
        from taxspine_orchestrator.lots import _load_year_end_prices
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "combined_nok_2025.csv"
            p.write_text("date,asset_id,fiat_currency,price_fiat\n2025-12-31,xrp,NOK,7.00\n")
            with patch("taxspine_orchestrator.lots.settings") as ms:
                ms.PRICES_DIR = Path(td)
                result = _load_year_end_prices(2025)
        assert "XRP" in result

    def test_corrupt_price_row_skipped(self):
        from taxspine_orchestrator.lots import _load_year_end_prices
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "combined_nok_2025.csv"
            p.write_text(
                "date,asset_id,fiat_currency,price_fiat\n"
                "2025-12-31,XRP,NOK,7.00\n"
                "2025-12-31,BTC,NOK,not_a_number\n"
            )
            with patch("taxspine_orchestrator.lots.settings") as ms:
                ms.PRICES_DIR = Path(td)
                result = _load_year_end_prices(2025)
        assert "XRP" in result
        assert "BTC" not in result

    def test_returns_empty_on_unreadable_file(self):
        from taxspine_orchestrator.lots import _load_year_end_prices
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "combined_nok_2025.csv"
            p.write_text("not,valid,csv\ngarbage{{{{")
            with patch("taxspine_orchestrator.lots.settings") as ms:
                ms.PRICES_DIR = Path(td)
                result = _load_year_end_prices(2025)
        # Should not raise; returns what it could parse (or empty)
        assert isinstance(result, dict)


# ── TestHoldingsPricesUI ──────────────────────────────────────────────────────


class TestHoldingsPricesUI:
    """Verify the UI HTML has the new market-value columns."""

    def _html(self):
        return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")

    def test_market_value_column_header_present(self):
        assert "Market Value" in self._html()

    def test_pnl_column_header_present(self):
        assert "P&amp;L" in self._html() or "P&L" in self._html()

    def test_market_stat_element_present(self):
        assert 'id="tc-h-market"' in self._html()

    def test_pnl_stat_element_present(self):
        assert 'id="tc-h-pnl"' in self._html()

    def test_load_holdings_uses_include_prices(self):
        assert "include_prices=true" in self._html()
