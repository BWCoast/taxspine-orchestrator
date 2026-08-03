"""Named workspace CRUD router.

Each named workspace provides full data isolation: separate FIFO lot store,
separate dedup directory, and separate workspace config.  The default workspace
(no workspace_id) uses the global paths from settings.
"""

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

# Workspace ID validation — same pattern as JobInput.workspace_id
_WORKSPACE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,37}$")


# ── Response model ────────────────────────────────────────────────────────────


class WorkspaceInfo(BaseModel):
    """Summary of a named workspace's existence and data on disk."""

    workspace_id: str
    display_name: str
    lots_db_exists: bool
    dedup_dir_exists: bool
    workspace_json_exists: bool


# ── Helpers ───────────────────────────────────────────────────────────────────


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
    tmp = ws_dir / "meta.json.tmp"
    tmp.write_text(_json.dumps({"display_name": display_name}, indent=2), encoding="utf-8")
    tmp.replace(meta)


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


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=list[WorkspaceInfo])
def list_workspaces() -> list[WorkspaceInfo]:
    """List all named workspaces found under DATA_DIR/workspaces/.

    Returns an empty list when no named workspaces exist.  The default
    workspace (jobs with workspace_id=None) is not listed here.
    """
    ws_root = settings.DATA_DIR / "workspaces"
    if not ws_root.is_dir():
        return []
    result: list[WorkspaceInfo] = []
    for ws_dir in sorted(ws_root.iterdir()):
        if not ws_dir.is_dir():
            continue
        if not _WORKSPACE_ID_RE.match(ws_dir.name):
            continue  # skip directories that don't look like workspace IDs
        result.append(_workspace_info(ws_dir.name))
    return result


class CreateWorkspaceRequest(BaseModel):
    workspace_id: str = Field(
        pattern=r"^[a-z][a-z0-9_-]{0,37}$",
        description=(
            "Must start with a lowercase letter; may contain lowercase letters, "
            "digits, hyphens, and underscores; max 38 characters."
        ),
    )
    display_name: Optional[str] = Field(default=None)


@router.post("", response_model=WorkspaceInfo, status_code=201)
def create_workspace(body: CreateWorkspaceRequest) -> WorkspaceInfo:
    """Create a new named workspace directory under DATA_DIR/workspaces/.

    The workspace directory is created immediately.  Subdirectories (dedup/,
    lots.db) are created lazily when the first job runs in this workspace.

    Returns HTTP 409 if the workspace already exists.
    """
    ws_dir = _workspace_dir(body.workspace_id)
    if ws_dir.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Workspace '{body.workspace_id}' already exists",
        )
    ws_dir.mkdir(parents=True, exist_ok=True)
    name = body.display_name or body.workspace_id
    _write_display_name(ws_dir, name)
    return _workspace_info(body.workspace_id)


@router.get("/{workspace_id}", response_model=WorkspaceInfo)
def get_workspace(workspace_id: str) -> WorkspaceInfo:
    """Return info about a named workspace.

    Returns HTTP 404 if the workspace directory does not exist.
    """
    if not _WORKSPACE_ID_RE.match(workspace_id):
        raise HTTPException(status_code=422, detail="Invalid workspace_id format")
    ws_dir = _workspace_dir(workspace_id)
    if not ws_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Workspace '{workspace_id}' not found",
        )
    return _workspace_info(workspace_id)


@router.delete("/{workspace_id}", status_code=204)
def delete_workspace(
    workspace_id: str,
    delete_data: bool = Query(
        default=False,
        description=(
            "When true, permanently delete all workspace data on disk "
            "(lots.db, dedup/, workspace.json).  When false (default), "
            "only remove the workspace directory if it is empty."
        ),
    ),
) -> None:
    """Delete a named workspace.

    When ``delete_data`` is False (default) the directory is only removed if
    empty — this is a soft delete that prevents accidental data loss.  Pass
    ``delete_data=true`` to permanently erase all workspace data (lots,
    dedup records, and workspace config).

    Returns HTTP 404 if the workspace directory does not exist.
    """
    if not _WORKSPACE_ID_RE.match(workspace_id):
        raise HTTPException(status_code=422, detail="Invalid workspace_id format")
    ws_dir = _workspace_dir(workspace_id)
    if not ws_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Workspace '{workspace_id}' not found",
        )
    if delete_data:
        shutil.rmtree(ws_dir)
    else:
        try:
            ws_dir.rmdir()  # fails if not empty → safe default
        except OSError:
            pass  # directory not empty — leave data intact
