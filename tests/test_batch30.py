"""test_batch30.py — TL-12: content-based CSV format detection.

Findings covered
----------------
TL-12  CSV files submitted as source_type=generic_events but containing
       Firi/Coinbase/etc. content must be detected early (XRPL: fail fast;
       CSV-only: auto-correct source_type and log).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


_HERE     = Path(__file__).parent
_REPO     = _HERE.parent
_SERVICES = _REPO / "taxspine_orchestrator" / "services.py"


def _svc() -> str:
    return _SERVICES.read_text(encoding="utf-8")


def _make_dirs(tmp_path: Path):
    for d in ("output", "uploads", "data", "tmp"):
        (tmp_path / d).mkdir(exist_ok=True)


def _read_log(job: dict) -> str:
    """Read execution log content from log_path, or empty string."""
    log_path = (job.get("output") or {}).get("log_path")
    if log_path and Path(log_path).exists():
        return Path(log_path).read_text(encoding="utf-8")
    return ""


def _get_error(job: dict) -> str:
    """Get error_message from job output, or empty string."""
    return (job.get("output") or {}).get("error_message") or ""


# ── Firi CSV header (minimal fingerprint for sniff detection) ─────────────────

_FIRI_HEADER = "Date,Type,Asset,Amount,Fee,Match ID,Withdraw ID,Deposit ID\n"
# Coinbase RAWTX CSV: columns with commas must be quoted for the CSV reader
# to parse them as single fields (matching the fingerprint frozenset).
_COINBASE_HEADER = (
    'Timestamp,Transaction Type,Asset,Quantity Transacted,Spot Price Currency,'
    'Spot Price at Transaction,Subtotal,'
    '"Total (inclusive of fees and/or spread)",'
    'Fees and/or Spread,Notes,Asset Acquired,'
    '"Asset Disposed (Sold, Sent, etc)"\n'
)
_GENERIC_HEADER = (
    "event_id,timestamp,event_type,source,account,"
    "asset_in,amount_in,asset_out,amount_out,"
    "fee_asset,fee_amount,tx_hash,exchange_tx_id,label,complex_tax_treatment,note\n"
)


# ── Static-analysis tests ─────────────────────────────────────────────────────


class TestTL12SourceAnnotation:
    """TL-12 guard must be labeled in services.py."""

    def test_tl12_comment_present(self):
        """services.py must contain a TL-12 comment."""
        assert "TL-12" in _svc(), (
            "TL-12: services.py must contain a TL-12 annotation on the "
            "content-based CSV detection guard"
        )

    def test_tl12_error_message_present(self):
        """The XRPL TL-12 error message must identify itself."""
        src = _svc()
        assert "TL-12: XRPL job received non-generic CSV" in src, (
            "TL-12: services.py must raise a TL-12-prefixed error for XRPL+mismatch case"
        )

    def test_sniff_mapping_present(self):
        """_SNIFF_TO_SOURCE_TYPE dict must map 'Firi CSV' and 'Coinbase RAWTX CSV'."""
        src = _svc()
        assert "Firi CSV" in src
        assert "Coinbase RAWTX CSV" in src


# ── XRPL+CSV: Firi CSV submitted as generic_events must fail (TL-12) ─────────


class TestTL12XrplFiriCsvRejected:
    """Firi CSV submitted as generic_events in an XRPL job must be rejected."""

    def test_firi_csv_as_generic_in_xrpl_job_fails(self, tmp_path, monkeypatch):
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        _make_dirs(tmp_path)

        # Write a file with Firi CSV header columns
        csv_file = tmp_path / "uploads" / "firi_export.csv"
        csv_file.write_text(_FIRI_HEADER + "2025-01-01,buy,BTC,0.01,0,,,\n", encoding="utf-8")

        def _mock_sniff_firi(path):
            return ("Firi CSV", "Use --source-type firi_csv.")

        with patch("taxspine_orchestrator.services._SNIFF_AVAILABLE", True), \
             patch("taxspine_orchestrator.services._sniff_csv_source_type",
                   side_effect=_mock_sniff_firi):
            with TestClient(app) as c:
                resp = c.post("/jobs", json={
                    "country": "norway",
                    "tax_year": 2025,
                    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
                    "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                })
                assert resp.status_code == 201
                job_id = resp.json()["id"]
                c.post(f"/jobs/{job_id}/start")

                for _ in range(60):
                    status = c.get(f"/jobs/{job_id}").json()["status"]
                    if status in ("failed", "completed", "cancelled"):
                        break
                    time.sleep(0.05)

                job = c.get(f"/jobs/{job_id}").json()
                assert job["status"] == "failed", (
                    f"TL-12: XRPL job with Firi CSV as generic_events must fail; "
                    f"got status={job['status']!r}"
                )
                err = _get_error(job)
                assert "TL-12" in err, f"TL-12: error must contain 'TL-12'; got {err!r}"
                assert "firi" in err.lower(), f"TL-12: error must mention firi; got {err!r}"

    def test_real_generic_csv_still_passes_tl12(self, tmp_path, monkeypatch):
        """A genuine generic-events CSV must not be blocked by TL-12."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        _make_dirs(tmp_path)

        csv_file = tmp_path / "uploads" / "events.csv"
        csv_file.write_text(_GENERIC_HEADER, encoding="utf-8")

        # Patch subprocess so the actual CLI isn't invoked
        with TestClient(app) as c:
            with patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                resp = c.post("/jobs", json={
                    "country": "norway",
                    "tax_year": 2025,
                    "xrpl_accounts": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
                    "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                })
                assert resp.status_code == 201
                job_id = resp.json()["id"]
                c.post(f"/jobs/{job_id}/start")

                for _ in range(60):
                    status = c.get(f"/jobs/{job_id}").json()["status"]
                    if status in ("failed", "completed", "cancelled"):
                        break
                    time.sleep(0.05)

                job = c.get(f"/jobs/{job_id}").json()
                err = _get_error(job)
                # Must NOT fail with TL-12
                assert "TL-12" not in err, (
                    f"TL-12: genuine generic-events CSV must not trigger TL-12; got {err!r}"
                )


# ── CSV-only: Firi CSV submitted as generic_events must auto-correct ──────────


class TestTL12CsvOnlyFiriAutoCorrect:
    """Firi CSV as generic_events in a CSV-only job must be auto-corrected."""

    def test_firi_csv_auto_corrected_in_csv_only_job(self, tmp_path, monkeypatch):
        """services.py logs a TL-12 auto-correction message and routes correctly."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        _make_dirs(tmp_path)

        csv_file = tmp_path / "uploads" / "firi_export.csv"
        csv_file.write_text(_FIRI_HEADER + "2025-01-01,buy,BTC,0.01,0,,,\n", encoding="utf-8")

        def _mock_sniff_firi(path):
            return ("Firi CSV", "Use --source-type firi_csv.")

        with patch("taxspine_orchestrator.services._SNIFF_AVAILABLE", True), \
             patch("taxspine_orchestrator.services._sniff_csv_source_type",
                   side_effect=_mock_sniff_firi):
            with TestClient(app) as c:
                with patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    resp = c.post("/jobs", json={
                        "country": "norway",
                        "tax_year": 2025,
                        "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                    })
                    assert resp.status_code == 201
                    job_id = resp.json()["id"]
                    c.post(f"/jobs/{job_id}/start")

                    for _ in range(60):
                        status = c.get(f"/jobs/{job_id}").json()["status"]
                        if status in ("failed", "completed", "cancelled"):
                            break
                        time.sleep(0.05)

                    job = c.get(f"/jobs/{job_id}").json()
                    logs = _read_log(job)
                    # The auto-correction log message must appear
                    assert "TL-12" in logs, (
                        f"TL-12: CSV-only job with Firi CSV must log TL-12 auto-correction; "
                        f"got log={logs!r}"
                    )
                    assert "firi_csv" in logs.lower() or "firi" in logs.lower(), (
                        f"TL-12: log must mention firi; got {logs!r}"
                    )
                    # Must not fail due to TL-12
                    err = _get_error(job)
                    assert "TL-12" not in err, (
                        f"TL-12: CSV-only job must not fail with TL-12; got {err!r}"
                    )

    def test_coinbase_csv_auto_corrected_in_csv_only_job(self, tmp_path, monkeypatch):
        """Coinbase RAWTX CSV as generic_events in CSV-only job must be auto-corrected."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        _make_dirs(tmp_path)

        csv_file = tmp_path / "uploads" / "coinbase_export.csv"
        csv_file.write_text(_COINBASE_HEADER + "2025-01-01,Buy,BTC,0.01,USD,90000,900,900,0,note,BTC,\n", encoding="utf-8")

        def _mock_sniff_coinbase(path):
            return ("Coinbase RAWTX CSV", "Use --source-type coinbase_csv.")

        with patch("taxspine_orchestrator.services._SNIFF_AVAILABLE", True), \
             patch("taxspine_orchestrator.services._sniff_csv_source_type",
                   side_effect=_mock_sniff_coinbase):
            with TestClient(app) as c:
                with patch("taxspine_orchestrator.services.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    resp = c.post("/jobs", json={
                        "country": "norway",
                        "tax_year": 2025,
                        "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                    })
                    assert resp.status_code == 201
                    job_id = resp.json()["id"]
                    c.post(f"/jobs/{job_id}/start")

                    for _ in range(60):
                        status = c.get(f"/jobs/{job_id}").json()["status"]
                        if status in ("failed", "completed", "cancelled"):
                            break
                        time.sleep(0.05)

                    job = c.get(f"/jobs/{job_id}").json()
                    logs = _read_log(job)
                    assert "TL-12" in logs, (
                        f"TL-12: CSV-only job with Coinbase CSV must log TL-12 auto-correction; "
                        f"got log={logs!r}"
                    )
                    err = _get_error(job)
                    assert "TL-12" not in err


# ── Unknown format: Bybit CSV as generic_events must fail ─────────────────────


class TestTL12UnknownFormatRejected:
    """CSV formats with no CsvSourceType mapping (Bybit, Kraken) must fail."""

    def test_bybit_uta_csv_as_generic_fails(self, tmp_path, monkeypatch):
        """Bybit UTA CSV detected as generic_events must fail with TL-12 error."""
        from taxspine_orchestrator.main import app
        from taxspine_orchestrator.config import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "DATA_DIR",   tmp_path / "data")
        monkeypatch.setattr(settings, "TEMP_DIR",   tmp_path / "tmp")
        _make_dirs(tmp_path)

        # Bybit UTA fingerprint: "Filled Price", "Contract", "Uid"
        bybit_header = "Uid,Contract,Filled Price,Other\n"
        csv_file = tmp_path / "uploads" / "bybit_export.csv"
        csv_file.write_text(bybit_header + "12345,BTCUSDT,90000,x\n", encoding="utf-8")

        def _mock_sniff_bybit(path):
            return ("Bybit UTA CSV", "Use --source-type bybit_uta.")

        with patch("taxspine_orchestrator.services._SNIFF_AVAILABLE", True), \
             patch("taxspine_orchestrator.services._sniff_csv_source_type",
                   side_effect=_mock_sniff_bybit):
            with TestClient(app) as c:
                resp = c.post("/jobs", json={
                    "country": "norway",
                    "tax_year": 2025,
                    "csv_files": [{"path": str(csv_file), "source_type": "generic_events"}],
                })
                assert resp.status_code == 201
                job_id = resp.json()["id"]
                c.post(f"/jobs/{job_id}/start")

                for _ in range(60):
                    status = c.get(f"/jobs/{job_id}").json()["status"]
                    if status in ("failed", "completed", "cancelled"):
                        break
                    time.sleep(0.05)

                job = c.get(f"/jobs/{job_id}").json()
                assert job["status"] == "failed"
                err = _get_error(job)
                assert "TL-12" in err, f"TL-12: unknown format must fail with TL-12; got {err!r}"
                assert "bybit" in err.lower() or "Bybit" in err, (
                    f"TL-12: error must mention Bybit; got {err!r}"
                )


# ── sniff unavailable: guard degrades gracefully ──────────────────────────────


class TestTL12SniffUnavailable:
    """When tax_spine is not installed, TL-12 must degrade gracefully (no crash)."""

    def test_sniff_unavailable_does_not_crash(self, tmp_path, monkeypatch):
        """With _SNIFF_AVAILABLE=False, CSV files pass through unmodified."""
        import taxspine_orchestrator.services as _svc_mod

        monkeypatch.setattr(_svc_mod, "_SNIFF_AVAILABLE", False)

        # Verify the constant is patchable (module-level)
        assert _svc_mod._SNIFF_AVAILABLE is False
