"""Batch 20 — Tax Law correctness tests.

Findings covered
----------------
TL-11  Non-GENERIC_EVENTS CSVs in mixed XRPL+CSV jobs must be rejected (label added)
TL-07  Price-table coverage warning missing at execution time
TL-10  RLUSD and missing-asset detection happens only at fetch time (same fix as TL-07)
TL-09  Dummy engine applies NOK amounts to UK pipeline without currency label — warn
TL-15  _fill_calendar_gaps must seed last_rate from first available rate (early-Jan gap)
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── shared helpers ────────────────────────────────────────────────────────────

_HERE     = Path(__file__).parent
_REPO     = _HERE.parent
_SERVICES = _REPO / "taxspine_orchestrator" / "services.py"
_PRICES   = _REPO / "taxspine_orchestrator" / "prices.py"


def _svc() -> str:
    return _SERVICES.read_text(encoding="utf-8")


def _prices_src() -> str:
    return _PRICES.read_text(encoding="utf-8")


# ── TL-11: mixed XRPL+CSV rejection label ────────────────────────────────────


class TestTL11MixedJobLabel:
    """TL-11: the mixed-job rejection guard must carry a TL-11 label so it is
    traceable to the audit finding, and the error message must identify the
    unsupported source type."""

    def test_tl11_comment_in_services(self):
        """services.py must have a TL-11 annotation on the mixed-job guard."""
        src = _svc()
        assert "TL-11" in src, (
            "TL-11: services.py must contain a TL-11 comment labelling the "
            "mixed XRPL+CSV rejection guard"
        )

    def test_tl11_error_message_in_services(self):
        """The error raised by the guard must include 'TL-11' for traceability."""
        src = _svc()
        # Locate the error string that is returned when unsupported specs found
        idx = src.find("TL-11: Mixed XRPL+CSV")
        assert idx >= 0, (
            "TL-11: error message must be prefixed 'TL-11: Mixed XRPL+CSV jobs …'"
        )

    def test_coinbase_csv_rejected_in_xrpl_job(self, tmp_path, monkeypatch):
        """A job with XRPL account + COINBASE_CSV must fail with a TL-11 error."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        # Create a dummy CSV file so the CSV-existence check passes
        csv_file = tmp_path / "uploads" / "coinbase.csv"
        csv_file.write_text("header\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "norway",
                "tax_year": 2025,
                "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
                "csv_files": [{"path": str(csv_file), "source_type": "coinbase_csv"}],
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]

            c.post(f"/jobs/{job_id}/start")

            # Wait for job to finish (it should fail immediately)
            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            assert job["status"] == "failed", (
                f"TL-11: mixed XRPL+COINBASE_CSV job must fail; got status={job['status']!r}"
            )
            err = job["output"].get("error_message", "")
            assert "TL-11" in err, (
                f"TL-11: error message must contain 'TL-11'; got {err!r}"
            )
            assert "coinbase_csv" in err.lower() or "coinbase" in err.lower(), (
                f"TL-11: error must name the offending source type; got {err!r}"
            )

    def test_generic_events_csv_accepted_in_xrpl_job(self, tmp_path, monkeypatch):
        """GENERIC_EVENTS CSV with an XRPL account must NOT be rejected by TL-11."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        csv_file = tmp_path / "uploads" / "events.csv"
        csv_file.write_text("header\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "norway",
                "tax_year": 2025,
                "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "dry_run": True,
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]
            c.post(f"/jobs/{job_id}/start")

            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            err = job["output"].get("error_message", "") or ""
            assert "TL-11" not in err, (
                "TL-11: GENERIC_EVENTS CSV must not trigger the TL-11 rejection; "
                f"got error_message={err!r}"
            )

    def test_csv_only_non_generic_not_rejected(self, tmp_path, monkeypatch):
        """Non-GENERIC_EVENTS CSV in a CSV-only job (no XRPL) must not be rejected."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        csv_file = tmp_path / "uploads" / "coinbase.csv"
        csv_file.write_text("header\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "norway",
                "tax_year": 2025,
                "xrpl_accounts": [],
                "csv_files": [{"path": str(csv_file), "source_type": "coinbase_csv"}],
                "dry_run": True,
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]
            c.post(f"/jobs/{job_id}/start")

            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            err = job["output"].get("error_message", "") or ""
            assert "TL-11" not in err, (
                "TL-11: CSV-only non-generic job must not be rejected; "
                f"got error_message={err!r}"
            )


# ── TL-07 / TL-10: price table coverage warning ───────────────────────────────


class TestTL07PriceTableCoverageWarning:
    """TL-07/TL-10: when valuation_mode=price_table, the execution log must
    warn that asset coverage is not verified and that absent-asset transactions
    produce zero-value (UNRESOLVED) lots."""

    def test_tl07_comment_in_services(self):
        """services.py must have a TL-07 annotation on the coverage warning."""
        src = _svc()
        assert "TL-07" in src, (
            "TL-07: services.py must contain a TL-07 comment on the price table "
            "coverage warning"
        )

    def test_tl10_comment_in_services(self):
        """services.py must also reference TL-10 on the same warning block."""
        src = _svc()
        assert "TL-10" in src, (
            "TL-10: services.py must reference TL-10 alongside TL-07 on the "
            "coverage-warning block"
        )

    def test_rlusd_mentioned_in_coverage_warning(self):
        """The coverage warning in services.py must mention RLUSD as a known gap."""
        src = _svc()
        idx = src.find("TL-07")
        assert idx >= 0
        window = src[idx:idx + 600]
        assert "RLUSD" in window, (
            "TL-07: the coverage warning block must mention RLUSD as a known "
            "unsupported asset"
        )

    def test_price_table_job_log_contains_coverage_warning(self, tmp_path, monkeypatch):
        """A dry_run job with valuation_mode=price_table must log the TL-07 warning."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        # Create a dummy CSV price table
        price_csv = tmp_path / "prices.csv"
        price_csv.write_text("date,BTC\n2025-01-01,1000000\n", encoding="utf-8")
        csv_file = tmp_path / "uploads" / "events.csv"
        csv_file.write_text("header\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "norway",
                "tax_year": 2025,
                "xrpl_accounts": [],
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "valuation_mode": "price_table",
                "csv_prices_path": str(price_csv),
                "dry_run": True,
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]
            c.post(f"/jobs/{job_id}/start")

            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            log_path = job["output"].get("log_path")
            assert log_path, "expected a log_path in job output"
            log_content = Path(log_path).read_text(encoding="utf-8")
            assert "TL-07" in log_content, (
                "TL-07: execution log must contain the coverage warning when "
                f"valuation_mode=price_table; log was:\n{log_content}"
            )
            assert "UNRESOLVED" in log_content, (
                "TL-07: execution log must mention UNRESOLVED valuations in coverage warning"
            )

    def test_dummy_mode_no_coverage_warning(self, tmp_path, monkeypatch):
        """A dry_run job with valuation_mode=dummy must NOT log the TL-07 warning."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        csv_file = tmp_path / "uploads" / "events.csv"
        csv_file.write_text("header\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "norway",
                "tax_year": 2025,
                "xrpl_accounts": [],
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "valuation_mode": "dummy",
                "dry_run": True,
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]
            c.post(f"/jobs/{job_id}/start")

            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            log_path = job["output"].get("log_path")
            if log_path and Path(log_path).exists():
                log_content = Path(log_path).read_text(encoding="utf-8")
                assert "WARNING TL-07" not in log_content, (
                    "TL-07: dummy mode must not log the price-table coverage warning"
                )


# ── TL-09: UK + dummy valuation currency mismatch warning ─────────────────────


class TestTL09UkDummyWarning:
    """TL-09: a UK job with valuation_mode=dummy must log a warning that the
    DummyValuationEngine returns NOK values, not GBP, so the operator cannot
    accidentally file the output with HMRC."""

    def test_tl09_comment_in_services(self):
        """services.py must have a TL-09 annotation on the UK+dummy warning."""
        src = _svc()
        assert "TL-09" in src, (
            "TL-09: services.py must contain a TL-09 comment on the UK+dummy "
            "currency mismatch warning"
        )

    def test_uk_dummy_job_log_contains_tl09_warning(self, tmp_path, monkeypatch):
        """A dry_run UK job with valuation_mode=dummy must log the TL-09 warning."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        csv_file = tmp_path / "uploads" / "events.csv"
        csv_file.write_text("header\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "uk",
                "tax_year": 2025,
                "xrpl_accounts": [],
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "valuation_mode": "dummy",
                "dry_run": True,
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]
            c.post(f"/jobs/{job_id}/start")

            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            log_path = job["output"].get("log_path")
            assert log_path, "expected a log_path in job output"
            log_content = Path(log_path).read_text(encoding="utf-8")
            assert "TL-09" in log_content, (
                "TL-09: UK+dummy execution log must contain 'TL-09'; "
                f"log was:\n{log_content}"
            )
            assert "NOK" in log_content, (
                "TL-09: warning must mention NOK (wrong currency for UK filing)"
            )

    def test_norway_dummy_no_tl09_warning(self, tmp_path, monkeypatch):
        """A Norway job with dummy valuation must NOT log the TL-09 warning."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        csv_file = tmp_path / "uploads" / "events.csv"
        csv_file.write_text("header\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "norway",
                "tax_year": 2025,
                "xrpl_accounts": [],
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "valuation_mode": "dummy",
                "dry_run": True,
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]
            c.post(f"/jobs/{job_id}/start")

            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            log_path = job["output"].get("log_path")
            if log_path and Path(log_path).exists():
                log_content = Path(log_path).read_text(encoding="utf-8")
                assert "TL-09" not in log_content, (
                    "TL-09: Norway dummy job must not log the TL-09 UK-specific warning"
                )

    def test_uk_price_table_no_tl09_warning(self, tmp_path, monkeypatch):
        """A UK job with valuation_mode=price_table must NOT log the TL-09 warning."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        for d in ("output", "uploads", "data", "tmp"):
            (tmp_path / d).mkdir()

        csv_file = tmp_path / "uploads" / "events.csv"
        csv_file.write_text("header\n", encoding="utf-8")
        price_csv = tmp_path / "prices.csv"
        price_csv.write_text("date,BTC\n2025-01-01,80000\n", encoding="utf-8")

        with TestClient(app) as c:
            resp = c.post("/jobs", json={
                "country": "uk",
                "tax_year": 2025,
                "xrpl_accounts": [],
                "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                "valuation_mode": "price_table",
                "csv_prices_path": str(price_csv),
                "dry_run": True,
            })
            assert resp.status_code == 201
            job_id = resp.json()["id"]
            c.post(f"/jobs/{job_id}/start")

            for _ in range(40):
                status = c.get(f"/jobs/{job_id}").json()["status"]
                if status in ("failed", "completed", "cancelled"):
                    break
                time.sleep(0.05)

            job = c.get(f"/jobs/{job_id}").json()
            log_path = job["output"].get("log_path")
            if log_path and Path(log_path).exists():
                log_content = Path(log_path).read_text(encoding="utf-8")
                assert "TL-09" not in log_content, (
                    "TL-09: UK price_table job must not log the TL-09 warning "
                    "(only triggered for dummy mode)"
                )

    def test_tl09_warning_mentions_hmrc(self):
        """The TL-09 warning text in services.py must mention HMRC."""
        src = _svc()
        idx = src.find("TL-09")
        assert idx >= 0
        window = src[idx:idx + 400]
        assert "HMRC" in window, (
            "TL-09: the warning text must mention HMRC so operators recognise "
            "the filing context"
        )


# ── TL-15: _fill_calendar_gaps early-January seeding ─────────────────────────


class TestTL15FxGapSeeding:
    """TL-15: _fill_calendar_gaps must seed last_rate from the earliest available
    rate so that days before the first Norges Bank publication (e.g. Jan 1–2 when
    the first business day is Jan 3) are not left blank."""

    @pytest.fixture(autouse=True)
    def _import_fn(self):
        from taxspine_orchestrator.prices import _fill_calendar_gaps
        self._fill = _fill_calendar_gaps

    def test_tl15_comment_in_prices(self):
        """prices.py must have a TL-15 annotation on the seeding change."""
        src = _prices_src()
        assert "TL-15" in src, (
            "TL-15: prices.py must contain a TL-15 comment on the seeding fix"
        )

    def test_empty_rates_returns_empty(self):
        """_fill_calendar_gaps with no rates must return an empty dict (no crash)."""
        result = self._fill({}, 2025)
        assert result == {}, f"TL-15: empty rates must yield {{}}; got {result}"

    def test_jan1_seeded_when_first_rate_is_jan3(self):
        """When the first available rate is 2025-01-03, Jan 1 must get that rate."""
        rates = {
            "2025-01-03": Decimal("11.5000"),
            "2025-01-06": Decimal("11.6000"),
        }
        filled = self._fill(rates, 2025)
        assert "2025-01-01" in filled, (
            "TL-15: 2025-01-01 must be present when first available rate is Jan 3"
        )
        assert filled["2025-01-01"] == Decimal("11.5000"), (
            f"TL-15: Jan 1 must carry the Jan-3 seed; got {filled['2025-01-01']!r}"
        )

    def test_jan2_seeded_same_as_jan1(self):
        """January 2 must receive the same seed as January 1."""
        rates = {"2025-01-03": Decimal("11.5000")}
        filled = self._fill(rates, 2025)
        assert filled.get("2025-01-02") == Decimal("11.5000"), (
            "TL-15: Jan 2 must also carry the Jan-3 seed rate"
        )

    def test_actual_rate_used_from_publication_day(self):
        """From the first publication date onward the actual rate must be used."""
        rates = {
            "2025-01-03": Decimal("11.5000"),
            "2025-01-06": Decimal("11.6000"),
        }
        filled = self._fill(rates, 2025)
        # Jan 3 should have exactly the Jan-3 rate (not the seed from a prior day)
        assert filled["2025-01-03"] == Decimal("11.5000"), (
            "TL-15: Jan 3 must use the Jan-3 rate"
        )
        # Jan 6 should have the updated rate
        assert filled["2025-01-06"] == Decimal("11.6000"), (
            "TL-15: Jan 6 must use the Jan-6 rate"
        )
        # Jan 4 and 5 (weekend) must carry Jan-3 forward
        assert filled["2025-01-04"] == Decimal("11.5000"), (
            "TL-15: Jan 4 (Sat) must carry Jan-3 forward"
        )
        assert filled["2025-01-05"] == Decimal("11.5000"), (
            "TL-15: Jan 5 (Sun) must carry Jan-3 forward"
        )

    def test_full_year_coverage(self):
        """With at least one rate, all 365 days of the year must be covered."""
        rates = {"2025-01-03": Decimal("11.0000")}
        filled = self._fill(rates, 2025)
        # 2025 is not a leap year
        assert len(filled) == 365, (
            f"TL-15: filled dict must cover all 365 days; got {len(filled)}"
        )
        # Every entry must have a non-None Decimal value
        for date_str, val in filled.items():
            assert isinstance(val, Decimal), (
                f"TL-15: all filled values must be Decimal; {date_str} → {val!r}"
            )

    def test_year_end_gap_filled(self):
        """Rates published mid-December must carry forward to Dec 31."""
        rates = {"2025-12-15": Decimal("12.0000")}
        filled = self._fill(rates, 2025)
        assert filled.get("2025-12-31") == Decimal("12.0000"), (
            "TL-15: Dec 31 must carry the Dec-15 rate forward"
        )
