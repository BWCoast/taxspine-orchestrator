"""Dedup skip-log inspection endpoints.

Surfaces the per-source SQLite deduplication databases (under DEDUP_DIR) so
that operators can see what has been skipped during import without needing
shell access.

Endpoints
---------
GET /dedup/sources
    List all source slugs that have a dedup database on disk, with file
    metadata (size, last-modified).

GET /dedup/{source}/summary
    Return aggregate skip counts for a single source.  Optionally filtered
    by a ``since`` ISO 8601 UTC timestamp.

GET /dedup/{source}/entries
    Return individual skip log entries (most-recent first) for a source,
    with optional ``source_type``, ``since``, and ``limit`` filters.

Note: ``source`` in the URL corresponds to the slug naming convention used
by the command builders — e.g. ``generic_events``, ``firi_csv``,
``nor_multi``, ``xrpl_rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .config import settings

router = APIRouter(prefix="/dedup", tags=["dedup"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _db_path(source: str) -> Path:
    """Resolve a source slug to its on-disk SQLite path.

    SEC-02: allowlist-only sanitisation — any character outside ``[A-Za-z0-9_-]``
    is replaced with ``_``.  This is significantly stricter than the previous
    separator-only replacement, which still admitted ``..`` sequences that could
    resolve outside DEDUP_DIR after the separator characters were stripped.
    The resolved path is also asserted to remain inside DEDUP_DIR as a second
    line of defence against symlink traversal or other OS-level tricks.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", source)
    resolved = (settings.DEDUP_DIR / f"{safe}.db").resolve()
    # Belt-and-suspenders containment check.
    try:
        resolved.relative_to(settings.DEDUP_DIR.resolve())
    except ValueError:
        raise ValueError(
            f"Resolved dedup path {resolved!r} escapes DEDUP_DIR — "
            f"source slug {source!r} rejected"
        )
    return resolved


def _mtime_iso(path: Path) -> str:
    """Return the file's mtime as an ISO 8601 UTC string."""
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Sources list ──────────────────────────────────────────────────────────────


@router.get("/sources", summary="List all dedup source databases")
def list_dedup_sources() -> list[dict]:
    """Return metadata for every ``.db`` file in DEDUP_DIR.

    Each item contains:
    - ``source``        — slug name (basename without ``.db``)
    - ``db_path``       — absolute path on the server
    - ``size_bytes``    — file size in bytes
    - ``last_modified`` — ISO 8601 UTC timestamp of the last write
    """
    dedup_dir = settings.DEDUP_DIR
    if not dedup_dir.is_dir():
        return []

    results = []
    for db_file in sorted(dedup_dir.glob("*.db")):
        try:
            stat = db_file.stat()
            results.append({
                "source": db_file.stem,
                "db_path": str(db_file),
                "size_bytes": stat.st_size,
                "last_modified": _mtime_iso(db_file),
            })
        except OSError:
            # Race condition: file disappeared between glob and stat.
            pass

    return results


# ── Summary ───────────────────────────────────────────────────────────────────


@router.get("/{source}/summary", summary="Skip-log summary for a source")
def get_dedup_summary(
    source: str,
    since: Optional[str] = Query(
        default=None,
        description="Count only entries at or after this UTC timestamp (ISO 8601, e.g. 2026-01-01T00:00:00Z).",
    ),
) -> dict:
    """Return aggregate skip counts for *source*.

    - ``total_skips`` — number of dedup events recorded (optionally since *since*)
    - ``by_source``   — breakdown by ``source_type`` field stored in the skip log

    Returns ``{"db_exists": false}`` when no database file exists for this source
    yet (i.e. no events have ever been processed through this source).
    """
    db = _db_path(source)
    if not db.is_file():
        return {"source": source, "db_exists": False, "total_skips": 0, "by_source": {}}

    try:
        from tax_spine.ingestion.dedup_store import SkipLogReader
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"tax_spine package not available: {exc}",
        ) from exc

    try:
        with SkipLogReader(str(db)) as reader:
            summary = reader.summary_by_source(since=since)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read dedup store for '{source}': {exc}",
        ) from exc

    return {
        "source": source,
        "db_exists": True,
        "db_path": str(db),
        "since": since,
        "total_skips": summary.total_skips,
        "by_source": summary.by_source,
    }


# ── Entries list ──────────────────────────────────────────────────────────────


@router.get("/{source}/entries", summary="List skip-log entries for a source")
def list_dedup_entries(
    source: str,
    source_type: Optional[str] = Query(
        default=None,
        description="Filter by source_type stored in the skip log (e.g. 'firi_csv').",
    ),
    since: Optional[str] = Query(
        default=None,
        description="Return only entries at or after this UTC timestamp (ISO 8601).",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of entries to return (default 50, max 500).",
    ),
) -> list[dict]:
    """Return individual skip log entries for *source* (most-recent first).

    Each entry contains the dedup key, when it was skipped, and provenance of
    the original ingestion (first_seen_at, first_seen_batch_id, first_seen_event_id).

    Returns an empty list when the database does not exist.
    """
    db = _db_path(source)
    if not db.is_file():
        return []

    try:
        from tax_spine.ingestion.dedup_store import SkipLogReader
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"tax_spine package not available: {exc}",
        ) from exc

    try:
        with SkipLogReader(str(db)) as reader:
            entries = reader.list_skips(
                source_type=source_type,
                since=since,
                limit=limit,
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read dedup store for '{source}': {exc}",
        ) from exc

    return [
        {
            "dedup_key": e.dedup_key,
            "skipped_at": e.skipped_at,
            "source_type": e.source_type,
            "import_batch_id": e.import_batch_id,
            "first_seen_at": e.first_seen_at,
            "first_seen_batch_id": e.first_seen_batch_id,
            "first_seen_event_id": e.first_seen_event_id,
        }
        for e in entries
    ]
