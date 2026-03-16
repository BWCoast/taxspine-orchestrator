"""Tests for Batch 10 MEDIUM security findings.

SEC-01 — LIKE wildcard injection in SqliteJobStore.list()
SEC-02 — Path traversal via source slugs in _db_path() and _dedup_store_path()
SEC-16 — /health and /alerts must not expose raw exception text to callers
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_job(client, case_name: str = "test") -> dict:
    resp = client.post(
        "/jobs",
        json={"tax_year": 2025, "country": "norway", "case_name": case_name},
    )
    assert resp.status_code == 201, resp.json()
    return resp.json()


# ── SEC-01: LIKE wildcard injection ───────────────────────────────────────────


class TestSec01LikeWildcardInjection:
    """SqliteJobStore.list() must escape % and _ before wrapping in LIKE wildcards."""

    def test_percent_query_does_not_match_all_jobs(self, client: TestClient) -> None:
        """query='%' is a LIKE wildcard — it must be escaped so it matches '%' literally."""
        _create_job(client, case_name="norway_2025")
        _create_job(client, case_name="uk_2025")

        # A literal '%' should match no job whose case_name contains a percent sign.
        resp = client.get("/jobs", params={"query": "%"})
        assert resp.status_code == 200
        # Both jobs have names without a literal '%', so neither should be returned.
        jobs = resp.json()
        for j in jobs:
            assert "%" in (j.get("input") or {}).get("case_name", ""), (
                "query='%' must match only case names containing a literal percent sign, "
                f"but matched job with case_name={j}"
            )

    def test_underscore_query_does_not_match_all_jobs(self, client: TestClient) -> None:
        """query='_' is a LIKE single-char wildcard — must be escaped to match literally."""
        _create_job(client, case_name="alpha")
        _create_job(client, case_name="beta")

        resp = client.get("/jobs", params={"query": "_"})
        assert resp.status_code == 200
        jobs = resp.json()
        for j in jobs:
            cn = (j.get("input") or {}).get("case_name", "")
            assert "_" in cn, (
                f"query='_' matched job with case_name={cn!r} that has no underscore"
            )

    def test_literal_search_still_works(self, client: TestClient) -> None:
        """Normal substring search must still function after escaping."""
        _create_job(client, case_name="norway_2025")
        _create_job(client, case_name="uk_report")

        resp = client.get("/jobs", params={"query": "norway"})
        assert resp.status_code == 200
        jobs = resp.json()
        assert len(jobs) >= 1
        for j in jobs:
            cn = (j.get("input") or {}).get("case_name", "")
            assert "norway" in cn.lower(), (
                f"query='norway' returned unexpected job with case_name={cn!r}"
            )

    def test_escape_clause_in_storage_source(self) -> None:
        """The storage.py source must contain ESCAPE to handle LIKE metacharacters."""
        storage_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "storage.py"
        src = storage_py.read_text(encoding="utf-8")
        assert "ESCAPE" in src, (
            "storage.py must use ESCAPE in the LIKE clause to handle % and _ "
            "metacharacters (SEC-01)"
        )

    def test_escape_replaces_percent_in_query(self) -> None:
        """Unit: the escaped query must replace % with \\%."""
        storage_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "storage.py"
        src = storage_py.read_text(encoding="utf-8")
        assert r"replace('%'" in src or r'replace("%"' in src or r"\\%" in src, (
            "storage.py must escape the '%' LIKE metacharacter before building the param"
        )


# ── SEC-02: Source slug path traversal ────────────────────────────────────────


class TestSec02SourceSlugSanitisation:
    """Source slugs must be allowlisted to [A-Za-z0-9_-] only."""

    def test_dedup_db_path_strips_dotdot(self) -> None:
        from taxspine_orchestrator.dedup import _db_path
        # '../etc/passwd' after sanitisation must become '__etc_passwd.db' inside DEDUP_DIR.
        result = _db_path("../etc/passwd")
        assert ".." not in str(result), (
            f"_db_path must strip '..' from slugs; got {result}"
        )
        from taxspine_orchestrator.config import settings
        assert str(result).startswith(str(settings.DEDUP_DIR)), (
            f"_db_path result must be inside DEDUP_DIR; got {result}"
        )

    def test_dedup_db_path_strips_forward_slash(self) -> None:
        from taxspine_orchestrator.dedup import _db_path
        result = _db_path("foo/bar")
        assert "/" not in result.name, f"Slash not stripped from filename: {result}"

    def test_dedup_db_path_strips_backslash(self) -> None:
        from taxspine_orchestrator.dedup import _db_path
        result = _db_path("foo\\bar")
        assert "\\" not in result.name, f"Backslash not stripped from filename: {result}"

    def test_dedup_db_path_allowlist_regex_used(self) -> None:
        """Verify the allowlist regex is present in dedup.py (static check)."""
        dedup_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "dedup.py"
        src = dedup_py.read_text(encoding="utf-8")
        assert "A-Za-z0-9_-" in src or "[^A-Za-z0-9" in src, (
            "dedup.py must use an allowlist regex [A-Za-z0-9_-] for slug sanitisation"
        )

    def test_dedup_db_path_containment_asserted(self) -> None:
        """Verify containment assertion is present in dedup.py (static check)."""
        dedup_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "dedup.py"
        src = dedup_py.read_text(encoding="utf-8")
        assert "relative_to" in src, (
            "dedup.py must assert the resolved path is relative_to DEDUP_DIR"
        )

    def test_services_dedup_store_path_allowlist_regex_used(self) -> None:
        """Verify the allowlist regex is present in services.py (static check)."""
        services_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "services.py"
        src = services_py.read_text(encoding="utf-8")
        # Look specifically in _dedup_store_path — use 1500 chars to cover the docstring.
        start = src.find("def _dedup_store_path(")
        assert start >= 0, "_dedup_store_path must exist in services.py"
        snippet = src[start: start + 1500]
        assert "A-Za-z0-9_-" in snippet or "[^A-Za-z0-9" in snippet, (
            "_dedup_store_path must use an allowlist regex for slug sanitisation"
        )

    def test_services_dedup_store_path_strips_dotdot(self, tmp_path: Path) -> None:
        from taxspine_orchestrator.services import JobService
        from taxspine_orchestrator.config import settings

        # Patch DEDUP_DIR to tmp_path so resolution is safe in tests.
        orig = settings.DEDUP_DIR
        settings.DEDUP_DIR = tmp_path
        try:
            result = JobService._dedup_store_path("../etc/passwd")
            assert ".." not in str(result), (
                f"_dedup_store_path must strip '..' from slugs; got {result}"
            )
            assert str(result).startswith(str(tmp_path)), (
                f"_dedup_store_path result must be inside DEDUP_DIR; got {result}"
            )
        finally:
            settings.DEDUP_DIR = orig

    def test_normal_slug_passes_through(self) -> None:
        from taxspine_orchestrator.dedup import _db_path
        result = _db_path("kraken_spot")
        assert result.name == "kraken_spot.db", (
            f"Normal slug must pass unchanged; got {result.name!r}"
        )

    def test_slug_with_dots_sanitised(self) -> None:
        from taxspine_orchestrator.dedup import _db_path
        # Dots are not in the allowlist, so they should be replaced.
        result = _db_path("foo.bar.baz")
        assert ".." not in result.name
        assert result.name.endswith(".db")


# ── SEC-16: Opaque health error text ─────────────────────────────────────────


class TestSec16OpaqueHealthErrors:
    """/health and /alerts must not leak raw exception text to callers."""

    def test_health_db_error_is_opaque(self, client: TestClient) -> None:
        """When DB ping fails, the response body must say 'error', not contain traceback text.

        /health response shape: {"status": "ok"/"degraded", "db": "...", "output_dir": "...", ...}
        "status" is an overall flag; individual check results are top-level keys.
        """
        from taxspine_orchestrator import main as _m

        with patch.object(_m._job_store, "ping", side_effect=RuntimeError("sqlite path: /secret/db")):
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        # "db" is a top-level key, not nested under "status".
        assert body["db"] == "error", (
            f"DB check must report 'error' when ping fails; got {body['db']!r}"
        )
        # The raw exception text must NOT appear in the response body.
        raw_body = resp.text
        assert "sqlite path" not in raw_body, (
            "Raw exception text must not appear in /health response body (SEC-16)"
        )
        assert "/secret" not in raw_body, (
            "Filesystem paths must not appear in /health response body (SEC-16)"
        )

    def test_health_output_dir_error_is_opaque(self, client: TestClient) -> None:
        """output_dir error must be reported as a simple 'error' string without detail text."""
        with patch("os.access", return_value=False):
            resp = client.get("/health")
        body = resp.json()
        # "output_dir" is a top-level key in the health response.
        out_status = body.get("output_dir", "")
        assert out_status == "error", (
            f"output_dir must report opaque 'error', got {out_status!r} (SEC-16)"
        )
        assert "not writable" not in out_status, (
            "output_dir error must not include detail text (SEC-16)"
        )

    def test_health_source_code_uses_opaque_error(self) -> None:
        """Static check: main.py must not use f'error: {exc}' in /health."""
        main_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        src = main_py.read_text(encoding="utf-8")
        # Find the health() function.
        health_start = src.find("async def health(")
        assert health_start >= 0
        # Read up to the next function definition.
        next_fn = src.find("\n@app.", health_start + 1)
        snippet = src[health_start: next_fn] if next_fn > 0 else src[health_start: health_start + 1000]
        assert 'f"error: {exc}"' not in snippet, (
            "/health must not embed raw exception text in its response (SEC-16)"
        )

    def test_alerts_db_error_is_opaque(self, client: TestClient) -> None:
        """When DB ping fails in /alerts, raw exception text must not appear in response."""
        from taxspine_orchestrator import main as _m

        with patch.object(_m._job_store, "ping", side_effect=RuntimeError("internal path: /var/lib/jobs.db")):
            resp = client.get("/alerts")

        assert resp.status_code == 200
        raw_body = resp.text
        assert "internal path" not in raw_body, (
            "Raw exception text must not appear in /alerts response body (SEC-16)"
        )
        assert "/var/lib" not in raw_body, (
            "Filesystem paths must not appear in /alerts response body (SEC-16)"
        )

    def test_alerts_source_code_uses_opaque_error(self) -> None:
        """Static check: main.py must not use f'error: {exc}' in /alerts."""
        main_py = Path(__file__).parent.parent / "taxspine_orchestrator" / "main.py"
        src = main_py.read_text(encoding="utf-8")
        alerts_start = src.find("async def get_alerts(")
        assert alerts_start >= 0
        next_fn = src.find("\n@app.", alerts_start + 1)
        snippet = src[alerts_start: next_fn] if next_fn > 0 else src[alerts_start: alerts_start + 2000]
        assert 'f"error: {exc}"' not in snippet, (
            "/alerts must not embed raw exception text in its response (SEC-16)"
        )
