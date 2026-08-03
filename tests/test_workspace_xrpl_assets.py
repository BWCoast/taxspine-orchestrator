"""Tests for workspace XRPL asset tracking (WorkspaceConfig.xrpl_assets).

Covers:
- WorkspaceConfig.xrpl_assets field defaults to [].
- WorkspaceConfig JSON round-trip preserves xrpl_assets.
- WorkspaceStore.add_xrpl_asset adds a spec (idempotent).
- WorkspaceStore.remove_xrpl_asset removes a spec.
- WorkspaceStore.clear also clears xrpl_assets.
- POST /workspace/xrpl-assets registers an asset → returns updated config.
- POST /workspace/xrpl-assets is idempotent.
- POST /workspace/xrpl-assets rejects invalid spec format (422).
- DELETE /workspace/xrpl-assets/{spec} removes the asset.
- GET /workspace includes xrpl_assets field.
- Full add→GET→remove→GET roundtrip.
- Auto-fetch in _run_job passes workspace xrpl_assets to fetch_all_prices_for_year.
- Auto-fetch with empty workspace assets works without error.
- Execution log mentions workspace assets when they are present.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import WorkspaceConfig
from taxspine_orchestrator.storage import WorkspaceStore


# ── Constants ─────────────────────────────────────────────────────────────────

_SOLO_SPEC  = "SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL"
_MXRP_SPEC  = "mXRP.r4GDFMLGJUKMjNEycw16tWB9CqEjxztMqJ"
_RLUSD_SPEC = "RLUSD.rMxCKbEDwqr76QuheSUMdEGf4B9xJ8m5De"

_XRPL_ACCOUNT = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
_NORWAY_BASE = {
    "xrpl_accounts": [_XRPL_ACCOUNT],
    "tax_year": 2025,
    "country": "norway",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_stores():
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()
    _m._workspace_store.clear()
    yield
    _m._job_store.clear()
    _m._workspace_store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


# ── TestWorkspaceXrplAssetsModel ──────────────────────────────────────────────


class TestWorkspaceXrplAssetsModel:
    def test_defaults_to_empty_list(self):
        cfg = WorkspaceConfig()
        assert cfg.xrpl_assets == []

    def test_json_round_trip(self):
        cfg = WorkspaceConfig(xrpl_assets=[_SOLO_SPEC, _MXRP_SPEC])
        restored = WorkspaceConfig.model_validate_json(cfg.model_dump_json())
        assert restored.xrpl_assets == [_SOLO_SPEC, _MXRP_SPEC]

    def test_field_preserved_alongside_accounts_and_csv(self):
        cfg = WorkspaceConfig(
            xrpl_accounts=[_XRPL_ACCOUNT],
            xrpl_assets=[_SOLO_SPEC],
        )
        assert cfg.xrpl_accounts == [_XRPL_ACCOUNT]
        assert cfg.xrpl_assets == [_SOLO_SPEC]


# ── TestWorkspaceStoreXrplAssets ──────────────────────────────────────────────


class TestWorkspaceStoreXrplAssets:
    def test_add_xrpl_asset(self, tmp_path):
        store = WorkspaceStore(tmp_path / "ws.json")
        cfg = store.add_xrpl_asset(_SOLO_SPEC)
        assert _SOLO_SPEC in cfg.xrpl_assets

    def test_add_xrpl_asset_idempotent(self, tmp_path):
        store = WorkspaceStore(tmp_path / "ws.json")
        store.add_xrpl_asset(_SOLO_SPEC)
        cfg = store.add_xrpl_asset(_SOLO_SPEC)
        assert cfg.xrpl_assets.count(_SOLO_SPEC) == 1

    def test_remove_xrpl_asset(self, tmp_path):
        store = WorkspaceStore(tmp_path / "ws.json")
        store.add_xrpl_asset(_SOLO_SPEC)
        store.add_xrpl_asset(_MXRP_SPEC)
        cfg = store.remove_xrpl_asset(_SOLO_SPEC)
        assert _SOLO_SPEC not in cfg.xrpl_assets
        assert _MXRP_SPEC in cfg.xrpl_assets

    def test_remove_nonexistent_is_safe(self, tmp_path):
        store = WorkspaceStore(tmp_path / "ws.json")
        cfg = store.remove_xrpl_asset(_SOLO_SPEC)
        assert cfg.xrpl_assets == []

    def test_clear_removes_xrpl_assets(self, tmp_path):
        store = WorkspaceStore(tmp_path / "ws.json")
        store.add_xrpl_asset(_SOLO_SPEC)
        store.add_xrpl_asset(_MXRP_SPEC)
        cfg = store.clear()
        assert cfg.xrpl_assets == []

    def test_persisted_across_load(self, tmp_path):
        store = WorkspaceStore(tmp_path / "ws.json")
        store.add_xrpl_asset(_SOLO_SPEC)
        store2 = WorkspaceStore(tmp_path / "ws.json")
        assert _SOLO_SPEC in store2.load().xrpl_assets


# ── TestWorkspaceXrplAssetsEndpoints ─────────────────────────────────────────


class TestWorkspaceXrplAssetsEndpoints:
    def test_post_adds_asset(self, client):
        resp = client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        assert resp.status_code == 200
        assert _SOLO_SPEC in resp.json()["xrpl_assets"]

    def test_post_is_idempotent(self, client):
        client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        resp = client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        assert resp.status_code == 200
        assert resp.json()["xrpl_assets"].count(_SOLO_SPEC) == 1

    def test_post_multiple_assets(self, client):
        client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        resp = client.post("/workspace/xrpl-assets", json={"spec": _MXRP_SPEC})
        assets = resp.json()["xrpl_assets"]
        assert _SOLO_SPEC in assets
        assert _MXRP_SPEC in assets

    def test_post_invalid_spec_no_dot(self, client):
        resp = client.post("/workspace/xrpl-assets", json={"spec": "SOLONODOT"})
        assert resp.status_code == 422

    def test_post_invalid_spec_bad_issuer(self, client):
        resp = client.post("/workspace/xrpl-assets", json={"spec": "SOLO.notanaddress"})
        assert resp.status_code == 422

    def test_post_invalid_spec_empty(self, client):
        resp = client.post("/workspace/xrpl-assets", json={"spec": ""})
        assert resp.status_code == 422

    def test_delete_removes_asset(self, client):
        client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        client.post("/workspace/xrpl-assets", json={"spec": _MXRP_SPEC})
        resp = client.delete(f"/workspace/xrpl-assets/{_SOLO_SPEC}")
        assert resp.status_code == 200
        assets = resp.json()["xrpl_assets"]
        assert _SOLO_SPEC not in assets
        assert _MXRP_SPEC in assets

    def test_get_workspace_includes_xrpl_assets(self, client):
        client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        resp = client.get("/workspace")
        assert resp.status_code == 200
        assert "xrpl_assets" in resp.json()
        assert _SOLO_SPEC in resp.json()["xrpl_assets"]

    def test_roundtrip_add_get_remove_get(self, client):
        # Add
        client.post("/workspace/xrpl-assets", json={"spec": _RLUSD_SPEC})
        assert _RLUSD_SPEC in client.get("/workspace").json()["xrpl_assets"]
        # Remove
        client.delete(f"/workspace/xrpl-assets/{_RLUSD_SPEC}")
        assert _RLUSD_SPEC not in client.get("/workspace").json()["xrpl_assets"]

    def test_purge_workspace_clears_xrpl_assets(self, client):
        client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        resp = client.delete("/workspace")
        assert resp.status_code == 200
        assert resp.json()["xrpl_assets"] == []


# ── TestAutoFetchUsesWorkspaceAssets ─────────────────────────────────────────


class TestAutoFetchUsesWorkspaceAssets:
    """Auto-fetch in _run_job passes workspace xrpl_assets to fetch_all_prices_for_year."""

    def test_workspace_assets_forwarded_to_fetch(self, client, tmp_path):
        """fetch_all_prices_for_year is called with workspace xrpl_assets."""
        client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})
        client.post("/workspace/xrpl-assets", json={"spec": _MXRP_SPEC})

        resp = client.post("/jobs", json={**_NORWAY_BASE, "valuation_mode": "price_table"})
        job_id = resp.json()["id"]

        from tests.conftest import start_and_wait
        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year") as mock_fetch,
        ):
            mock_s.PRICES_DIR = tmp_path   # no cached CSV → triggers auto-fetch
            mock_s.OUTPUT_DIR = tmp_path
            mock_fetch.side_effect = RuntimeError("offline")
            start_and_wait(client, job_id)

        mock_fetch.assert_called_once()
        extra = mock_fetch.call_args.kwargs.get("extra_xrpl_assets")
        assert extra is not None, f"extra_xrpl_assets not passed; call_args={mock_fetch.call_args}"
        assert _SOLO_SPEC in extra
        assert _MXRP_SPEC in extra

    def test_empty_workspace_assets_no_error(self, client, tmp_path):
        """Auto-fetch works fine when workspace has no xrpl_assets."""
        resp = client.post("/jobs", json={**_NORWAY_BASE, "valuation_mode": "price_table"})
        job_id = resp.json()["id"]

        from tests.conftest import start_and_wait
        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year") as mock_fetch,
        ):
            mock_s.PRICES_DIR = tmp_path
            mock_s.OUTPUT_DIR = tmp_path
            mock_fetch.side_effect = RuntimeError("offline")
            body = start_and_wait(client, job_id)

        assert body["status"] == "failed"
        assert "Auto-fetch" in body["output"]["error_message"]

    def test_log_mentions_workspace_assets(self, client, tmp_path):
        """Execution log records which workspace assets are included in fetch."""
        client.post("/workspace/xrpl-assets", json={"spec": _SOLO_SPEC})

        resp = client.post("/jobs", json={**_NORWAY_BASE, "valuation_mode": "price_table"})
        job_id = resp.json()["id"]

        from tests.conftest import start_and_wait
        with (
            patch("taxspine_orchestrator.services.settings") as mock_s,
            patch("taxspine_orchestrator.prices.fetch_all_prices_for_year") as mock_fetch,
        ):
            mock_s.PRICES_DIR = tmp_path
            mock_s.OUTPUT_DIR = tmp_path
            mock_fetch.side_effect = RuntimeError("offline")
            body = start_and_wait(client, job_id)

        log = Path(body["output"]["log_path"]).read_text(encoding="utf-8")
        assert "workspace" in log.lower()
        assert "SOLO" in log
