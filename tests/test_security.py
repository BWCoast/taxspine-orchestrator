"""Security hardening tests — P2-A.

Covers:
- X-Orchestrator-Key authentication header (Fix 1)
- OUTPUT_DIR containment check on the download endpoint (Fix 2)
- UPLOAD_DIR containment check on the workspace/csv endpoint (Fix 3)
- XRPL address format validation (Fix 4)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── Valid test addresses (pass _XRPL_ADDRESS_RE) ──────────────────────────────
_VALID_ADDR = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"  # 34-char genesis addr

_SAMPLE_JOB = {
    "xrpl_accounts": [_VALID_ADDR],
    "tax_year": 2025,
    "country": "norway",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Clear job store between tests."""
    from taxspine_orchestrator import main as _m

    _m._job_store.clear()


@pytest.fixture()
def client() -> TestClient:
    from taxspine_orchestrator.main import app

    return TestClient(app)


# ── TestAuthHeader ────────────────────────────────────────────────────────────


class TestAuthHeader:
    """Fix 1 — X-Orchestrator-Key header."""

    def test_post_endpoint_requires_key_when_configured(
        self, client: TestClient
    ) -> None:
        """POST /jobs without a key → 401 when ORCHESTRATOR_KEY is set."""
        with patch.dict(os.environ, {"ORCHESTRATOR_KEY": "secret-key"}):
            from taxspine_orchestrator.config import settings as _s

            original = _s.ORCHESTRATOR_KEY
            _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
            try:
                resp = client.post("/jobs", json=_SAMPLE_JOB)
                assert resp.status_code == 401
                assert "X-Orchestrator-Key" in resp.json()["detail"]
            finally:
                _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_post_endpoint_succeeds_with_correct_key(
        self, client: TestClient
    ) -> None:
        """POST /jobs with the correct key → not 401."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.post(
                "/jobs",
                json=_SAMPLE_JOB,
                headers={"X-Orchestrator-Key": "secret-key"},
            )
            assert resp.status_code != 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_post_endpoint_no_auth_when_key_empty(
        self, client: TestClient
    ) -> None:
        """ORCHESTRATOR_KEY = '' → POST succeeds without header."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = ""  # type: ignore[assignment]
        try:
            resp = client.post("/jobs", json=_SAMPLE_JOB)
            # Should not be rejected by auth (may still fail for other reasons,
            # but must not be 401).
            assert resp.status_code != 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_get_endpoint_requires_key_when_configured(self, client: TestClient) -> None:
        """GET /jobs → 401 when ORCHESTRATOR_KEY is set and no key is sent.

        SEC-12 fix: sensitive read endpoints are now gated on the same key
        as mutating endpoints.  An unauthenticated network peer on the LAN
        can no longer read job records, FIFO lots, or cost-basis data.
        """
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/jobs")
            assert resp.status_code == 401, (
                "GET /jobs must return 401 when ORCHESTRATOR_KEY is set "
                "and no X-Orchestrator-Key header is provided"
            )
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_get_endpoint_succeeds_with_correct_key(self, client: TestClient) -> None:
        """GET /jobs → 200 when the correct key header is provided."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/jobs", headers={"X-Orchestrator-Key": "secret-key"})
            assert resp.status_code == 200
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_get_endpoint_no_auth_when_key_empty(self, client: TestClient) -> None:
        """GET /jobs → 200 when ORCHESTRATOR_KEY is '' (dev/local mode)."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = ""  # type: ignore[assignment]
        try:
            resp = client.get("/jobs")
            assert resp.status_code == 200, (
                "GET /jobs must be freely accessible when ORCHESTRATOR_KEY is empty"
            )
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_wrong_key_returns_401(self, client: TestClient) -> None:
        """POST /jobs with the wrong key → 401."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "correct-key"  # type: ignore[assignment]
        try:
            resp = client.post(
                "/jobs",
                json=_SAMPLE_JOB,
                headers={"X-Orchestrator-Key": "wrong-key"},
            )
            assert resp.status_code == 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_health_endpoint_does_not_require_key(
        self, client: TestClient
    ) -> None:
        """GET /health → 200 even when ORCHESTRATOR_KEY is set."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/health")
            assert resp.status_code == 200
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]


# ── TestPathContainment ───────────────────────────────────────────────────────


class TestPathContainment:
    """Fix 2 & 3 — output and upload directory containment."""

    def _make_fake_job_with_path(self, client: TestClient, path_str: str) -> str:
        """Create a pending job, then directly inject a tampered path into the
        job store so the download endpoint will try to serve it.  Returns
        the job id."""
        resp = client.post("/jobs", json=_SAMPLE_JOB)
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        # Directly inject a tampered report_html_path into the job store.
        from taxspine_orchestrator import main as _m
        from taxspine_orchestrator.models import JobOutput, JobStatus

        _m._job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output=JobOutput(report_html_path=path_str, report_html_paths=[path_str]),
        )
        return job_id

    def test_download_outside_output_dir_returns_403(
        self, client: TestClient
    ) -> None:
        """Serving a file outside OUTPUT_DIR must return 403."""
        # Use a path that is guaranteed to be outside OUTPUT_DIR.
        evil_path = "/etc/passwd"
        job_id = self._make_fake_job_with_path(client, evil_path)

        resp = client.get(f"/jobs/{job_id}/files/report")
        assert resp.status_code == 403
        assert "outside output directory" in resp.json()["detail"]

    def test_download_outside_output_dir_by_index_returns_403(
        self, client: TestClient
    ) -> None:
        """GET /jobs/{id}/reports/{index} with a tampered path must return 403."""
        evil_path = "/etc/passwd"
        job_id = self._make_fake_job_with_path(client, evil_path)

        resp = client.get(f"/jobs/{job_id}/reports/0")
        assert resp.status_code == 403
        assert "outside output directory" in resp.json()["detail"]

    def test_csv_outside_upload_dir_returns_400(self, client: TestClient) -> None:
        """POST /workspace/csv with a path outside UPLOAD_DIR must return 400."""
        resp = client.post(
            "/workspace/csv",
            json={"path": "/etc/passwd"},
        )
        assert resp.status_code == 400
        assert "upload directory" in resp.json()["detail"]

    def test_csv_inside_upload_dir_but_missing_returns_400(
        self, client: TestClient
    ) -> None:
        """A path inside UPLOAD_DIR that does not exist should return 400 (not
        403), because the containment check passes but the file is absent."""
        from taxspine_orchestrator.config import settings as _s

        # Construct a path that IS inside UPLOAD_DIR but does not exist.
        missing = str(_s.UPLOAD_DIR / "nonexistent_test_file.csv")
        resp = client.post("/workspace/csv", json={"path": missing})
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()

    def test_attach_csv_outside_upload_dir_returns_400(
        self, client: TestClient
    ) -> None:
        """SEC-13 — POST /jobs/{id}/attach-csv with a path outside UPLOAD_DIR
        must return 400, even for an authenticated caller.

        Previously, only /workspace/csv enforced containment; attach-csv
        bypassed it, allowing arbitrary filesystem paths to be forwarded
        to the tax CLI.
        """
        # Create a pending job.
        resp = client.post("/jobs", json=_SAMPLE_JOB)
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        # Attempt to attach a path outside UPLOAD_DIR.
        resp = client.post(
            f"/jobs/{job_id}/attach-csv",
            json={"csv_files": [{"path": "/etc/passwd", "source_type": "generic_events"}]},
        )
        assert resp.status_code == 400, (
            "attach-csv must reject paths outside UPLOAD_DIR with 400"
        )
        assert "upload directory" in resp.json()["detail"]

    def test_attach_csv_inside_upload_dir_passes_containment(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """SEC-13 — attach-csv accepts paths inside UPLOAD_DIR (containment
        check passes; only fails because the file does not exist, not 400
        due to traversal)."""
        from taxspine_orchestrator.config import settings as _s

        resp = client.post("/jobs", json=_SAMPLE_JOB)
        job_id = resp.json()["id"]

        # A path that IS inside UPLOAD_DIR (file does not exist → 400 file-not-found,
        # not 400 traversal-error).
        inside_path = str(_s.UPLOAD_DIR / "some_nonexistent.csv")
        resp = client.post(
            f"/jobs/{job_id}/attach-csv",
            json={"csv_files": [{"path": inside_path, "source_type": "generic_events"}]},
        )
        # 400 is expected because the file doesn't exist — but NOT because of
        # path containment.  The error message must NOT say "upload directory".
        assert resp.status_code == 400
        assert "upload directory" not in resp.json()["detail"]
        assert "not found" in resp.json()["detail"]


# ── TestSec14PricesFetchAuth ──────────────────────────────────────────────────


class TestSec14PricesFetchAuth:
    """SEC-14 — POST /prices/fetch must require the orchestrator key.

    Previously this endpoint was unauthenticated, allowing any LAN peer to
    trigger repeated outbound HTTPS requests to Kraken and Norges Bank.
    """

    def test_fetch_prices_requires_key_when_configured(
        self, client: TestClient
    ) -> None:
        """POST /prices/fetch → 401 when ORCHESTRATOR_KEY is set and no key sent."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.post("/prices/fetch", json={"year": 2023})
            assert resp.status_code == 401, (
                "POST /prices/fetch must return 401 when key is configured "
                "and the header is missing"
            )
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_get_prices_requires_key_when_configured(
        self, client: TestClient
    ) -> None:
        """GET /prices → 401 when ORCHESTRATOR_KEY is set and no key sent."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/prices")
            assert resp.status_code == 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_fetch_prices_succeeds_with_correct_key(
        self, client: TestClient
    ) -> None:
        """POST /prices/fetch → not 401 when the correct key is provided."""
        from taxspine_orchestrator.config import settings as _s
        from unittest.mock import patch, MagicMock

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            mock_resp = MagicMock()
            mock_resp.asset = "COMBINED"
            mock_resp.year = 2023
            mock_resp.path = "/tmp/combined_nok_2023.csv"
            mock_resp.rows = 365
            mock_resp.age_hours = 0.0
            mock_resp.cached = False
            mock_resp.unsupported_assets = []

            with patch(
                "taxspine_orchestrator.prices.fetch_all_prices_for_year",
                return_value=mock_resp,
            ):
                resp = client.post(
                    "/prices/fetch",
                    json={"year": 2023},
                    headers={"X-Orchestrator-Key": "secret-key"},
                )
            assert resp.status_code != 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_fetch_prices_no_auth_when_key_empty(
        self, client: TestClient
    ) -> None:
        """POST /prices/fetch → not 401 when ORCHESTRATOR_KEY is '' (dev mode)."""
        from taxspine_orchestrator.config import settings as _s
        from unittest.mock import patch, MagicMock

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = ""  # type: ignore[assignment]
        try:
            mock_resp = MagicMock()
            mock_resp.asset = "COMBINED"
            mock_resp.year = 2023
            mock_resp.path = "/tmp/combined_nok_2023.csv"
            mock_resp.rows = 365
            mock_resp.age_hours = 0.0
            mock_resp.cached = False
            mock_resp.unsupported_assets = []

            with patch(
                "taxspine_orchestrator.prices.fetch_all_prices_for_year",
                return_value=mock_resp,
            ):
                resp = client.post("/prices/fetch", json={"year": 2023})
            assert resp.status_code != 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]


# ── TestSec12LotsAndWorkspaceAuth ─────────────────────────────────────────────


class TestSec12LotsAndWorkspaceAuth:
    """SEC-12 — Sensitive read endpoints beyond /jobs require auth."""

    def test_get_workspace_requires_key_when_configured(
        self, client: TestClient
    ) -> None:
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/workspace")
            assert resp.status_code == 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_get_alerts_requires_key_when_configured(
        self, client: TestClient
    ) -> None:
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/alerts")
            assert resp.status_code == 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_get_lots_years_requires_key_when_configured(
        self, client: TestClient
    ) -> None:
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/lots/years")
            assert resp.status_code == 401
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]

    def test_health_endpoint_always_public(self, client: TestClient) -> None:
        """GET /health must remain unauthenticated (liveness probe)."""
        from taxspine_orchestrator.config import settings as _s

        original = _s.ORCHESTRATOR_KEY
        _s.ORCHESTRATOR_KEY = "secret-key"  # type: ignore[assignment]
        try:
            resp = client.get("/health")
            assert resp.status_code == 200
        finally:
            _s.ORCHESTRATOR_KEY = original  # type: ignore[assignment]


# ── TestXrplAddressValidation ─────────────────────────────────────────────────


class TestXrplAddressValidation:
    """Fix 4 — XRPL address format validation."""

    def test_valid_xrpl_address_accepted(self, client: TestClient) -> None:
        """A well-formed XRPL address should not be rejected at validation."""
        resp = client.post(
            "/workspace/accounts",
            json={"account": _VALID_ADDR},
        )
        # 200 = accepted and registered.  Any other status except 422 is fine
        # (could be 400 if workspace already has it, but not 422).
        assert resp.status_code != 422

    def test_invalid_xrpl_address_returns_422(self, client: TestClient) -> None:
        """A totally invalid string should be rejected with 422."""
        resp = client.post(
            "/workspace/accounts",
            json={"account": "not-an-address"},
        )
        assert resp.status_code == 422

    def test_too_short_address_returns_422(self, client: TestClient) -> None:
        """An address that is too short (fewer than 25 total chars) → 422."""
        # 'r' + 5 chars = 6 total, way below minimum 25
        resp = client.post(
            "/workspace/accounts",
            json={"account": "rShort"},
        )
        assert resp.status_code == 422

    def test_too_long_address_returns_422(self, client: TestClient) -> None:
        """An address that is too long (>34 total chars) → 422."""
        # 'r' + 34 chars = 35 total, above maximum 34
        long_addr = "r" + "a" * 34
        resp = client.post(
            "/workspace/accounts",
            json={"account": long_addr},
        )
        assert resp.status_code == 422

    def test_address_with_invalid_chars_returns_422(
        self, client: TestClient
    ) -> None:
        """An address containing base58-excluded chars (0, O, I, l) → 422."""
        # '0' is excluded from base58check
        bad_addr = "r0000000000000000000000000"  # 26 chars total, contains '0'
        resp = client.post(
            "/workspace/accounts",
            json={"account": bad_addr},
        )
        assert resp.status_code == 422

    def test_job_input_with_invalid_xrpl_account_returns_422(
        self, client: TestClient
    ) -> None:
        """POST /jobs with an invalid XRPL address in xrpl_accounts → 422."""
        resp = client.post(
            "/jobs",
            json={
                "xrpl_accounts": ["not-valid"],
                "tax_year": 2025,
                "country": "norway",
            },
        )
        assert resp.status_code == 422

    def test_job_input_with_valid_xrpl_account_accepted(
        self, client: TestClient
    ) -> None:
        """POST /jobs with a valid XRPL address → not 422."""
        resp = client.post("/jobs", json=_SAMPLE_JOB)
        assert resp.status_code != 422
