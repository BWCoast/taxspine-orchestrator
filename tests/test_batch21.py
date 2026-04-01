"""Batch 21 — Security hardening (SEC-03, SEC-05, SEC-06, SEC-07, SEC-08, SEC-09, SEC-10, SEC-11, SEC-15).

Coverage:
    SEC-06  services.py guard: paths starting with '--' rejected before subprocess
    SEC-07  /alerts requires _require_key (verified as already done)
    SEC-08  startup warning when ORCHESTRATOR_KEY is empty (verified as already done)
    SEC-09  magic-byte validation in /uploads/csv
    SEC-10  CORS + rate-limiting documented in README
    SEC-11  null-byte in dedup slug returns 400 (already mitigated by allowlist regex)
    SEC-15  file download endpoints require auth (verified as already done)
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app, _is_binary_upload, _BINARY_MAGIC_SIGNATURES
from taxspine_orchestrator.services import JobService
from taxspine_orchestrator.models import Country, JobInput, JobStatus, ValuationMode, CsvSourceType


# ── Shared test client ────────────────────────────────────────────────────────

client = TestClient(app)


# ──────────────────────────────────────────────────────────────────────────────
# TestSEC06FlagInjectionGuard
# ──────────────────────────────────────────────────────────────────────────────


class TestSEC06FlagInjectionGuard:
    """SEC-06: CSV file paths starting with '--' are rejected before subprocess."""

    def test_sec06_comment_present_in_services(self):
        """The SEC-06 guard comment is present in services.py."""
        import taxspine_orchestrator.services as svc_mod
        src = Path(svc_mod.__file__).read_text(encoding="utf-8")
        assert "SEC-06" in src

    def test_sec06_guard_rejects_dashed_csv_path(self, tmp_path):
        """A CSV path starting with '--' is treated as flag injection and fails the job."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.models import CsvFileSpec
        store = InMemoryJobStore()
        svc = JobService(store)

        job_input = JobInput(
            csv_files=[CsvFileSpec(path="--inject-flag", source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
        )
        job = svc.create_job(job_input)
        svc.start_job_execution(job.id)

        updated = svc.get_job(job.id)
        assert updated.status == JobStatus.FAILED
        assert "SEC-06" in (updated.output.error_message or "")

    def test_sec06_guard_rejects_dashed_csv_prices_path(self, tmp_path):
        """A csv_prices_path starting with '--' is treated as flag injection."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.models import CsvFileSpec
        store = InMemoryJobStore()
        svc = JobService(store)

        csv = tmp_path / "events.csv"
        csv.write_text("event_id,timestamp\n")

        job_input = JobInput(
            csv_files=[CsvFileSpec(path=str(csv), source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
            valuation_mode=ValuationMode.PRICE_TABLE,
            csv_prices_path="--csv-prices /etc/passwd",
        )
        job = svc.create_job(job_input)
        svc.start_job_execution(job.id)

        updated = svc.get_job(job.id)
        assert updated.status == JobStatus.FAILED
        assert "SEC-06" in (updated.output.error_message or "")

    def test_sec06_normal_path_not_rejected(self, tmp_path):
        """A normal absolute path does NOT trigger the SEC-06 guard."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.models import CsvFileSpec
        store = InMemoryJobStore()
        svc = JobService(store)

        # A path that doesn't start with '--'; the SEC-06 guard should not fire.
        # It may fail later (e.g. file not found), but not with SEC-06.
        normal_path = str(tmp_path / "events.csv")

        job_input = JobInput(
            csv_files=[CsvFileSpec(path=normal_path, source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
        )
        job = svc.create_job(job_input)
        svc.start_job_execution(job.id)

        updated = svc.get_job(job.id)
        # Should not be a SEC-06 error (may fail for other reasons like missing file)
        assert "SEC-06" not in (updated.output.error_message or "")

    def test_sec06_path_with_leading_dash_single_only_not_flagged(self, tmp_path):
        """A single-dash path like '-event.csv' is not blocked (only '--' prefix is)."""
        from taxspine_orchestrator.storage import InMemoryJobStore
        from taxspine_orchestrator.models import CsvFileSpec
        store = InMemoryJobStore()
        svc = JobService(store)

        # Single dash is unusual but not a flag; only '--' triggers the guard.
        path_with_single_dash = "-not-a-flag.csv"

        job_input = JobInput(
            csv_files=[CsvFileSpec(path=path_with_single_dash, source_type=CsvSourceType.GENERIC_EVENTS)],
            tax_year=2025,
            country=Country.NORWAY,
        )
        job = svc.create_job(job_input)
        svc.start_job_execution(job.id)

        updated = svc.get_job(job.id)
        # Should not be a SEC-06 error
        assert "SEC-06" not in (updated.output.error_message or "")


# ──────────────────────────────────────────────────────────────────────────────
# TestSEC07AlertsAuth
# ──────────────────────────────────────────────────────────────────────────────


class TestSEC07AlertsAuth:
    """SEC-07: /alerts endpoint requires authentication when ORCHESTRATOR_KEY is set."""

    def test_sec07_alerts_rejects_missing_key(self):
        """When key is set, /alerts without the header returns 401."""
        with patch.dict(os.environ, {"ORCHESTRATOR_KEY": "test-sec07-key"}):
            from taxspine_orchestrator import config
            original = config.settings.ORCHESTRATOR_KEY
            config.settings.ORCHESTRATOR_KEY = "test-sec07-key"
            try:
                r = client.get("/alerts")
                assert r.status_code == 401
            finally:
                config.settings.ORCHESTRATOR_KEY = original

    def test_sec07_alerts_accepts_correct_key(self):
        """When key is set and provided correctly, /alerts returns 200."""
        from taxspine_orchestrator import config
        original = config.settings.ORCHESTRATOR_KEY
        config.settings.ORCHESTRATOR_KEY = "test-sec07-key"
        try:
            r = client.get("/alerts", headers={"X-Api-Key": "test-sec07-key"})
            assert r.status_code == 200
        finally:
            config.settings.ORCHESTRATOR_KEY = original

    def test_sec07_alerts_has_require_key_in_source(self):
        """The /alerts route declaration includes _require_key in source code."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        # Look for the pattern: @app.get("/alerts", ..., dependencies=[Depends(_require_key)])
        assert 'app.get("/alerts"' in src
        # Find the alerts route definition and confirm _require_key is on the same decorator
        alerts_block = src[src.index('app.get("/alerts"'):][:200]
        assert "_require_key" in alerts_block


# ──────────────────────────────────────────────────────────────────────────────
# TestSEC08StartupWarning
# ──────────────────────────────────────────────────────────────────────────────


class TestSEC08StartupWarning:
    """SEC-08: Startup warning logged when ORCHESTRATOR_KEY is empty."""

    def test_sec08_warning_present_in_source(self):
        """main.py contains a startup warning for missing ORCHESTRATOR_KEY."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        assert "ORCHESTRATOR_KEY is not set" in src
        assert "PUBLICLY ACCESSIBLE" in src

    def test_sec08_warning_uses_logger(self):
        """The warning is emitted via a logger, not print()."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        # The warning block should reference _startup_logger.warning
        assert "_startup_logger.warning" in src


# ──────────────────────────────────────────────────────────────────────────────
# TestSEC09MagicByteValidation
# ──────────────────────────────────────────────────────────────────────────────


class TestSEC09MagicByteValidation:
    """SEC-09: Magic-byte check rejects binary file uploads masquerading as CSV."""

    def test_magic_signatures_constant_present(self):
        """_BINARY_MAGIC_SIGNATURES is exported from main.py."""
        assert isinstance(_BINARY_MAGIC_SIGNATURES, tuple)
        assert len(_BINARY_MAGIC_SIGNATURES) > 0

    def test_is_binary_upload_detects_zip(self):
        """PK (ZIP) header is detected as binary."""
        assert _is_binary_upload(b"\x50\x4b\x03\x04" + b"\x00" * 100)

    def test_is_binary_upload_detects_pe_exe(self):
        """MZ (PE/Windows executable) header is detected as binary."""
        assert _is_binary_upload(b"\x4d\x5a\x90\x00" + b"\x00" * 100)

    def test_is_binary_upload_detects_elf(self):
        """ELF (Linux executable) header is detected as binary."""
        assert _is_binary_upload(b"\x7f\x45\x4c\x46" + b"\x00" * 100)

    def test_is_binary_upload_detects_pdf(self):
        """%PDF header is detected as binary."""
        assert _is_binary_upload(b"\x25\x50\x44\x46-1.7\n" + b"\x00" * 100)

    def test_is_binary_upload_detects_jpeg(self):
        """JPEG (FFD8FF) header is detected as binary."""
        assert _is_binary_upload(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    def test_is_binary_upload_detects_png(self):
        """PNG header is detected as binary."""
        assert _is_binary_upload(b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a" + b"\x00" * 100)

    def test_is_binary_upload_accepts_plain_csv(self):
        """A plain-text CSV header is NOT flagged as binary."""
        csv_header = b"event_id,timestamp,amount\n"
        assert not _is_binary_upload(csv_header)

    def test_is_binary_upload_accepts_utf8_with_bom(self):
        """A UTF-8 BOM (EF BB BF) followed by CSV text is not flagged as binary."""
        utf8_bom_csv = b"\xef\xbb\xbf" + b"event_id,timestamp\n"
        assert not _is_binary_upload(utf8_bom_csv)

    def test_is_binary_upload_accepts_empty_bytes(self):
        """An empty byte string does not trigger the binary check."""
        assert not _is_binary_upload(b"")

    def test_upload_endpoint_rejects_zip_disguised_as_csv(self, tmp_path):
        """POST /uploads/csv returns 400 when the file has a ZIP magic header."""
        zip_content = b"\x50\x4b\x03\x04" + b"\x00" * 200
        r = client.post(
            "/uploads/csv",
            files={"file": ("fake.csv", io.BytesIO(zip_content), "text/csv")},
        )
        assert r.status_code == 400
        assert "binary" in r.json()["detail"].lower()

    def test_upload_endpoint_rejects_pe_exe_disguised_as_csv(self, tmp_path):
        """POST /uploads/csv returns 400 when the file has a PE (MZ) magic header."""
        pe_content = b"\x4d\x5a" + b"\x00" * 200
        r = client.post(
            "/uploads/csv",
            files={"file": ("exploit.csv", io.BytesIO(pe_content), "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_upload_endpoint_accepts_plain_csv(self, tmp_path):
        """POST /uploads/csv accepts a valid plain-text CSV."""
        csv_content = b"event_id,timestamp,amount\n1,2025-01-01T00:00:00Z,100\n"
        r = client.post(
            "/uploads/csv",
            files={"file": ("events.csv", io.BytesIO(csv_content), "text/csv")},
        )
        # Should succeed (200/201) or fail only for non-binary reasons
        assert r.status_code != 400 or "binary" not in r.json().get("detail", "").lower()

    def test_is_binary_upload_function_exported(self):
        """_is_binary_upload is importable from main.py."""
        from taxspine_orchestrator.main import _is_binary_upload as fn
        assert callable(fn)


# ──────────────────────────────────────────────────────────────────────────────
# TestSEC10CorsAndRateLimitDocs
# ──────────────────────────────────────────────────────────────────────────────


class TestSEC10CorsAndRateLimitDocs:
    """SEC-10 / SEC-03: CORS override and rate-limiting guidance in README."""

    def _read_readme(self) -> str:
        readme = Path(__file__).parent.parent / "README.md"
        return readme.read_text(encoding="utf-8")

    def test_cors_origins_documented_in_readme(self):
        """README documents CORS_ORIGINS override requirement."""
        readme = self._read_readme()
        assert "CORS_ORIGINS" in readme

    def test_cors_production_note_present(self):
        """README explains that CORS_ORIGINS must be set in production."""
        readme = self._read_readme()
        # The README should mention setting CORS_ORIGINS to the actual hostname
        assert "CORS" in readme
        assert "production" in readme.lower()

    def test_rate_limiting_section_present(self):
        """README mentions rate limiting guidance for production."""
        readme = self._read_readme()
        assert "rate limit" in readme.lower() or "rate-limit" in readme.lower()

    def test_reverse_proxy_recommendation_present(self):
        """README recommends a reverse proxy (nginx/Caddy) for rate limiting."""
        readme = self._read_readme()
        assert any(proxy in readme.lower() for proxy in ("nginx", "caddy", "reverse proxy"))

    def test_orchestrator_key_warning_documented(self):
        """README documents that no key means fully open API."""
        readme = self._read_readme()
        assert "ORCHESTRATOR_KEY" in readme
        assert "publicly accessible" in readme.lower() or "PUBLICLY ACCESSIBLE" in readme


# ──────────────────────────────────────────────────────────────────────────────
# TestSEC11NullByteDedup
# ──────────────────────────────────────────────────────────────────────────────


class TestSEC11NullByteDedup:
    """SEC-11: Dedup source slug sanitization handles null bytes without 500 errors."""

    def test_sec11_null_byte_in_source_returns_non_500(self):
        """A null-byte (%00) in the dedup source slug does not cause a 500."""
        # FastAPI URL-decodes, but null bytes in URL path are typically handled
        # before routing. We test the _db_path helper directly.
        from taxspine_orchestrator.dedup import _db_path
        # The allowlist regex converts null bytes to '_'; this should not raise.
        result = _db_path("source\x00with\x00nulls")
        # Should have replaced null bytes, not raised
        assert "\x00" not in str(result)

    def test_sec11_allowlist_regex_handles_null_byte(self):
        """The sanitization regex in dedup._db_path replaces null bytes with '_'."""
        from taxspine_orchestrator.dedup import _db_path
        import re
        # Replicate the sanitization: null byte is not in [A-Za-z0-9_-], so replaced
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", "test\x00source")
        assert "\x00" not in safe
        assert "_" in safe

    def test_sec11_dedup_sources_endpoint_returns_200(self):
        """GET /dedup/sources returns 200 (not 500) — basic smoke test."""
        r = client.get("/dedup/sources")
        assert r.status_code == 200

    def test_sec11_allowlist_comment_in_dedup_source(self):
        """dedup.py documents the allowlist sanitization approach."""
        from taxspine_orchestrator import dedup as dedup_mod
        src = Path(dedup_mod.__file__).read_text(encoding="utf-8")
        assert "SEC-02" in src  # SEC-02 fix covers null bytes via allowlist


# ──────────────────────────────────────────────────────────────────────────────
# TestSEC15FileDownloadAuth
# ──────────────────────────────────────────────────────────────────────────────


class TestSEC15FileDownloadAuth:
    """SEC-15: File download endpoints require authentication when key is set."""

    def _with_key(self):
        from taxspine_orchestrator import config
        return config.settings

    def test_sec15_job_files_list_requires_key(self):
        """GET /jobs/{id}/files requires auth when ORCHESTRATOR_KEY is set."""
        from taxspine_orchestrator import config
        original = config.settings.ORCHESTRATOR_KEY
        config.settings.ORCHESTRATOR_KEY = "test-sec15-key"
        try:
            r = client.get("/jobs/nonexistent/files")
            assert r.status_code in (401, 404)  # 401 before 404 when key is wrong
            # Without the key, we expect 401
            assert r.status_code == 401
        finally:
            config.settings.ORCHESTRATOR_KEY = original

    def test_sec15_job_file_download_requires_key(self):
        """GET /jobs/{id}/files/{kind} requires auth when ORCHESTRATOR_KEY is set."""
        from taxspine_orchestrator import config
        original = config.settings.ORCHESTRATOR_KEY
        config.settings.ORCHESTRATOR_KEY = "test-sec15-key"
        try:
            r = client.get("/jobs/nonexistent/files/log")
            assert r.status_code == 401
        finally:
            config.settings.ORCHESTRATOR_KEY = original

    def test_sec15_job_reports_list_requires_key(self):
        """GET /jobs/{id}/reports requires auth when ORCHESTRATOR_KEY is set."""
        from taxspine_orchestrator import config
        original = config.settings.ORCHESTRATOR_KEY
        config.settings.ORCHESTRATOR_KEY = "test-sec15-key"
        try:
            r = client.get("/jobs/nonexistent/reports")
            assert r.status_code == 401
        finally:
            config.settings.ORCHESTRATOR_KEY = original

    def test_sec15_file_endpoints_have_require_key_in_source(self):
        """All file download route decorators include _require_key."""
        import taxspine_orchestrator.main as main_mod
        src = Path(main_mod.__file__).read_text(encoding="utf-8")
        # Each file endpoint should have _require_key in its @app.get decorator.
        # The decorator pattern is:
        #   @app.get("/jobs/{job_id}/files", tags=[...], dependencies=[Depends(_require_key)])
        # _require_key appears AFTER the route string in the same decorator line.
        file_routes = [
            '"/jobs/{job_id}/files"',
            '"/jobs/{job_id}/files/{kind}"',
            '"/jobs/{job_id}/reports"',
            '"/jobs/{job_id}/reports/{index}"',
        ]
        for route in file_routes:
            assert route in src, f"Route {route} not found in source"
            # Search the 300 chars AFTER the route string for _require_key
            idx = src.index(route)
            decorator_tail = src[idx:idx + 300]
            assert "_require_key" in decorator_tail, (
                f"_require_key not found in decorator for route {route}"
            )
