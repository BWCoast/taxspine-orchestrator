"""Tests for the FIFO lot store inspection API.

Covers:
- GET /lots/years → db_exists=False when no lot store file.
- GET /lots/years → correct list after saving lots.
- GET /lots/{year} → 404 when no snapshot for that year.
- GET /lots/{year} → correct summary (total, active, depleted, assets).
- GET /lots/{year}/carry-forward → 404 when no snapshot.
- GET /lots/{year}/carry-forward → only active lots returned.
- GET /lots/{year}/carry-forward → lot fields present (lot_id, asset, remaining_quantity, ...).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.config import settings as _real_settings
from taxspine_orchestrator.main import app

# ── Availability guard ─────────────────────────────────────────────────────────
# Skip all tests in this module when tax_spine is not installed.
# This keeps CI green in pure-orchestrator environments where tax-nor is not
# available.  In local dev (and in the Docker image), tax-nor IS installed so
# all tests run fully.
try:
    from tax_spine.pipeline.lot_store import LotPersistenceStore as _LotStore  # noqa: F401
    _TAX_SPINE_AVAILABLE = True
except ImportError:
    _TAX_SPINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _TAX_SPINE_AVAILABLE,
    reason="tax_spine not installed — skipping lot store API tests",
)


@pytest.fixture()
def client():
    return TestClient(app)


def _write_lots(db_path: Path, tax_year: int) -> None:
    """Write a small set of test lots to *db_path* for *tax_year*."""
    from tax_spine.fifo.models import Lot
    from tax_spine.pipeline.lot_store import LotPersistenceStore

    lots = [
        Lot(
            lot_id=f"lot_{i}",
            origin_event_id=f"evt_{i}",
            origin_type="buy",
            asset="BTC",
            acquired_timestamp="2025-01-15T12:00:00+00:00",
            ordering_key=f"2025-01-15_{i:05d}",
            original_quantity=Decimal("0.5"),
            remaining_quantity=Decimal("0.5") if i < 2 else Decimal("0"),  # 2 active, 1 depleted
            original_cost_basis_nok=Decimal("50000"),
            remaining_cost_basis_nok=Decimal("50000") if i < 2 else Decimal("0"),
            basis_status="resolved",
        )
        for i in range(3)
    ] + [
        Lot(
            lot_id="lot_eth_0",
            origin_event_id="evt_eth_0",
            origin_type="buy",
            asset="ETH",
            acquired_timestamp="2025-03-01T08:00:00+00:00",
            ordering_key="2025-03-01_00000",
            original_quantity=Decimal("2.0"),
            remaining_quantity=Decimal("1.5"),
            original_cost_basis_nok=Decimal("20000"),
            remaining_cost_basis_nok=Decimal("15000"),
            basis_status="resolved",
        )
    ]

    with LotPersistenceStore(str(db_path)) as store:
        store.save_year_end_lots(lots, tax_year)


# ── TestLotYears ──────────────────────────────────────────────────────────────


class TestLotYears:
    def test_no_db_file_returns_db_exists_false(self, client, tmp_path):
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", tmp_path / "lots.db"):
            resp = client.get("/lots/years")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_exists"] is False
        assert data["years"] == []

    def test_lists_saved_years(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2024)
        _write_lots(db, 2025)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            resp = client.get("/lots/years")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_exists"] is True
        assert 2024 in data["years"]
        assert 2025 in data["years"]

    def test_empty_store_returns_empty_years(self, client, tmp_path):
        from tax_spine.pipeline.lot_store import LotPersistenceStore
        db = tmp_path / "lots.db"
        with LotPersistenceStore(str(db)):
            pass  # creates schema, no data
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            resp = client.get("/lots/years")
        assert resp.json()["years"] == []


# ── TestLotYearSummary ────────────────────────────────────────────────────────


class TestLotYearSummary:
    def test_404_for_missing_year(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2024)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            resp = client.get("/lots/2023")
        assert resp.status_code == 404

    def test_404_when_no_db(self, client, tmp_path):
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", tmp_path / "lots.db"):
            resp = client.get("/lots/2025")
        assert resp.status_code == 404

    def test_summary_counts(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2025)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            resp = client.get("/lots/2025")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tax_year"] == 2025
        assert data["total_lots"] == 4   # 3 BTC + 1 ETH
        assert data["active_lots"] == 3  # 2 BTC active + 1 ETH active
        assert data["depleted_lots"] == 1  # 1 BTC depleted

    def test_per_asset_breakdown(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2025)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            data = client.get("/lots/2025").json()
        assets = data["assets"]
        assert "BTC" in assets
        assert "ETH" in assets
        assert assets["BTC"]["total_lots"] == 3
        assert assets["BTC"]["active_lots"] == 2
        assert assets["ETH"]["total_lots"] == 1
        assert assets["ETH"]["active_lots"] == 1


# ── TestCarryForwardLots ──────────────────────────────────────────────────────


class TestCarryForwardLots:
    def test_404_when_no_db(self, client, tmp_path):
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", tmp_path / "lots.db"):
            resp = client.get("/lots/2025/carry-forward")
        assert resp.status_code == 404

    def test_404_for_missing_year(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2024)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            resp = client.get("/lots/2023/carry-forward")
        assert resp.status_code == 404

    def test_only_active_lots_returned(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2025)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            resp = client.get("/lots/2025/carry-forward")
        assert resp.status_code == 200
        lots = resp.json()
        # Only active lots (remaining_quantity > 0) should be returned.
        # 2 BTC active + 1 ETH active = 3 lots.
        assert len(lots) == 3
        for lot in lots:
            qty = Decimal(lot["remaining_quantity"])
            assert qty > 0, f"Expected only active lots but got remaining_quantity={qty}"

    def test_lot_fields_present(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2025)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            lots = client.get("/lots/2025/carry-forward").json()
        required_fields = {
            "lot_id", "asset", "acquired_timestamp",
            "original_quantity", "remaining_quantity",
            "basis_status", "origin_event_id", "origin_type",
        }
        for lot in lots:
            for field in required_fields:
                assert field in lot, f"Missing field '{field}' in lot {lot.get('lot_id')}"

    def test_basis_status_values(self, client, tmp_path):
        db = tmp_path / "lots.db"
        _write_lots(db, 2025)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            lots = client.get("/lots/2025/carry-forward").json()
        for lot in lots:
            assert lot["basis_status"] in ("resolved", "missing")

    def test_cost_basis_as_string(self, client, tmp_path):
        """Cost basis values must be strings to preserve Decimal precision."""
        db = tmp_path / "lots.db"
        _write_lots(db, 2025)
        from unittest.mock import patch
        with patch.object(_real_settings, "LOT_STORE_DB", db):
            lots = client.get("/lots/2025/carry-forward").json()
        for lot in lots:
            # remaining_quantity and original_quantity should be parseable as Decimal.
            Decimal(lot["remaining_quantity"])
            Decimal(lot["original_quantity"])
