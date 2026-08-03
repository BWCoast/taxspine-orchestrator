# Multi-Workspace Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow one orchestrator instance to manage tax profiles for multiple people, each with isolated lots, dedup, and workspace config — switchable from the UI topbar.

**Architecture:** The backend already has `WorkspacePaths.resolve()`, `workspace_id` in `JobInput`, and a full `/workspaces` CRUD router. What's missing is threading `?workspace_id=` through `/workspace/*`, `/lots/*`, `/review/*`, and `/sidecar/{year}`, plus storing a human-readable `display_name` per workspace and adding a workspace pill to the UI topbar. Existing data stays in the "default" workspace (no migration).

**Tech Stack:** FastAPI + SQLite (backend), React 18 via Babel standalone (UI), localStorage for active workspace persistence.

---

## File Map

| File | What changes |
|------|-------------|
| `taxspine_orchestrator/workspaces.py` | Add `display_name` field; read/write `meta.json` in workspace dir |
| `taxspine_orchestrator/lots.py` | `_open_store(workspace_id)` uses `WorkspacePaths.resolve()`; all 4 routes get `?workspace_id=` |
| `taxspine_orchestrator/review.py` | `?workspace_id=` on summary + jobs routes; filter `store.list()` by workspace; route lot store via workspace paths |
| `taxspine_orchestrator/main.py` | `_get_ws_store(workspace_id)` helper; add `?workspace_id=` to all `/workspace/*` endpoints; fix sidecar path for named workspaces |
| `ALTANA_extracted/api.js` + `ui/api.js` | Workspace context from localStorage; `?workspace_id=` appended to relevant calls; `getWorkspaces()`, `createWorkspace()` |
| `ALTANA_extracted/shell.jsx` + `ui/shell.jsx` | Workspace pill in Topbar — shows current display_name, opens switcher/create dropdown |

---

## Task 1: `workspaces.py` — add display_name via meta.json

**Files:**
- Modify: `taxspine_orchestrator/workspaces.py`
- Test: `tests/test_workspaces_meta.py` (new)

Workspace directories get a `meta.json` file: `{"display_name": "My Friend"}`. The `WorkspaceInfo` response includes `display_name`. The `CreateWorkspaceRequest` accepts an optional `display_name` (defaults to the workspace_id if omitted).

- [ ] **Step 1: Write failing tests**

Create `tests/test_workspaces_meta.py`:
```python
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

def test_create_workspace_with_display_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from taxspine_orchestrator.config import settings
    settings.DATA_DIR = tmp_path

    from taxspine_orchestrator.workspaces import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/workspaces", json={"workspace_id": "friend", "display_name": "Alice"})
    assert r.status_code == 201
    data = r.json()
    assert data["display_name"] == "Alice"
    # meta.json written to disk
    meta = tmp_path / "workspaces" / "friend" / "meta.json"
    assert meta.is_file()
    import json
    assert json.loads(meta.read_text())["display_name"] == "Alice"

def test_create_workspace_default_display_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from taxspine_orchestrator.config import settings
    settings.DATA_DIR = tmp_path

    from taxspine_orchestrator.workspaces import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/workspaces", json={"workspace_id": "bob"})
    assert r.status_code == 201
    assert r.json()["display_name"] == "bob"  # falls back to workspace_id

def test_list_workspaces_includes_display_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from taxspine_orchestrator.config import settings
    settings.DATA_DIR = tmp_path
    import json

    ws_dir = tmp_path / "workspaces" / "carol"
    ws_dir.mkdir(parents=True)
    (ws_dir / "meta.json").write_text(json.dumps({"display_name": "Carol"}))

    from taxspine_orchestrator.workspaces import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/workspaces")
    assert r.status_code == 200
    items = r.json()
    assert any(i["workspace_id"] == "carol" and i["display_name"] == "Carol" for i in items)
```

- [ ] **Step 2: Run to confirm failure**
```
cd "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator"
python -m pytest tests/test_workspaces_meta.py -v
```
Expected: `AttributeError` or `ValidationError` — `display_name` not in model.

- [ ] **Step 3: Implement `display_name` in `workspaces.py`**

Add two helper functions and update the three models/endpoints. Replace the entire file content:

```python
"""Named workspace CRUD router."""
from __future__ import annotations

import json as _json
import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .config import settings
from .storage import WorkspacePaths

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
_WORKSPACE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,37}$")


class WorkspaceInfo(BaseModel):
    workspace_id: str
    display_name: str
    lots_db_exists: bool
    dedup_dir_exists: bool
    workspace_json_exists: bool


def _workspace_dir(workspace_id: str) -> Path:
    return settings.DATA_DIR / "workspaces" / workspace_id


def _read_display_name(ws_dir: Path, fallback: str) -> str:
    meta = ws_dir / "meta.json"
    if meta.is_file():
        try:
            return _json.loads(meta.read_text(encoding="utf-8")).get("display_name", fallback)
        except (OSError, ValueError):
            pass
    return fallback


def _write_display_name(ws_dir: Path, display_name: str) -> None:
    meta = ws_dir / "meta.json"
    meta.write_text(_json.dumps({"display_name": display_name}, indent=2), encoding="utf-8")


def _workspace_info(workspace_id: str) -> WorkspaceInfo:
    ws_dir = _workspace_dir(workspace_id)
    paths = WorkspacePaths.resolve(
        workspace_id,
        data_dir=settings.DATA_DIR,
        lots_db=settings.LOT_STORE_DB,
        dedup_dir=settings.DEDUP_DIR,
    )
    return WorkspaceInfo(
        workspace_id=workspace_id,
        display_name=_read_display_name(ws_dir, fallback=workspace_id),
        lots_db_exists=bool(paths.lots_db and paths.lots_db.is_file()),
        dedup_dir_exists=paths.dedup_dir.is_dir(),
        workspace_json_exists=paths.workspace_json.is_file(),
    )


@router.get("", response_model=list[WorkspaceInfo])
def list_workspaces() -> list[WorkspaceInfo]:
    ws_root = settings.DATA_DIR / "workspaces"
    if not ws_root.is_dir():
        return []
    return [
        _workspace_info(ws_dir.name)
        for ws_dir in sorted(ws_root.iterdir())
        if ws_dir.is_dir() and _WORKSPACE_ID_RE.match(ws_dir.name)
    ]


class CreateWorkspaceRequest(BaseModel):
    workspace_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,37}$")
    display_name: Optional[str] = Field(default=None)


@router.post("", response_model=WorkspaceInfo, status_code=201)
def create_workspace(body: CreateWorkspaceRequest) -> WorkspaceInfo:
    ws_dir = _workspace_dir(body.workspace_id)
    if ws_dir.exists():
        raise HTTPException(status_code=409, detail=f"Workspace '{body.workspace_id}' already exists")
    ws_dir.mkdir(parents=True, exist_ok=True)
    name = body.display_name or body.workspace_id
    _write_display_name(ws_dir, name)
    return _workspace_info(body.workspace_id)


@router.get("/{workspace_id}", response_model=WorkspaceInfo)
def get_workspace(workspace_id: str) -> WorkspaceInfo:
    if not _workspace_dir(workspace_id).is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_id}' not found")
    return _workspace_info(workspace_id)


@router.delete("/{workspace_id}", status_code=204)
def delete_workspace(
    workspace_id: str,
    delete_data: bool = Query(default=False),
) -> None:
    ws_dir = _workspace_dir(workspace_id)
    if not ws_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_id}' not found")
    if delete_data:
        shutil.rmtree(ws_dir)
    else:
        try:
            ws_dir.rmdir()
        except OSError:
            pass
```

- [ ] **Step 4: Run tests — expect PASS**
```
python -m pytest tests/test_workspaces_meta.py -v
```

- [ ] **Step 5: Commit**
```
git add taxspine_orchestrator/workspaces.py tests/test_workspaces_meta.py
git commit -m "feat: add display_name to WorkspaceInfo via meta.json"
```

---

## Task 2: `lots.py` — workspace_id awareness

**Files:**
- Modify: `taxspine_orchestrator/lots.py`
- Test: `tests/test_lots_workspace.py` (new)

The `_open_store()` helper currently hardcodes `settings.LOT_STORE_DB`. We need it to accept an optional `workspace_id` and resolve the correct path via `WorkspacePaths.resolve()`.

- [ ] **Step 1: Write failing test**

Create `tests/test_lots_workspace.py`:
```python
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

def test_lots_years_uses_workspace_lot_store(tmp_path, monkeypatch):
    """GET /lots/years?workspace_id=friend uses the friend workspace lot store."""
    from taxspine_orchestrator.config import settings
    settings.DATA_DIR = tmp_path
    settings.LOT_STORE_DB = tmp_path / "lots.db"
    settings.DEDUP_DIR = tmp_path / "dedup"

    from taxspine_orchestrator.lots import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Default workspace → no lot store → empty list (not 503)
    r = client.get("/lots/years")
    assert r.status_code == 200
    assert r.json() == []

    # Named workspace that doesn't exist → empty list (not 503)
    r = client.get("/lots/years?workspace_id=friend")
    assert r.status_code == 200
    assert r.json() == []
```

- [ ] **Step 2: Run to confirm it passes baseline (or fails for the right reason)**
```
python -m pytest tests/test_lots_workspace.py -v
```

- [ ] **Step 3: Update `lots.py`**

Find `_open_store` and all four route definitions. Replace:

```python
# OLD
def _open_store():
    db_path = settings.LOT_STORE_DB
    if not db_path.is_file():
        return None
    return LotPersistenceStore(str(db_path))
```

With:

```python
# NEW — add this import at top of file
from .storage import WorkspacePaths

def _open_store(workspace_id: str | None = None):
    """Open a read-only connection to the correct lot store for workspace_id."""
    try:
        from tax_spine.pipeline.lot_store import LotPersistenceStore
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"tax_spine package not available: {exc}",
        ) from exc

    if workspace_id is None:
        db_path = settings.LOT_STORE_DB
    else:
        paths = WorkspacePaths.resolve(
            workspace_id,
            data_dir=settings.DATA_DIR,
            lots_db=settings.LOT_STORE_DB,
            dedup_dir=settings.DEDUP_DIR,
        )
        db_path = paths.lots_db

    if db_path is None or not db_path.is_file():
        return None
    return LotPersistenceStore(str(db_path))
```

Then add `workspace_id: str | None = Query(None)` to all four route functions and pass it to `_open_store(workspace_id)`. The four routes are:
- `GET /lots/years`
- `GET /lots/{year}`
- `GET /lots/{year}/carry-forward`
- `GET /lots/{year}/portfolio`

For each, change `store = _open_store()` → `store = _open_store(workspace_id)`.

Example for `GET /lots/years`:
```python
@router.get("/years")
def list_lot_years(
    workspace_id: str | None = Query(None, description="Named workspace (omit for default)"),
) -> list[int]:
    store = _open_store(workspace_id)
    if store is None:
        return []
    with store:
        return sorted(store.list_years())
```

Apply the same `workspace_id: str | None = Query(None)` + `_open_store(workspace_id)` pattern to the other three routes.

- [ ] **Step 4: Run tests**
```
python -m pytest tests/test_lots_workspace.py -v
```

- [ ] **Step 5: Run full test suite to check no regressions**
```
python -m pytest tests/ -x -q 2>&1 | tail -20
```

- [ ] **Step 6: Commit**
```
git add taxspine_orchestrator/lots.py tests/test_lots_workspace.py
git commit -m "feat: thread workspace_id through /lots/* endpoints"
```

---

## Task 3: `review.py` — workspace_id awareness

**Files:**
- Modify: `taxspine_orchestrator/review.py`

Two changes:
1. `_missing_basis_detail(year)` needs `workspace_id` so it reads the right `lots.db`.
2. `get_review_summary` needs `?workspace_id=` and must pass it to `store.list(filter_workspace=True, workspace_id=...)`.

- [ ] **Step 1: Update `_missing_basis_detail`**

Change the signature and the `db_path` resolution:

```python
# OLD
def _missing_basis_detail(year: int) -> list[dict]:
    ...
    db_path = settings.LOT_STORE_DB
    if not db_path.is_file():
        return []
```

```python
# NEW — add import at top of file
from .storage import WorkspacePaths

def _missing_basis_detail(year: int, workspace_id: str | None = None) -> list[dict]:
    try:
        from tax_spine.pipeline.lot_store import LotPersistenceStore
    except ImportError:
        return []

    if workspace_id is None:
        db_path = settings.LOT_STORE_DB
    else:
        paths = WorkspacePaths.resolve(
            workspace_id,
            data_dir=settings.DATA_DIR,
            lots_db=settings.LOT_STORE_DB,
            dedup_dir=settings.DEDUP_DIR,
        )
        db_path = paths.lots_db

    if db_path is None or not db_path.is_file():
        return []
    # ... rest of the function unchanged
```

Also update `_missing_basis_assets` to pass workspace_id through:
```python
def _missing_basis_assets(year: int, workspace_id: str | None = None) -> list[str]:
    return [d["asset"] for d in _missing_basis_detail(year, workspace_id)]
```

- [ ] **Step 2: Update `get_review_summary`**

Add `workspace_id` query param and pass to job filter and missing_basis:

```python
@router.get("/summary", summary="Aggregated review summary for a tax year")
def get_review_summary(
    year: int = Query(..., ge=2009, le=2100),
    workspace_id: str | None = Query(None, description="Named workspace (omit for default)"),
) -> dict:
    from .storage import SqliteJobStore
    from .models import JobStatus, Country

    db_path = settings.DATA_DIR / "jobs.db"
    if not db_path.is_file():
        return _empty_summary(year)

    store = SqliteJobStore(db_path)
    # Filter by workspace: None = default workspace (jobs with workspace_id IS NULL)
    all_jobs = store.list(
        status=JobStatus.COMPLETED,
        country=Country.NORWAY,
        limit=10_000,
        offset=0,
        filter_workspace=True,
        workspace_id=workspace_id,
    )
    year_jobs = [j for j in all_jobs if j.input.tax_year == year]

    # ... rest of the function unchanged until _missing_basis_detail call:
    missing_detail = _missing_basis_detail(year, workspace_id)
    # ... rest unchanged
```

Apply the same `workspace_id` param to the `GET /review/jobs` route if it exists (same pattern — add param, pass to `store.list(filter_workspace=True, workspace_id=workspace_id)`).

- [ ] **Step 3: Run full test suite**
```
python -m pytest tests/ -x -q 2>&1 | tail -20
```

- [ ] **Step 4: Commit**
```
git add taxspine_orchestrator/review.py
git commit -m "feat: thread workspace_id through /review/* endpoints"
```

---

## Task 4: `main.py` — /workspace and /sidecar workspace awareness

**Files:**
- Modify: `taxspine_orchestrator/main.py`

Two sub-tasks:
1. Add `_get_ws_store(workspace_id)` helper and thread `?workspace_id=` through all `/workspace/*` endpoints.
2. Fix `/sidecar/{year}` to look in `PERSONAL_REPORTS_DIR/{workspace_id}/{year}/` for named workspaces.

- [ ] **Step 1: Add `_get_ws_store` helper**

After the `_workspace_store = WorkspaceStore(...)` line (~line 179), add:

```python
def _get_ws_store(workspace_id: str | None) -> WorkspaceStore:
    """Return the WorkspaceStore for the given workspace.

    Returns the global default store when workspace_id is None.
    For named workspaces, creates a per-request WorkspaceStore pointing
    at DATA_DIR/workspaces/{workspace_id}/workspace.json.
    The workspace directory is created lazily if it doesn't exist yet.
    """
    from .storage import WorkspacePaths  # already imported at module level, but safe to repeat
    if workspace_id is None:
        return _workspace_store  # reuse the global singleton
    paths = WorkspacePaths.resolve(
        workspace_id,
        data_dir=settings.DATA_DIR,
        lots_db=settings.LOT_STORE_DB,
        dedup_dir=settings.DEDUP_DIR,
    )
    paths.workspace_json.parent.mkdir(parents=True, exist_ok=True)
    return WorkspaceStore(paths.workspace_json)
```

- [ ] **Step 2: Add `?workspace_id=` to `/workspace` GET**

Find:
```python
@app.get("/workspace", response_model=WorkspaceConfig, ...)
def get_workspace() -> WorkspaceConfig:
    return _workspace_store.load()
```

Replace with:
```python
@app.get("/workspace", response_model=WorkspaceConfig, ...)
def get_workspace(
    workspace_id: str | None = Query(None, description="Named workspace (omit for default)"),
) -> WorkspaceConfig:
    return _get_ws_store(workspace_id).load()
```

- [ ] **Step 3: Add `?workspace_id=` to all mutating `/workspace/*` endpoints**

There are ~12 workspace mutation endpoints (`POST /workspace/accounts`, `DELETE /workspace/accounts/{account}`, `POST /workspace/xrpl-assets`, `DELETE /workspace/xrpl-assets/{spec}`, `PUT/DELETE /workspace/labels/wallet/{address}`, `PUT/DELETE /workspace/labels/asset/{spec}`, `PUT/DELETE /workspace/labels/nft-collection/{prefix}`, `DELETE /workspace`).

For each one, add `workspace_id: str | None = Query(None)` as a parameter and replace `_workspace_store.` with `_get_ws_store(workspace_id).`:

Example:
```python
@app.post("/workspace/accounts", response_model=WorkspaceConfig, ...)
def add_workspace_account(
    body: AddAccountRequest,
    workspace_id: str | None = Query(None),
) -> WorkspaceConfig:
    return _get_ws_store(workspace_id).add_account(body.account)
```

Apply the same pattern to all other workspace mutation endpoints. The `DELETE /workspace` endpoint (purge) also needs it — use `_get_ws_store(workspace_id).load()` and `_get_ws_store(workspace_id).clear()`.

- [ ] **Step 4: Fix `/sidecar/{year}` for named workspaces**

Find the sidecar endpoint and change the path resolution:

```python
@app.get("/sidecar/{year}", tags=["meta"], dependencies=[Depends(_require_key)])
async def get_dashboard_sidecar(
    year: int,
    workspace_id: str | None = Query(None),
) -> dict:
    if not settings.PERSONAL_REPORTS_DIR:
        raise HTTPException(status_code=404, detail="PERSONAL_REPORTS_DIR is not configured")

    # Named workspace: PERSONAL_REPORTS_DIR/{workspace_id}/{year}/dashboard_summary.json
    # Default workspace: PERSONAL_REPORTS_DIR/{year}/dashboard_summary.json (backward compat)
    if workspace_id is not None:
        sidecar_path = settings.PERSONAL_REPORTS_DIR / workspace_id / str(year) / "dashboard_summary.json"
    else:
        sidecar_path = settings.PERSONAL_REPORTS_DIR / str(year) / "dashboard_summary.json"

    if not sidecar_path.is_file():
        raise HTTPException(status_code=404, detail=f"No dashboard sidecar found for {year}")
    try:
        import json as _json_sidecar
        raw = await asyncio.to_thread(sidecar_path.read_text, encoding="utf-8")
        return _json_sidecar.loads(raw)
    except Exception as exc:
        _log.error("sidecar: could not read %s: %s", sidecar_path, exc)
        raise HTTPException(status_code=500, detail="Could not read sidecar file")
```

- [ ] **Step 5: Run full test suite**
```
python -m pytest tests/ -x -q 2>&1 | tail -20
```

- [ ] **Step 6: Commit**
```
git add taxspine_orchestrator/main.py
git commit -m "feat: thread workspace_id through /workspace/* and /sidecar endpoints"
```

---

## Task 5: UI — `api.js` workspace context

**Files:**
- Modify: `ALTANA_extracted/api.js`
- Then copy to: `ui/api.js`

Add workspace context — persisted in localStorage under `'TAXNOR_WORKSPACE'`. All API calls that are workspace-scoped append `?workspace_id=` when a named workspace is active (omit when `null` so the backend defaults to the default workspace).

- [ ] **Step 1: Update `api.js`**

Add workspace state management and a `_wsParam()` helper. Insert after the `apiKey()` function and before `apiFetch`:

```javascript
// ── Workspace context ──────────────────────────────────────────────────────
// null   = default workspace (omit workspace_id from all calls)
// string = named workspace id (appended as ?workspace_id=X)
const _WS_KEY = 'TAXNOR_WORKSPACE';
const getWorkspaceId = () =>
  (typeof localStorage !== 'undefined' ? localStorage.getItem(_WS_KEY) : null) || null;
const setWorkspaceId = (id) => {
  if (typeof localStorage === 'undefined') return;
  if (id === null) localStorage.removeItem(_WS_KEY);
  else localStorage.setItem(_WS_KEY, id);
};
// Returns '?workspace_id=X' or '' depending on active workspace
const _wsParam = (extra = '') => {
  const id = getWorkspaceId();
  const ws = id ? `workspace_id=${encodeURIComponent(id)}` : '';
  const both = [ws, extra].filter(Boolean).join('&');
  return both ? `?${both}` : '';
};
```

Then update all workspace-scoped endpoint calls to include `_wsParam()`. The full updated return object:

```javascript
return {
  getHealth:      ()            => get('/health'),
  getDiagnostics: ()            => get('/diagnostics'),
  getAlerts:      (limit = 20)  => get(`/alerts?limit=${limit}`),

  getJobs: (params = {}) => {
    const qs = new URLSearchParams({ limit: 50, ...params }).toString();
    return get(`/jobs?${qs}`);
  },
  getJob:       (id)   => get(`/jobs/${id}`),
  startJob:     (id)   => post(`/jobs/${id}/start`, {}),
  runWorkspace: (body) => post('/workspace/run', body),

  getLotYears:  ()           => get(`/lots/years${_wsParam()}`),
  getLotYear:   (year)       => get(`/lots/${year}${_wsParam()}`),
  getPortfolio: (year, live) => get(
    `/lots/${year}/portfolio${_wsParam(`include_prices=true&price_type=${live ? 'current' : 'year_end'}`)}`
  ),

  getReviewSummary: (year) => get(`/review/summary${_wsParam(`year=${year}`)}`),

  getWorkspace: () => get(`/workspace${_wsParam()}`),

  getSpotPrices: () => get('/prices/spot'),
  getPriceFiles: () => get('/prices'),

  getDedupSources:  ()       => get('/dedup/sources'),
  getDedupSummary:  (source) => get(`/dedup/${source}/summary`),

  getBotStatus: () => get('/bot/sync/status'),

  getSidecar: (year) => get(`/sidecar/${year}${_wsParam()}`),

  // ── Workspace management ────────────────────────────────────────────────
  getWorkspaces:    ()     => get('/workspaces'),
  createWorkspace:  (body) => post('/workspaces', body),
  deleteWorkspace:  (id, deleteData = false) =>
    apiFetch(`/workspaces/${id}?delete_data=${deleteData}`, { method: 'DELETE' }),

  // Expose workspace switcher so UI components can call it
  getWorkspaceId,
  setWorkspaceId,
};
```

- [ ] **Step 2: Copy to `ui/`**
```powershell
Copy-Item "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator\ALTANA_extracted\api.js" `
          "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator\ui\api.js" -Force
```

- [ ] **Step 3: Verify in browser console**

Open `http://localhost:8000/ui/`, open DevTools → Console, run:
```javascript
window.API.getWorkspaceId()      // → null (default workspace)
window.API.setWorkspaceId('x')
window.API.getWorkspaceId()      // → 'x'
window.API.setWorkspaceId(null)  // reset
```

---

## Task 6: UI — Topbar workspace switcher

**Files:**
- Modify: `ALTANA_extracted/shell.jsx`
- Then copy to: `ui/shell.jsx`

Add a workspace pill to the Topbar (next to the jurisdiction pill). Clicking it opens an inline dropdown listing available workspaces plus a "New workspace" row. Selecting a workspace calls `window.API.setWorkspaceId()` and reloads the page so all `useApi` hooks re-fetch with the new context.

- [ ] **Step 1: Add `WorkspaceSwitcher` component and wire into Topbar**

In `shell.jsx`, add the `WorkspaceSwitcher` component before `Topbar`:

```jsx
const WorkspaceSwitcher = () => {
  const [open, setOpen]             = React.useState(false);
  const [creating, setCreating]     = React.useState(false);
  const [newId, setNewId]           = React.useState('');
  const [newName, setNewName]       = React.useState('');
  const [error, setError]           = React.useState('');
  const { data: workspaces, reload } = window.useApi(() => window.API.getWorkspaces(), []);
  const activeId   = window.API.getWorkspaceId();  // null = default
  const activeName = activeId
    ? (workspaces?.find(w => w.workspace_id === activeId)?.display_name ?? activeId)
    : 'My workspace';

  const switchTo = (id) => {
    window.API.setWorkspaceId(id);   // null clears → default
    window.location.reload();
  };

  const handleCreate = async () => {
    setError('');
    if (!newId.match(/^[a-z][a-z0-9_-]{0,37}$/)) {
      setError('ID: lowercase letters, digits, hyphens. Start with a letter.');
      return;
    }
    try {
      await window.API.createWorkspace({ workspace_id: newId, display_name: newName || newId });
      reload();
      setCreating(false);
      setNewId(''); setNewName('');
      window.API.setWorkspaceId(newId);
      window.location.reload();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  return (
    <div style={{position: 'relative'}}>
      <button
        className="juris-pill tip"
        data-tip="Switch workspace"
        onClick={() => setOpen(o => !o)}
        style={{gap: 6}}
      >
        <Icon name="workspace" size={13}/>
        <span>{activeName}</span>
        <Icon name={open ? 'chevron_up' : 'chevron_down'} size={10}/>
      </button>

      {open && (
        <div
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', right: 0,
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 8, minWidth: 220, zIndex: 200,
            boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            padding: '6px 0',
          }}
          onClick={e => e.stopPropagation()}
        >
          {/* Default workspace row */}
          <div
            className={`nav-item ${activeId === null ? 'active' : ''}`}
            style={{padding: '7px 14px', cursor: 'pointer', fontSize: 13}}
            onClick={() => switchTo(null)}
          >
            My workspace
            {activeId === null && <span className="tag green" style={{marginLeft: 8, fontSize: 10}}>active</span>}
          </div>

          {/* Named workspaces */}
          {(workspaces ?? []).map(ws => (
            <div
              key={ws.workspace_id}
              className={`nav-item ${activeId === ws.workspace_id ? 'active' : ''}`}
              style={{padding: '7px 14px', cursor: 'pointer', fontSize: 13}}
              onClick={() => switchTo(ws.workspace_id)}
            >
              {ws.display_name}
              {activeId === ws.workspace_id && (
                <span className="tag green" style={{marginLeft: 8, fontSize: 10}}>active</span>
              )}
            </div>
          ))}

          <div style={{height: 1, background: 'var(--border)', margin: '6px 0'}}/>

          {/* Create new workspace */}
          {!creating ? (
            <div
              style={{padding: '7px 14px', cursor: 'pointer', fontSize: 13, color: 'var(--accent)'}}
              onClick={() => setCreating(true)}
            >
              <Icon name="plus" size={11}/> New workspace
            </div>
          ) : (
            <div style={{padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: 6}}>
              <input
                autoFocus
                placeholder="id (e.g. alice)"
                value={newId}
                onChange={e => setNewId(e.target.value.toLowerCase())}
                style={{
                  background: 'var(--surface-hover)', border: '1px solid var(--border)',
                  borderRadius: 4, padding: '4px 8px', fontSize: 12, color: 'var(--text)',
                  outline: 'none', width: '100%',
                }}
              />
              <input
                placeholder="Display name (e.g. Alice)"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                style={{
                  background: 'var(--surface-hover)', border: '1px solid var(--border)',
                  borderRadius: 4, padding: '4px 8px', fontSize: 12, color: 'var(--text)',
                  outline: 'none', width: '100%',
                }}
              />
              {error && <div style={{fontSize: 11, color: 'var(--red)'}}>{error}</div>}
              <div className="row" style={{gap: 6}}>
                <button className="btn primary sm" onClick={handleCreate}>Create</button>
                <button className="btn ghost sm" onClick={() => { setCreating(false); setError(''); }}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Close on outside click */}
      {open && (
        <div
          style={{position: 'fixed', inset: 0, zIndex: 199}}
          onClick={() => setOpen(false)}
        />
      )}
    </div>
  );
};
```

- [ ] **Step 2: Wire into Topbar**

In the `Topbar` component, add `<WorkspaceSwitcher/>` inside `<div className="topbar-right">` before the jurisdiction pill:

```jsx
<div className="topbar-right">
  <WorkspaceSwitcher/>
  <button className="juris-pill tip" ...>  {/* existing jurisdiction pill */}
```

- [ ] **Step 3: Add `workspace` icon to icons.jsx**

In `icons.jsx`, find the `Icon` component's icon map and add an entry for `"workspace"`. Use a simple grid/layers SVG path. Find wherever other icons like `"sources"` or `"portfolio"` are defined and add alongside:

```jsx
workspace: <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
  <rect x="2" y="3" width="16" height="4" rx="1"/>
  <rect x="2" y="9" width="7" height="4" rx="1"/>
  <rect x="11" y="9" width="7" height="4" rx="1"/>
  <rect x="2" y="15" width="16" height="2" rx="1"/>
</svg>,
```

If `icons.jsx` uses a different pattern (e.g. a switch/case or a string map), follow the existing pattern exactly — don't restructure the file.

- [ ] **Step 4: Copy to `ui/`**
```powershell
Copy-Item "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator\ALTANA_extracted\shell.jsx" `
          "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator\ui\shell.jsx" -Force
Copy-Item "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator\ALTANA_extracted\icons.jsx" `
          "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator\ui\icons.jsx" -Force
```

- [ ] **Step 5: Restart the orchestrator and verify**

Kill existing uvicorn process and restart:
```powershell
Get-Process python* | Where-Object {
  (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match "taxspine_orchestrator"
} | Stop-Process -Force

Start-Process -FilePath "uvicorn" `
  -ArgumentList "taxspine_orchestrator.main:app","--env-file",".env","--host","0.0.0.0","--port","8000" `
  -WorkingDirectory "%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator" `
  -NoNewWindow -PassThru
```

Open `http://localhost:8000/ui/` — the topbar should show a workspace pill. Click it: "My workspace" (default) + "New workspace". Create a workspace named `friend` with display name "Friend Name". Verify the switcher changes the active workspace and the pill label updates.

---

## Task 7: Commit UI and final smoke test

- [ ] **Step 1: Manual smoke test checklist**

With default workspace active:
- [ ] Dashboard KPIs show your data
- [ ] Portfolio shows your lots
- [ ] Sidebar nav badges reflect your review issues

Switch to the friend workspace:
- [ ] Dashboard KPIs show 404/empty (no sidecar yet for friend)
- [ ] Portfolio shows empty (no lots for friend)
- [ ] Review shows clean (no jobs yet for friend)
- [ ] A new job submitted while friend workspace is active is tagged `workspace_id=friend`

- [ ] **Step 2: Commit UI changes**
```
git add ALTANA_extracted/api.js ALTANA_extracted/shell.jsx ALTANA_extracted/icons.jsx
git add ui/api.js ui/shell.jsx ui/icons.jsx
git commit -m "feat: workspace switcher in topbar — select/create named workspaces from UI"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Workspace isolation (lots, dedup, workspace config) — Tasks 2, 3, 4
- ✅ Shared prices — prices endpoints untouched (price data is universal)
- ✅ UI workspace switcher — Task 6
- ✅ Create/name workspace — Tasks 1, 6
- ✅ Default workspace unchanged (backward compat) — `workspace_id=None` path preserved throughout
- ✅ Sidecar per-workspace — Task 4

**Placeholder scan:** None found. All steps contain exact file paths, code, and commands.

**Type consistency:**
- `workspace_id: str | None` used consistently in backend and UI
- `WorkspaceInfo.display_name: str` defined in Task 1, used in Task 6 (`ws.display_name`)
- `_wsParam()` defined in Task 5, used for all scoped calls
- `window.API.getWorkspaceId()` / `setWorkspaceId()` defined in Task 5, called in Task 6
