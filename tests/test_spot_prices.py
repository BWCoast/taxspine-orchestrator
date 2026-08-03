"""test_spot_prices.py — Tests for live spot price support.

Covers:
- _fetch_kraken_spot_usd()       — Kraken Ticker parsing + result-key mapping
- _fetch_norges_bank_usd_nok_current() — Norges Bank latest rate
- fetch_spot_prices_nok()        — combined NOK conversion + 5-min cache
- GET /prices/spot               — HTTP endpoint
- GET /lots/{year}/portfolio?price_type=current — live prices in portfolio
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── _fetch_kraken_spot_usd ───────────────────────────────────────────────────


class TestFetchKrakenSpotUsd:
    """Kraken Ticker response is correctly parsed into {asset: usd_price}."""

    def _make_ticker_body(self, overrides: dict | None = None) -> bytes:
        result = {
            "XXRPZUSD": {"c": ["0.52000", "100"]},
            "XXBTZUSD": {"c": ["85000.00", "1"]},
            "XETHZUSD": {"c": ["3200.00", "5"]},
            "ADAUSD":   {"c": ["0.45000", "200"]},
            "XLTCZUSD": {"c": ["75.00", "10"]},
        }
        if overrides:
            result.update(overrides)
        return json.dumps({"error": [], "result": result}).encode()

    def _mock_urlopen(self, body: bytes):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_all_tier1_assets_returned(self) -> None:
        from taxspine_orchestrator.prices import _fetch_kraken_spot_usd
        body = self._make_ticker_body()
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            prices = _fetch_kraken_spot_usd()
        assert set(prices.keys()) == {"XRP", "BTC", "ETH", "ADA", "LTC"}

    def test_prices_are_decimal(self) -> None:
        from taxspine_orchestrator.prices import _fetch_kraken_spot_usd
        body = self._make_ticker_body()
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            prices = _fetch_kraken_spot_usd()
        assert all(isinstance(v, Decimal) for v in prices.values())

    def test_xrp_price_correct(self) -> None:
        from taxspine_orchestrator.prices import _fetch_kraken_spot_usd
        body = self._make_ticker_body()
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            prices = _fetch_kraken_spot_usd()
        assert prices["XRP"] == Decimal("0.52000")

    def test_btc_price_correct(self) -> None:
        from taxspine_orchestrator.prices import _fetch_kraken_spot_usd
        body = self._make_ticker_body()
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            prices = _fetch_kraken_spot_usd()
        assert prices["BTC"] == Decimal("85000.00")

    def test_kraken_error_raises_runtime_error(self) -> None:
        from taxspine_orchestrator.prices import _fetch_kraken_spot_usd
        body = json.dumps({"error": ["EGeneral:Internal error"], "result": {}}).encode()
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            with pytest.raises(RuntimeError, match="Kraken Ticker error"):
                _fetch_kraken_spot_usd()

    def test_network_failure_raises_runtime_error(self) -> None:
        from taxspine_orchestrator.prices import _fetch_kraken_spot_usd
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            with pytest.raises(RuntimeError, match="Could not reach Kraken Ticker"):
                _fetch_kraken_spot_usd()

    def test_missing_result_key_asset_absent(self) -> None:
        """If ETH result key is missing from response, ETH is simply absent."""
        from taxspine_orchestrator.prices import _fetch_kraken_spot_usd
        result = {
            "XXRPZUSD": {"c": ["0.52000", "100"]},
            "XXBTZUSD": {"c": ["85000.00", "1"]},
            # XETHZUSD missing
            "ADAUSD":   {"c": ["0.45000", "200"]},
            "XLTCZUSD": {"c": ["75.00", "10"]},
        }
        body = json.dumps({"error": [], "result": result}).encode()
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            prices = _fetch_kraken_spot_usd()
        assert "ETH" not in prices


# ── _fetch_norges_bank_usd_nok_current ──────────────────────────────────────


class TestFetchNorgesBankCurrentRate:
    """Norges Bank current rate is correctly parsed."""

    def _make_nb_body(self, rate: float = 10.5) -> bytes:
        body = {
            "data": {
                "structure": {
                    "dimensions": {
                        "observation": [
                            {"values": [{"id": "2026-03-21"}, {"id": "2026-03-22"}]}
                        ]
                    }
                },
                "dataSets": [
                    {
                        "series": {
                            "0:0:0:0:0": {
                                "observations": {
                                    "0": [10.3],
                                    "1": [rate],
                                }
                            }
                        }
                    }
                ],
            }
        }
        return json.dumps(body).encode()

    def _mock_urlopen(self, body: bytes):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_returns_latest_rate(self) -> None:
        from taxspine_orchestrator.prices import _fetch_norges_bank_usd_nok_current
        body = self._make_nb_body(rate=10.75)
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            rate = _fetch_norges_bank_usd_nok_current()
        assert rate == Decimal("10.75")

    def test_returns_decimal(self) -> None:
        from taxspine_orchestrator.prices import _fetch_norges_bank_usd_nok_current
        body = self._make_nb_body()
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            rate = _fetch_norges_bank_usd_nok_current()
        assert isinstance(rate, Decimal)

    def test_network_failure_raises_runtime_error(self) -> None:
        from taxspine_orchestrator.prices import _fetch_norges_bank_usd_nok_current
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            with pytest.raises(RuntimeError, match="Could not reach Norges Bank API"):
                _fetch_norges_bank_usd_nok_current()


# ── fetch_spot_prices_nok ───────────────────────────────────────────────────


class TestFetchSpotPricesNok:
    """fetch_spot_prices_nok() converts USD prices to NOK and caches them."""

    def _mock_kraken(self, usd_prices: dict[str, Decimal]):
        return patch(
            "taxspine_orchestrator.prices._fetch_kraken_spot_usd",
            return_value=usd_prices,
        )

    def _mock_nb(self, rate: Decimal):
        return patch(
            "taxspine_orchestrator.prices._fetch_norges_bank_usd_nok_current",
            return_value=rate,
        )

    def _clear_cache(self):
        import taxspine_orchestrator.prices as p
        p._spot_cache = None
        p._spot_cache_ts = 0.0

    def test_nok_price_is_usd_times_fx(self) -> None:
        self._clear_cache()
        from taxspine_orchestrator.prices import fetch_spot_prices_nok
        usd = {"XRP": Decimal("0.50"), "BTC": Decimal("80000")}
        with self._mock_kraken(usd), self._mock_nb(Decimal("10.0")):
            prices, _ = fetch_spot_prices_nok(["XRP", "BTC"])
        assert prices["XRP"] == Decimal("5.0000")
        assert prices["BTC"] == Decimal("800000.0000")

    def test_returns_as_of_iso_string(self) -> None:
        self._clear_cache()
        from taxspine_orchestrator.prices import fetch_spot_prices_nok
        with self._mock_kraken({"XRP": Decimal("0.50")}), self._mock_nb(Decimal("10.0")):
            _, as_of = fetch_spot_prices_nok(["XRP"])
        assert "T" in as_of  # ISO 8601 datetime
        assert "+00:00" in as_of or "Z" in as_of

    def test_unknown_assets_excluded(self) -> None:
        self._clear_cache()
        from taxspine_orchestrator.prices import fetch_spot_prices_nok
        with self._mock_kraken({"BTC": Decimal("80000")}), self._mock_nb(Decimal("10.0")):
            prices, _ = fetch_spot_prices_nok(["BTC", "UNKNOWN_TOKEN"])
        assert "UNKNOWN_TOKEN" not in prices
        assert "BTC" in prices

    def test_cache_prevents_second_fetch(self) -> None:
        self._clear_cache()
        from taxspine_orchestrator.prices import fetch_spot_prices_nok
        with self._mock_kraken({"XRP": Decimal("0.50")}) as mk, \
             self._mock_nb(Decimal("10.0")):
            fetch_spot_prices_nok(["XRP"])
            fetch_spot_prices_nok(["XRP"])
        # Kraken should only be called once — second call uses cache
        assert mk.call_count == 1

    def test_cache_expires_after_ttl(self) -> None:
        self._clear_cache()
        import taxspine_orchestrator.prices as p
        p._spot_cache_ts = time.time() - p._SPOT_CACHE_TTL - 1  # force expired
        p._spot_cache = {"XRP": Decimal("5.0")}
        from taxspine_orchestrator.prices import fetch_spot_prices_nok
        with self._mock_kraken({"XRP": Decimal("0.60")}) as mk, \
             self._mock_nb(Decimal("10.0")):
            prices, _ = fetch_spot_prices_nok(["XRP"])
        assert mk.call_count == 1
        assert prices["XRP"] == Decimal("6.0000")  # fresh price, not cached 5.0


# ── GET /prices/spot endpoint ────────────────────────────────────────────────


class TestSpotPricesEndpoint:
    """GET /prices/spot returns prices, as_of, from_cache."""

    def _clear_cache(self):
        import taxspine_orchestrator.prices as p
        p._spot_cache = None
        p._spot_cache_ts = 0.0

    def test_returns_prices_dict(self, tmp_path: Path) -> None:
        self._clear_cache()
        from fastapi.testclient import TestClient
        from taxspine_orchestrator.main import app

        with patch("taxspine_orchestrator.prices.fetch_spot_prices_nok") as mock_fn:
            mock_fn.return_value = (
                {"XRP": Decimal("5.25"), "BTC": Decimal("850000.00")},
                "2026-03-23T12:00:00+00:00",
            )
            import taxspine_orchestrator.prices as p
            p._spot_cache = {"XRP": Decimal("5.25")}  # pretend cached
            client = TestClient(app)
            r = client.get("/prices/spot")

        assert r.status_code == 200
        body = r.json()
        assert "prices" in body
        assert "as_of" in body
        assert "from_cache" in body

    def test_503_on_fetch_failure(self) -> None:
        self._clear_cache()
        from fastapi.testclient import TestClient
        from taxspine_orchestrator.main import app
        with patch(
            "taxspine_orchestrator.prices.fetch_spot_prices_nok",
            side_effect=RuntimeError("network down"),
        ):
            client = TestClient(app)
            r = client.get("/prices/spot")
        assert r.status_code == 503


# ── GET /lots/{year}/portfolio?price_type=current ────────────────────────────


class TestPortfolioPriceType:
    """price_type=current uses fetch_spot_prices_nok instead of year-end CSV."""

    def test_price_type_current_calls_spot_fetch(self, tmp_path: Path) -> None:
        """When price_type=current, portfolio uses live prices."""
        from fastapi.testclient import TestClient
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        with patch.object(settings, "LOT_STORE_DB", tmp_path / "lots.db"), \
             patch("taxspine_orchestrator.lots._open_store") as mock_store, \
             patch("taxspine_orchestrator.prices.fetch_spot_prices_nok") as mock_spot:

            # Fake a store with one lot
            from unittest.mock import MagicMock
            fake_lot = MagicMock()
            fake_lot.asset = "XRP"
            fake_lot.remaining_quantity = Decimal("100")
            fake_lot.remaining_cost_basis_nok = Decimal("500")
            fake_lot.basis_status = "resolved"

            fake_store_ctx = MagicMock()
            fake_store_ctx.__enter__ = MagicMock(return_value=fake_store_ctx)
            fake_store_ctx.__exit__ = MagicMock(return_value=False)
            fake_store_ctx.list_years.return_value = [2025]
            fake_store_ctx.load_carry_forward.return_value = [fake_lot]
            mock_store.return_value = fake_store_ctx

            mock_spot.return_value = (
                {"XRP": Decimal("5.25")},
                "2026-03-23T12:00:00+00:00",
            )

            client = TestClient(app)
            r = client.get("/lots/2025/portfolio?include_prices=true&price_type=current")

        assert r.status_code == 200
        body = r.json()
        assert body["price_type"] == "current"
        assert body["prices_as_of"] == "2026-03-23T12:00:00+00:00"
        mock_spot.assert_called_once()

    def test_price_type_year_end_does_not_call_spot(self, tmp_path: Path) -> None:
        """When price_type=year_end (default), spot fetch is not called."""
        from fastapi.testclient import TestClient
        from taxspine_orchestrator.main import app

        with patch("taxspine_orchestrator.lots._open_store") as mock_store, \
             patch("taxspine_orchestrator.lots._load_year_end_prices", return_value={}), \
             patch("taxspine_orchestrator.prices.fetch_spot_prices_nok") as mock_spot:

            fake_store_ctx = MagicMock()
            fake_store_ctx.__enter__ = MagicMock(return_value=fake_store_ctx)
            fake_store_ctx.__exit__ = MagicMock(return_value=False)
            fake_store_ctx.list_years.return_value = [2025]
            fake_store_ctx.load_carry_forward.return_value = []
            mock_store.return_value = fake_store_ctx

            client = TestClient(app)
            r = client.get("/lots/2025/portfolio?include_prices=true&price_type=year_end")

        assert r.status_code == 200
        mock_spot.assert_not_called()

    def test_prices_as_of_present_in_response(self, tmp_path: Path) -> None:
        """prices_as_of is present in the portfolio response when include_prices=true."""
        from fastapi.testclient import TestClient
        from taxspine_orchestrator.main import app

        with patch("taxspine_orchestrator.lots._open_store") as mock_store, \
             patch("taxspine_orchestrator.lots._load_year_end_prices", return_value={}):

            fake_store_ctx = MagicMock()
            fake_store_ctx.__enter__ = MagicMock(return_value=fake_store_ctx)
            fake_store_ctx.__exit__ = MagicMock(return_value=False)
            fake_store_ctx.list_years.return_value = [2025]
            fake_store_ctx.load_carry_forward.return_value = []
            mock_store.return_value = fake_store_ctx

            client = TestClient(app)
            r = client.get("/lots/2025/portfolio?include_prices=true")

        assert r.status_code == 200
        body = r.json()
        assert "prices_as_of" in body
        assert body["prices_as_of"] == "2025-12-31T00:00:00+00:00"
