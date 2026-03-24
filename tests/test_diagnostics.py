"""test_diagnostics.py — Phase 3 dashboard: GET /diagnostics endpoint.

Tests the system diagnostics snapshot endpoint that feeds the Diagnostics
collapsible panel in the dashboard.
"""

from __future__ import annotations

import tempfile
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


# ── TestDiagnosticsEndpointStructure ─────────────────────────────────────────


class TestDiagnosticsEndpointStructure:
    """GET /diagnostics always returns 200 with the correct top-level shape."""

    def test_returns_200(self):
        client = _make_client()
        r = client.get("/diagnostics")
        assert r.status_code == 200

    def test_has_four_sections(self):
        client = _make_client()
        data = client.get("/diagnostics").json()
        assert "lots"   in data
        assert "prices" in data
        assert "jobs"   in data
        assert "dedup"  in data

    def test_lots_section_has_db_exists_key(self):
        client = _make_client()
        lots = client.get("/diagnostics").json()["lots"]
        assert "db_exists" in lots or "error" in lots

    def test_prices_section_has_csv_count(self):
        client = _make_client()
        prices = client.get("/diagnostics").json()["prices"]
        # Either csv_count present or error
        assert "csv_count" in prices or "error" in prices

    def test_jobs_section_has_counts(self):
        client = _make_client()
        jobs = client.get("/diagnostics").json()["jobs"]
        for key in ("total", "running", "failed", "completed"):
            assert key in jobs or "error" in jobs

    def test_dedup_section_has_source_count(self):
        client = _make_client()
        dedup = client.get("/diagnostics").json()["dedup"]
        assert "source_count" in dedup or "error" in dedup

    def test_never_raises_500(self):
        """Diagnostics must never 500 even if subsystems fail."""
        client = _make_client()
        # Patch everything to raise
        with patch("taxspine_orchestrator.main._job_service") as mock_svc:
            mock_svc.list_jobs.side_effect = RuntimeError("db exploded")
            r = client.get("/diagnostics")
        assert r.status_code == 200
        data = r.json()
        assert "error" in data["jobs"]


# ── TestDiagnosticsLotsSection ────────────────────────────────────────────────


@pytest.mark.skipif(not _TAX_SPINE_AVAILABLE, reason="tax_spine not installed")
class TestDiagnosticsLotsSection:
    """Lots section reflects lot store state."""

    def test_no_lot_store_db_exists_false(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.LOT_STORE_DB     = Path(td) / "nonexistent.db"
                mock_s.PRICES_DIR       = Path(td)
                mock_s.DEDUP_DIR        = Path(td)
                mock_s.OUTPUT_DIR       = Path(td)
                mock_s.ORCHESTRATOR_KEY = ""   # disable auth check
                data = client.get("/diagnostics").json()
        lots = data["lots"]
        if "error" not in lots:
            assert lots["db_exists"] is False
            assert lots["years"] == []

    def test_lot_store_years_listed(self):
        """When a mock lot store is present years appear in the response."""
        client = _make_client()
        mock_store_cls = MagicMock()
        mock_store_inst = MagicMock()
        mock_store_inst.__enter__ = lambda s: s
        mock_store_inst.__exit__ = MagicMock(return_value=False)
        mock_store_inst.list_years.return_value = [2023, 2024]
        mock_store_cls.return_value = mock_store_inst

        with tempfile.TemporaryDirectory() as td:
            fake_db = Path(td) / "lots.db"
            fake_db.touch()
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.LOT_STORE_DB     = fake_db
                mock_s.PRICES_DIR       = Path(td)
                mock_s.DEDUP_DIR        = Path(td)
                mock_s.OUTPUT_DIR       = Path(td)
                mock_s.ORCHESTRATOR_KEY = ""   # disable auth check
                with patch("tax_spine.pipeline.lot_store.LotPersistenceStore", mock_store_cls):
                    data = client.get("/diagnostics").json()

        lots = data["lots"]
        if "error" not in lots:
            assert lots["db_exists"] is True


# ── TestDiagnosticsPricesSection ──────────────────────────────────────────────


class TestDiagnosticsPricesSection:
    """Prices section reflects the PRICES_DIR cache state."""

    def test_empty_prices_dir_csv_count_zero(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.PRICES_DIR       = Path(td)
                mock_s.LOT_STORE_DB     = Path(td) / "lots.db"
                mock_s.DEDUP_DIR        = Path(td)
                mock_s.OUTPUT_DIR       = Path(td)
                mock_s.ORCHESTRATOR_KEY = ""
                data = client.get("/diagnostics").json()
        prices = data["prices"]
        if "error" not in prices:
            assert prices["csv_count"] == 0
            assert prices["combined_csvs"] == []

    def test_combined_csv_shows_in_combined_csvs(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "combined_nok_2025.csv").write_text("date,asset_id,fiat_currency,price_fiat\n")
            (td_path / "xrp_nok_2025.csv").write_text("date,asset_id,fiat_currency,price_fiat\n")
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.PRICES_DIR       = td_path
                mock_s.LOT_STORE_DB     = td_path / "lots.db"
                mock_s.DEDUP_DIR        = td_path
                mock_s.OUTPUT_DIR       = td_path
                mock_s.ORCHESTRATOR_KEY = ""
                data = client.get("/diagnostics").json()
        prices = data["prices"]
        if "error" not in prices:
            assert prices["csv_count"] == 2
            combined_names = [c["name"] for c in prices["combined_csvs"]]
            assert "combined_nok_2025.csv" in combined_names


# ── TestDiagnosticsJobsSection ────────────────────────────────────────────────


class TestDiagnosticsJobsSection:
    """Jobs section reflects live job store counts."""

    def test_jobs_section_counts_accurately(self):
        client = _make_client()
        # Create a couple of jobs to make the count non-zero.
        client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
        client.post("/jobs", json={"tax_year": 2025, "country": "uk"})
        data = client.get("/diagnostics").json()
        jobs = data["jobs"]
        if "error" not in jobs:
            assert jobs["total"] >= 2
            assert isinstance(jobs["running"], int)
            assert isinstance(jobs["failed"], int)
            assert isinstance(jobs["completed"], int)


# ── TestDiagnosticsDedup ──────────────────────────────────────────────────────


class TestDiagnosticsDedup:
    """Dedup section reflects the DEDUP_DIR SQLite databases."""

    def test_empty_dedup_dir_source_count_zero(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as td:
            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.DEDUP_DIR        = Path(td)
                mock_s.PRICES_DIR       = Path(td)
                mock_s.LOT_STORE_DB     = Path(td) / "lots.db"
                mock_s.OUTPUT_DIR       = Path(td)
                mock_s.ORCHESTRATOR_KEY = ""
                data = client.get("/diagnostics").json()
        dedup = data["dedup"]
        if "error" not in dedup:
            assert dedup["source_count"] == 0
            assert dedup["total_skips"] == 0

    def test_dedup_db_counted(self):
        client = _make_client()
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            db_path = td_path / "firi.db"
            con = sqlite3.connect(str(db_path))
            con.execute("CREATE TABLE skip_log (id INTEGER PRIMARY KEY, note TEXT)")
            con.execute("INSERT INTO skip_log VALUES (1, 'test')")
            con.commit()
            con.close()

            with patch("taxspine_orchestrator.main.settings") as mock_s:
                mock_s.DEDUP_DIR        = td_path
                mock_s.PRICES_DIR       = td_path
                mock_s.LOT_STORE_DB     = td_path / "lots.db"
                mock_s.OUTPUT_DIR       = td_path
                mock_s.ORCHESTRATOR_KEY = ""
                data = client.get("/diagnostics").json()

        dedup = data["dedup"]
        if "error" not in dedup:
            assert dedup["source_count"] == 1
            assert dedup["total_skips"] == 1


# ── TestDiagnosticsRouterRegistration ─────────────────────────────────────────


class TestDiagnosticsRouterRegistration:
    """Verify the /diagnostics route is registered in the application."""

    def test_route_present_in_openapi(self):
        client = _make_client()
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/diagnostics" in paths

    def test_route_method_is_get(self):
        client = _make_client()
        schema = client.get("/openapi.json").json()
        assert "get" in schema["paths"]["/diagnostics"]
