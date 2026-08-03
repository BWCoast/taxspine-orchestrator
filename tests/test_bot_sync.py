"""test_bot_sync.py — Tests for the bot DuckDB sync module.

Covers:
- BotDuckDbSyncWorker._fill_to_events() — fill row → generic-events mapping
- BotDuckDbSyncWorker._lp_to_events()  — LP event row → generic-events mapping
  (one-sided deposit, two-sided deposit, one-sided withdrawal, two-sided withdrawal)
- BotDuckDbSyncWorker._write_csv()     — CSV output format and snapshot overwrite
- POST /bot/sync                       — 503 guards (no duckdb, no BOT_DB, missing file)
- GET /bot/sync/status                 — initial state
- GET /bot/lot-impact/{symbol}         — no store, symbol normalisation, 503 on missing package
"""

from __future__ import annotations

import csv
import dataclasses
import datetime
import io
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_client() -> TestClient:
    from taxspine_orchestrator.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_bot_state():
    """Reset module-level sync state between tests."""
    import taxspine_orchestrator.bot as _bot
    _bot._last_sync_result = None
    yield
    _bot._last_sync_result = None


# ── TestFillToEvents ──────────────────────────────────────────────────────────


class TestFillToEvents:
    """BotDuckDbSyncWorker._fill_to_events() maps fill rows correctly."""

    def _worker(self, tmp_path: Path) -> "BotDuckDbSyncWorker":
        from taxspine_orchestrator.bot import BotDuckDbSyncWorker
        return BotDuckDbSyncWorker(
            db_path=tmp_path / "bot.duckdb",
            output_path=tmp_path / "out.csv",
        )

    def test_empty_list_returns_empty(self, tmp_path: Path):
        w = self._worker(tmp_path)
        assert w._fill_to_events([]) == []

    def test_basic_fill_maps_to_trade_event(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {
            "fill_id": "f-001",
            "strategy": "arb",
            "venue": "kraken",
            "chain_id": None,
            "asset_in": "XRP",
            "amount_in": Decimal("100.5"),
            "asset_out": "USD",
            "amount_out": Decimal("55.25"),
            "fee_asset": "XRP",
            "fee_amount": Decimal("0.1"),
            "tx_hash": "abc123",
            "order_id": "ORD-99",
            "executed_at": datetime.datetime(2025, 6, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        }
        events = w._fill_to_events([fill])
        assert len(events) == 1
        e = events[0]
        assert e["event_id"] == "f-001"
        assert e["event_type"] == "TRADE"
        assert e["asset_in"] == "XRP"
        assert e["asset_out"] == "USD"
        assert Decimal(e["amount_in"]) == Decimal("100.5")
        assert Decimal(e["amount_out"]) == Decimal("55.25")

    def test_source_is_bot_colon_venue(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {"fill_id": "x", "strategy": "s", "venue": "kraken",
                "asset_in": "BTC", "amount_in": 1, "asset_out": "NOK", "amount_out": 900000,
                "fee_asset": None, "fee_amount": None, "tx_hash": None,
                "order_id": None, "executed_at": None, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert e["source"] == "bot:kraken"

    def test_account_is_strategy(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {"fill_id": "x", "strategy": "grid_eth", "venue": "xrpl",
                "asset_in": "ETH", "amount_in": 1, "asset_out": "USDC", "amount_out": 3000,
                "fee_asset": None, "fee_amount": None, "tx_hash": None,
                "order_id": None, "executed_at": None, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert e["account"] == "grid_eth"

    def test_note_contains_strategy(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {"fill_id": "x", "strategy": "arb_xrp", "venue": "v",
                "asset_in": "XRP", "amount_in": 1, "asset_out": "ETH", "amount_out": 1,
                "fee_asset": None, "fee_amount": None, "tx_hash": None,
                "order_id": None, "executed_at": None, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert "strategy:arb_xrp" in e["note"]

    def test_complex_tax_treatment_is_false(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {"fill_id": "x", "strategy": "s", "venue": "v",
                "asset_in": "BTC", "amount_in": 1, "asset_out": "NOK", "amount_out": 900000,
                "fee_asset": None, "fee_amount": None, "tx_hash": None,
                "order_id": None, "executed_at": None, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert e["complex_tax_treatment"] == "False"

    def test_fee_fields_mapped(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {"fill_id": "x", "strategy": "s", "venue": "v",
                "asset_in": "BTC", "amount_in": 1, "asset_out": "NOK", "amount_out": 900000,
                "fee_asset": "BTC", "fee_amount": Decimal("0.0001"),
                "tx_hash": "hash", "order_id": "ord", "executed_at": None, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert e["fee_asset"] == "BTC"
        assert Decimal(e["fee_amount"]) == Decimal("0.0001")

    def test_exchange_tx_id_is_order_id(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {"fill_id": "x", "strategy": "s", "venue": "v",
                "asset_in": "A", "amount_in": 1, "asset_out": "B", "amount_out": 1,
                "fee_asset": None, "fee_amount": None, "tx_hash": None,
                "order_id": "ORDER-42", "executed_at": None, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert e["exchange_tx_id"] == "ORDER-42"

    def test_timestamp_from_datetime_object(self, tmp_path: Path):
        w = self._worker(tmp_path)
        ts = datetime.datetime(2025, 3, 15, 8, 30, 0, tzinfo=datetime.timezone.utc)
        fill = {"fill_id": "x", "strategy": "s", "venue": "v",
                "asset_in": "A", "amount_in": 1, "asset_out": "B", "amount_out": 1,
                "fee_asset": None, "fee_amount": None, "tx_hash": None,
                "order_id": None, "executed_at": ts, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert "2025-03-15" in e["timestamp"]
        assert "+00:00" in e["timestamp"] or "Z" in e["timestamp"]

    def test_multiple_fills_produce_multiple_events(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fills = [
            {"fill_id": f"f-{i}", "strategy": "s", "venue": "v",
             "asset_in": "BTC", "amount_in": i, "asset_out": "NOK", "amount_out": i * 900000,
             "fee_asset": None, "fee_amount": None, "tx_hash": None,
             "order_id": None, "executed_at": None, "chain_id": None}
            for i in range(1, 4)
        ]
        events = w._fill_to_events(fills)
        assert len(events) == 3
        assert [e["event_id"] for e in events] == ["f-1", "f-2", "f-3"]

    def test_none_fee_becomes_empty_string(self, tmp_path: Path):
        w = self._worker(tmp_path)
        fill = {"fill_id": "x", "strategy": "s", "venue": "v",
                "asset_in": "A", "amount_in": 1, "asset_out": "B", "amount_out": 1,
                "fee_asset": None, "fee_amount": None, "tx_hash": None,
                "order_id": None, "executed_at": None, "chain_id": None}
        e = w._fill_to_events([fill])[0]
        assert e["fee_asset"] == ""
        assert e["fee_amount"] == ""


# ── TestLpToEvents ────────────────────────────────────────────────────────────


class TestLpToEvents:
    """BotDuckDbSyncWorker._lp_to_events() maps LP event rows correctly."""

    def _worker(self, tmp_path: Path) -> "BotDuckDbSyncWorker":
        from taxspine_orchestrator.bot import BotDuckDbSyncWorker
        return BotDuckDbSyncWorker(
            db_path=tmp_path / "bot.duckdb",
            output_path=tmp_path / "out.csv",
        )

    def _lp_row(self, **overrides) -> dict:
        base = {
            "event_id": "lp-001",
            "lp_action": "deposit",
            "pool_address": "rPoolXXX",
            "venue": "xrpl_dex",
            "chain_id": "xrpl",
            "asset_a": "XRP",
            "amount_a": Decimal("1000"),
            "asset_b": None,
            "amount_b": None,
            "lp_token": "LP.rPoolXXX",
            "lp_amount": Decimal("500"),
            "tx_hash": "lpTx1",
            "executed_at": datetime.datetime(2025, 7, 1, tzinfo=datetime.timezone.utc),
        }
        base.update(overrides)
        return base

    def test_empty_list_returns_empty(self, tmp_path: Path):
        w = self._worker(tmp_path)
        assert w._lp_to_events([]) == []

    def test_deposit_one_sided_asset_in_is_asset_a(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(lp_action="deposit", asset_b=None)])[0]
        assert e["asset_in"] == "XRP"
        assert e["asset_out"] == "LP.rPoolXXX"
        assert Decimal(e["amount_in"]) == Decimal("1000")
        assert Decimal(e["amount_out"]) == Decimal("500")

    def test_deposit_label(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(lp_action="deposit")])[0]
        assert e["label"] == "lp_deposit"

    def test_withdraw_label(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(lp_action="withdraw")])[0]
        assert e["label"] == "lp_withdraw"

    def test_withdraw_one_sided_lp_token_is_asset_in(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(lp_action="withdraw", asset_b=None)])[0]
        assert e["asset_in"] == "LP.rPoolXXX"
        assert e["asset_out"] == "XRP"
        assert Decimal(e["amount_in"]) == Decimal("500")
        assert Decimal(e["amount_out"]) == Decimal("1000")

    def test_all_lp_events_have_complex_tax_treatment_true(self, tmp_path: Path):
        w = self._worker(tmp_path)
        for action in ("deposit", "withdraw"):
            e = w._lp_to_events([self._lp_row(lp_action=action)])[0]
            assert e["complex_tax_treatment"] == "True", f"Failed for action={action}"

    def test_event_type_is_trade(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row()])[0]
        assert e["event_type"] == "TRADE"

    def test_deposit_two_sided_encodes_asset_b_in_note(self, tmp_path: Path):
        w = self._worker(tmp_path)
        row = self._lp_row(
            lp_action="deposit",
            asset_b="ETH",
            amount_b=Decimal("0.5"),
        )
        e = w._lp_to_events([row])[0]
        assert "asset_b:ETH" in e["note"]
        assert "0.5" in e["note"]

    def test_withdraw_two_sided_encodes_asset_b_in_note(self, tmp_path: Path):
        w = self._worker(tmp_path)
        row = self._lp_row(
            lp_action="withdraw",
            asset_b="SOL",
            amount_b=Decimal("2.0"),
        )
        e = w._lp_to_events([row])[0]
        assert "asset_b:SOL" in e["note"]
        assert "2.0" in e["note"]

    def test_one_sided_deposit_no_asset_b_in_note(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(lp_action="deposit", asset_b=None)])[0]
        assert "asset_b" not in e["note"]

    def test_pool_address_in_note(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row()])[0]
        assert "pool:rPoolXXX" in e["note"]

    def test_source_is_bot_colon_venue(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(venue="xrpl_dex")])[0]
        assert e["source"] == "bot:xrpl_dex"

    def test_account_is_chain_id(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(chain_id="ethereum")])[0]
        assert e["account"] == "ethereum"

    def test_event_id_preserved(self, tmp_path: Path):
        w = self._worker(tmp_path)
        e = w._lp_to_events([self._lp_row(event_id="lp-abc-999")])[0]
        assert e["event_id"] == "lp-abc-999"


# ── TestWriteCsv ──────────────────────────────────────────────────────────────


class TestWriteCsv:
    """BotDuckDbSyncWorker._write_csv() writes a valid generic-events CSV."""

    def _worker(self, tmp_path: Path) -> "BotDuckDbSyncWorker":
        from taxspine_orchestrator.bot import BotDuckDbSyncWorker
        return BotDuckDbSyncWorker(
            db_path=tmp_path / "bot.duckdb",
            output_path=tmp_path / "out.csv",
        )

    def _sample_row(self) -> dict:
        from taxspine_orchestrator.bot import _GENERIC_EVENTS_COLUMNS
        return {col: f"val_{col}" for col in _GENERIC_EVENTS_COLUMNS}

    def test_writes_header(self, tmp_path: Path):
        from taxspine_orchestrator.bot import _GENERIC_EVENTS_COLUMNS
        w = self._worker(tmp_path)
        w._write_csv([self._sample_row()])
        out = (tmp_path / "out.csv").read_text(encoding="utf-8")
        first_line = out.splitlines()[0]
        for col in _GENERIC_EVENTS_COLUMNS:
            assert col in first_line

    def test_writes_data_row(self, tmp_path: Path):
        w = self._worker(tmp_path)
        row = self._sample_row()
        row["event_id"] = "test-event-123"
        w._write_csv([row])
        out = (tmp_path / "out.csv").read_text(encoding="utf-8")
        assert "test-event-123" in out

    def test_overwrites_on_second_call(self, tmp_path: Path):
        w = self._worker(tmp_path)
        row1 = self._sample_row()
        row1["event_id"] = "first"
        w._write_csv([row1])

        row2 = self._sample_row()
        row2["event_id"] = "second"
        w._write_csv([row2])

        out = (tmp_path / "out.csv").read_text(encoding="utf-8")
        assert "second" in out
        assert "first" not in out  # old content gone — snapshot model

    def test_empty_rows_writes_header_only(self, tmp_path: Path):
        from taxspine_orchestrator.bot import _GENERIC_EVENTS_COLUMNS
        w = self._worker(tmp_path)
        w._write_csv([])
        out = (tmp_path / "out.csv").read_text(encoding="utf-8")
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == 1  # header only
        assert _GENERIC_EVENTS_COLUMNS[0] in lines[0]

    def test_creates_parent_dirs(self, tmp_path: Path):
        from taxspine_orchestrator.bot import BotDuckDbSyncWorker
        nested = tmp_path / "a" / "b" / "c" / "out.csv"
        w = BotDuckDbSyncWorker(db_path=tmp_path / "bot.duckdb", output_path=nested)
        w._write_csv([])
        assert nested.is_file()

    def test_csv_is_parseable(self, tmp_path: Path):
        from taxspine_orchestrator.bot import _GENERIC_EVENTS_COLUMNS
        w = self._worker(tmp_path)
        rows = [self._sample_row() for _ in range(3)]
        w._write_csv(rows)
        with (tmp_path / "out.csv").open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            data = list(reader)
        assert len(data) == 3
        assert set(data[0].keys()) == set(_GENERIC_EVENTS_COLUMNS)


# ── TestSyncEndpoint ──────────────────────────────────────────────────────────


class TestSyncEndpoint:
    """POST /bot/sync — 503 guards when DuckDB or BOT_DB is unavailable."""

    def test_503_when_duckdb_not_available(self):
        """503 with clear message when duckdb package is not installed."""
        client = _make_client()
        with patch("taxspine_orchestrator.bot._DUCKDB_AVAILABLE", False):
            r = client.post("/bot/sync")
        assert r.status_code == 503
        assert "duckdb" in r.json()["detail"].lower()

    def test_503_when_bot_db_not_configured(self):
        """503 when BOT_DB is None (env var not set)."""
        client = _make_client()
        mock_settings = MagicMock()
        mock_settings.BOT_DB = None
        with patch("taxspine_orchestrator.bot._DUCKDB_AVAILABLE", True), \
             patch("taxspine_orchestrator.bot.settings", mock_settings):
            r = client.post("/bot/sync")
        assert r.status_code == 503
        assert "BOT_DB" in r.json()["detail"]

    def test_503_when_bot_db_file_missing(self, tmp_path: Path):
        """503 when BOT_DB is set but the file does not exist."""
        client = _make_client()
        missing_path = tmp_path / "nonexistent.duckdb"
        mock_settings = MagicMock()
        mock_settings.BOT_DB = missing_path
        mock_settings.DATA_DIR = tmp_path
        with patch("taxspine_orchestrator.bot._DUCKDB_AVAILABLE", True), \
             patch("taxspine_orchestrator.bot.settings", mock_settings):
            r = client.post("/bot/sync")
        assert r.status_code == 503
        assert "not found" in r.json()["detail"].lower()


# ── TestSyncStatus ────────────────────────────────────────────────────────────


class TestSyncStatus:
    """GET /bot/sync/status — reflects last sync result."""

    def test_never_synced_returns_synced_false(self):
        client = _make_client()
        r = client.get("/bot/sync/status")
        assert r.status_code == 200
        data = r.json()
        assert data["synced"] is False
        assert data["last_sync"] is None

    def test_after_sync_error_status_reflects_it(self):
        """If a sync recorded an error, status exposes it."""
        from taxspine_orchestrator.bot import BotSyncResult
        import taxspine_orchestrator.bot as _bot
        _bot._last_sync_result = BotSyncResult(
            synced_at="2025-06-01T12:00:00+00:00",
            fills_written=0,
            lp_events_written=0,
            total_events=0,
            output_path="/tmp/out.csv",
            error="Could not open DuckDB: file not found",
        )
        client = _make_client()
        r = client.get("/bot/sync/status")
        assert r.status_code == 200
        data = r.json()
        assert data["synced"] is True
        assert data["last_sync"]["error"] is not None
        assert "DuckDB" in data["last_sync"]["error"]

    def test_successful_sync_status_has_counts(self):
        """A successful prior sync exposes fill/lp counts."""
        from taxspine_orchestrator.bot import BotSyncResult
        import taxspine_orchestrator.bot as _bot
        _bot._last_sync_result = BotSyncResult(
            synced_at="2025-06-01T12:00:00+00:00",
            fills_written=5,
            lp_events_written=2,
            total_events=7,
            output_path="/tmp/out.csv",
        )
        client = _make_client()
        r = client.get("/bot/sync/status")
        data = r.json()
        assert data["last_sync"]["fills_written"] == 5
        assert data["last_sync"]["lp_events_written"] == 2
        assert data["last_sync"]["total_events"] == 7
        assert data["last_sync"]["error"] is None


# ── TestLotImpact ─────────────────────────────────────────────────────────────


class TestLotImpact:
    """GET /bot/lot-impact/{symbol} — prior FIFO lot check."""

    def test_no_store_returns_has_prior_lots_false(self, tmp_path: Path):
        """When no lot store exists, impact is zero — symbol is safe."""
        client = _make_client()
        mock_settings = MagicMock()
        mock_settings.LOT_STORE_DB = tmp_path / "nonexistent.db"

        with patch("taxspine_orchestrator.bot.settings", mock_settings):
            r = client.get("/bot/lot-impact/BTC")

        assert r.status_code == 200
        data = r.json()
        assert data["has_prior_lots"] is False
        assert data["db_exists"] is False
        assert data["lot_count"] == 0
        assert data["warning"] is None

    def test_symbol_is_uppercased(self, tmp_path: Path):
        """Lowercase symbol in URL is uppercased in the response."""
        client = _make_client()
        mock_settings = MagicMock()
        mock_settings.LOT_STORE_DB = tmp_path / "nonexistent.db"

        with patch("taxspine_orchestrator.bot.settings", mock_settings):
            r = client.get("/bot/lot-impact/eth")

        assert r.status_code == 200
        assert r.json()["symbol"] == "ETH"

    def test_503_when_tax_spine_not_importable(self):
        """503 when tax_spine package is not installed."""
        client = _make_client()

        with patch.dict("sys.modules", {"tax_spine.pipeline.lot_store": None}):
            with patch("builtins.__import__", side_effect=ImportError("no tax_spine")):
                # This will 503 because LotPersistenceStore can't be imported.
                # We patch at the bot module level to simulate a missing package.
                pass  # handled via the import mock below

        # Alternative: patch the import inside the endpoint directly.
        import sys
        original = sys.modules.get("tax_spine.pipeline.lot_store")
        sys.modules["tax_spine.pipeline.lot_store"] = None  # type: ignore[assignment]
        try:
            r = client.get("/bot/lot-impact/BTC")
            # When the module is None, importlib raises ImportError
            assert r.status_code in (503, 500)
        finally:
            if original is None:
                sys.modules.pop("tax_spine.pipeline.lot_store", None)
            else:
                sys.modules["tax_spine.pipeline.lot_store"] = original

    def test_matching_lots_returns_has_prior_lots_true(self, tmp_path: Path):
        """When open lots exist for the symbol, has_prior_lots is True."""
        client = _make_client()

        # Build a fake LotPersistenceStore that returns one open BTC lot.
        fake_lot = MagicMock()
        fake_lot.asset = "BTC"
        fake_lot.remaining_quantity = Decimal("0.5")
        fake_lot.acquired_timestamp = "2023-01-15T00:00:00+00:00"
        fake_lot.remaining_cost_basis_nok = Decimal("150000")

        fake_store = MagicMock()
        fake_store.__enter__ = lambda s: s
        fake_store.__exit__ = MagicMock(return_value=False)
        fake_store.list_years.return_value = [2023]
        fake_store.load_carry_forward.return_value = [fake_lot]

        fake_db = tmp_path / "lots.db"
        fake_db.touch()

        mock_settings = MagicMock()
        mock_settings.LOT_STORE_DB = fake_db

        FakeLotStore = MagicMock(return_value=fake_store)

        with patch("taxspine_orchestrator.bot.settings", mock_settings), \
             patch("tax_spine.pipeline.lot_store.LotPersistenceStore", FakeLotStore):
            # Temporarily replace the import inside the function
            import taxspine_orchestrator.bot as _bot_mod

            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else None

            # Simpler approach: patch at the sys.modules level
            import sys
            fake_module = MagicMock()
            fake_module.LotPersistenceStore = FakeLotStore
            sys.modules["tax_spine.pipeline.lot_store"] = fake_module
            try:
                r = client.get("/bot/lot-impact/BTC")
            finally:
                sys.modules.pop("tax_spine.pipeline.lot_store", None)

        assert r.status_code == 200
        data = r.json()
        assert data["has_prior_lots"] is True
        assert data["lot_count"] == 1
        assert data["symbol"] == "BTC"
        assert data["warning"] is not None
        assert "BTC" in data["warning"]

    def test_no_matching_lots_returns_false(self, tmp_path: Path):
        """Lots exist for other assets but not for the queried symbol."""
        client = _make_client()

        fake_lot = MagicMock()
        fake_lot.asset = "ETH"
        fake_lot.remaining_quantity = Decimal("2.0")
        fake_lot.acquired_timestamp = "2023-03-01T00:00:00+00:00"
        fake_lot.remaining_cost_basis_nok = Decimal("60000")

        fake_store = MagicMock()
        fake_store.__enter__ = lambda s: s
        fake_store.__exit__ = MagicMock(return_value=False)
        fake_store.list_years.return_value = [2023]
        fake_store.load_carry_forward.return_value = [fake_lot]

        fake_db = tmp_path / "lots.db"
        fake_db.touch()

        mock_settings = MagicMock()
        mock_settings.LOT_STORE_DB = fake_db

        FakeLotStore = MagicMock(return_value=fake_store)

        import sys
        fake_module = MagicMock()
        fake_module.LotPersistenceStore = FakeLotStore
        sys.modules["tax_spine.pipeline.lot_store"] = fake_module
        try:
            with patch("taxspine_orchestrator.bot.settings", mock_settings):
                r = client.get("/bot/lot-impact/BTC")  # querying BTC, only ETH lots exist
        finally:
            sys.modules.pop("tax_spine.pipeline.lot_store", None)

        assert r.status_code == 200
        data = r.json()
        assert data["has_prior_lots"] is False
        assert data["lot_count"] == 0

    def test_warning_mentions_fifo_pool(self, tmp_path: Path):
        """Warning text explains the FIFO pool sharing risk."""
        client = _make_client()

        fake_lot = MagicMock()
        fake_lot.asset = "XRP"
        fake_lot.remaining_quantity = Decimal("5000")
        fake_lot.acquired_timestamp = "2022-06-01T00:00:00+00:00"
        fake_lot.remaining_cost_basis_nok = Decimal("25000")

        fake_store = MagicMock()
        fake_store.__enter__ = lambda s: s
        fake_store.__exit__ = MagicMock(return_value=False)
        fake_store.list_years.return_value = [2022]
        fake_store.load_carry_forward.return_value = [fake_lot]

        fake_db = tmp_path / "lots.db"
        fake_db.touch()

        mock_settings = MagicMock()
        mock_settings.LOT_STORE_DB = fake_db

        FakeLotStore = MagicMock(return_value=fake_store)

        import sys
        fake_module = MagicMock()
        fake_module.LotPersistenceStore = FakeLotStore
        sys.modules["tax_spine.pipeline.lot_store"] = fake_module
        try:
            with patch("taxspine_orchestrator.bot.settings", mock_settings):
                r = client.get("/bot/lot-impact/XRP")
        finally:
            sys.modules.pop("tax_spine.pipeline.lot_store", None)

        assert r.status_code == 200
        warning = r.json()["warning"]
        assert "FIFO" in warning
        assert "XRP" in warning

    def test_empty_lot_store_years_returns_false(self, tmp_path: Path):
        """Lot store exists but has no years — treat as no prior lots."""
        client = _make_client()

        fake_store = MagicMock()
        fake_store.__enter__ = lambda s: s
        fake_store.__exit__ = MagicMock(return_value=False)
        fake_store.list_years.return_value = []

        fake_db = tmp_path / "lots.db"
        fake_db.touch()

        mock_settings = MagicMock()
        mock_settings.LOT_STORE_DB = fake_db

        FakeLotStore = MagicMock(return_value=fake_store)

        import sys
        fake_module = MagicMock()
        fake_module.LotPersistenceStore = FakeLotStore
        sys.modules["tax_spine.pipeline.lot_store"] = fake_module
        try:
            with patch("taxspine_orchestrator.bot.settings", mock_settings):
                r = client.get("/bot/lot-impact/SOL")
        finally:
            sys.modules.pop("tax_spine.pipeline.lot_store", None)

        assert r.status_code == 200
        data = r.json()
        assert data["has_prior_lots"] is False
        assert data["db_exists"] is True


# ── TestFormatHelpers ─────────────────────────────────────────────────────────


class TestFormatHelpers:
    """Unit tests for _format_ts and _format_decimal."""

    def test_format_ts_none(self):
        from taxspine_orchestrator.bot import _format_ts
        assert _format_ts(None) == ""

    def test_format_ts_aware_datetime(self):
        from taxspine_orchestrator.bot import _format_ts
        dt = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        result = _format_ts(dt)
        assert "2025-01-01" in result
        assert "+00:00" in result

    def test_format_ts_naive_datetime_gets_utc(self):
        from taxspine_orchestrator.bot import _format_ts
        dt = datetime.datetime(2025, 6, 15, 8, 0, 0)  # no tz
        result = _format_ts(dt)
        assert "2025-06-15" in result
        assert "+00:00" in result

    def test_format_ts_string_passthrough(self):
        from taxspine_orchestrator.bot import _format_ts
        assert _format_ts("2025-01-01T00:00:00Z") == "2025-01-01T00:00:00Z"

    def test_format_decimal_none(self):
        from taxspine_orchestrator.bot import _format_decimal
        assert _format_decimal(None) == ""

    def test_format_decimal_int(self):
        from taxspine_orchestrator.bot import _format_decimal
        assert _format_decimal(100) == "100"

    def test_format_decimal_decimal(self):
        from taxspine_orchestrator.bot import _format_decimal
        assert _format_decimal(Decimal("1.23456789")) == "1.23456789"

    def test_format_decimal_float_string(self):
        from taxspine_orchestrator.bot import _format_decimal
        result = _format_decimal("0.001")
        assert Decimal(result) == Decimal("0.001")

    def test_format_decimal_zero(self):
        from taxspine_orchestrator.bot import _format_decimal
        assert _format_decimal(0) == "0"
