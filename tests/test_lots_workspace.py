import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _make_app(tmp_path, monkeypatch):
    from taxspine_orchestrator.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "LOT_STORE_DB", tmp_path / "lots.db")
    monkeypatch.setattr(settings, "DEDUP_DIR", tmp_path / "dedup")

    from taxspine_orchestrator import lots as lots_mod
    app = FastAPI()
    app.include_router(lots_mod.router)
    return TestClient(app)


def test_lots_years_default_workspace_no_store(tmp_path, monkeypatch):
    """GET /lots/years with no lot store returns empty list (not 503)."""
    client = _make_app(tmp_path, monkeypatch)
    r = client.get("/lots/years")
    assert r.status_code == 200
    assert r.json() == {"years": [], "db_exists": False}


def test_lots_years_named_workspace_no_store(tmp_path, monkeypatch):
    """GET /lots/years?workspace_id=friend returns empty list when no lot store exists."""
    client = _make_app(tmp_path, monkeypatch)
    r = client.get("/lots/years?workspace_id=friend")
    assert r.status_code == 200
    assert r.json() == {"years": [], "db_exists": False}


def test_lots_year_named_workspace_no_store(tmp_path, monkeypatch):
    """GET /lots/{year}?workspace_id=x returns 404 when no lot store for that workspace."""
    client = _make_app(tmp_path, monkeypatch)
    r = client.get("/lots/2024?workspace_id=friend")
    # 404 or empty is both acceptable; must not be 500
    assert r.status_code in (200, 404)


def test_lots_portfolio_named_workspace_no_store(tmp_path, monkeypatch):
    """GET /lots/{year}/portfolio?workspace_id=x works without erroring."""
    client = _make_app(tmp_path, monkeypatch)
    r = client.get("/lots/2024/portfolio?workspace_id=friend")
    assert r.status_code in (200, 404)
