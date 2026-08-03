import json
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _make_app(tmp_path, monkeypatch):
    """Create a test client with workspaces router, DATA_DIR isolated to tmp_path."""
    from taxspine_orchestrator.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "LOT_STORE_DB", tmp_path / "lots.db")
    monkeypatch.setattr(settings, "DEDUP_DIR", tmp_path / "dedup")

    from taxspine_orchestrator import workspaces as ws_mod
    app = FastAPI()
    app.include_router(ws_mod.router)
    return TestClient(app)


def test_create_workspace_with_display_name(tmp_path, monkeypatch):
    client = _make_app(tmp_path, monkeypatch)

    r = client.post("/workspaces", json={"workspace_id": "friend", "display_name": "Alice"})
    assert r.status_code == 201
    data = r.json()
    assert data["display_name"] == "Alice"

    # meta.json written to disk
    meta = tmp_path / "workspaces" / "friend" / "meta.json"
    assert meta.is_file()
    assert json.loads(meta.read_text(encoding="utf-8"))["display_name"] == "Alice"


def test_create_workspace_default_display_name(tmp_path, monkeypatch):
    client = _make_app(tmp_path, monkeypatch)

    r = client.post("/workspaces", json={"workspace_id": "bob"})
    assert r.status_code == 201
    assert r.json()["display_name"] == "bob"  # falls back to workspace_id


def test_list_workspaces_includes_display_name(tmp_path, monkeypatch):
    client = _make_app(tmp_path, monkeypatch)

    # Pre-create workspace dir with meta.json
    ws_dir = tmp_path / "workspaces" / "carol"
    ws_dir.mkdir(parents=True)
    (ws_dir / "meta.json").write_text(json.dumps({"display_name": "Carol"}), encoding="utf-8")

    r = client.get("/workspaces")
    assert r.status_code == 200
    items = r.json()
    assert any(i["workspace_id"] == "carol" and i["display_name"] == "Carol" for i in items)


def test_get_workspace_includes_display_name(tmp_path, monkeypatch):
    client = _make_app(tmp_path, monkeypatch)

    # Create workspace via API, then GET it
    client.post("/workspaces", json={"workspace_id": "dave", "display_name": "Dave"})
    r = client.get("/workspaces/dave")
    assert r.status_code == 200
    assert r.json()["display_name"] == "Dave"


def test_display_name_fallback_when_no_meta_json(tmp_path, monkeypatch):
    client = _make_app(tmp_path, monkeypatch)

    # Create workspace dir without meta.json
    ws_dir = tmp_path / "workspaces" / "eve"
    ws_dir.mkdir(parents=True)

    r = client.get("/workspaces/eve")
    assert r.status_code == 200
    assert r.json()["display_name"] == "eve"  # falls back to workspace_id
