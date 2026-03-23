"""Review console endpoints — cross-job unresolved item aggregation.

Provides a year-level view of all review issues flagged across completed Norway
jobs, plus a per-job drill-down for the full Review Console.

Endpoints
---------
GET /review/summary?year=N
    Aggregated review state for tax year N.  Merges:
    - Unlinked-transfer flags from all completed Norway jobs for that year.
    - Warnings from all completed Norway jobs (deduplicated, categorised).
    - Missing-basis assets from the lot persistence store (with lot-count detail).

GET /review/jobs?year=N
    Per-job review data for all completed Norway jobs in year N.  Returns job
    metadata, inline review summary, and available download links so the UI can
    render per-job cards without a separate fetch per job.
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path

from fastapi import APIRouter, Query

from .config import settings

router = APIRouter(prefix="/review", tags=["review"])
_log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_review_json(path: str) -> dict | None:
    """Read and parse a single review JSON file.  Returns None on any error."""
    try:
        return _json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("review: could not read %s: %s", path, exc)
        return None


def _missing_basis_detail(year: int) -> list[dict]:
    """Return per-asset missing-basis detail for *year* from the lot store.

    Each entry: ``{"asset": str, "lot_count": int, "missing_lots": int,
    "total_remaining_qty": str}``.  Sorted by asset symbol.
    Returns ``[]`` when the store is absent or has no data for *year*.
    """
    try:
        from tax_spine.pipeline.lot_store import LotPersistenceStore  # noqa: PLC0415
    except ImportError:
        return []

    db_path = settings.LOT_STORE_DB
    if not db_path.is_file():
        return []

    try:
        store = LotPersistenceStore(str(db_path))
        with store:
            if year not in store.list_years():
                return []
            lots = store.load_carry_forward(year)
    except Exception as exc:  # noqa: BLE001
        _log.warning("review: could not read lot store for %d: %s", year, exc)
        return []

    from decimal import Decimal  # noqa: PLC0415

    # Per-asset accumulators: {asset: {"lot_count", "missing_lots", "total_qty"}}
    agg: dict[str, dict] = {}
    for lot in lots:
        a = lot.asset
        if a not in agg:
            agg[a] = {"lot_count": 0, "missing_lots": 0, "total_qty": Decimal("0")}
        agg[a]["lot_count"] += 1
        agg[a]["total_qty"] += lot.remaining_quantity
        if lot.remaining_cost_basis_nok is None:
            agg[a]["missing_lots"] += 1

    return [
        {
            "asset":                  a,
            "lot_count":              v["lot_count"],
            "missing_lots":           v["missing_lots"],
            "total_remaining_qty":    str(v["total_qty"]),
            "has_missing_basis":      v["missing_lots"] > 0,
        }
        for a, v in sorted(agg.items())
        if v["missing_lots"] > 0
    ]


def _missing_basis_assets(year: int) -> list[str]:
    """Return asset symbols with missing basis (backward-compat wrapper)."""
    return [d["asset"] for d in _missing_basis_detail(year)]


# Warning category keywords — checked in order; first match wins.
_WARNING_CATEGORIES: list[tuple[str, list[str]]] = [
    ("Tax Law",          ["TL-", "peg", "partial year", "UK tax year"]),
    ("Transfer Linking", ["unlinked", "transfer", "TRANSFER"]),
    ("Cost Basis",       ["basis", "UNRESOLVED", "missing cost", "cost basis"]),
    ("Income",           ["income", "reward", "airdrop", "staking"]),
    ("Valuation",        ["valuat", "price", "NOK", "GBP", "USD"]),
]
_DEFAULT_CATEGORY = "General"


def _categorize_warnings(warnings: list[str]) -> dict[str, list[str]]:
    """Group *warnings* into named categories for display.

    Returns an ordered dict ``{category_name: [warning_str, ...]}``.
    Category order matches ``_WARNING_CATEGORIES`` + "General" last.
    Empty categories are omitted.
    """
    buckets: dict[str, list[str]] = {}
    for w in warnings:
        placed = False
        for cat, keywords in _WARNING_CATEGORIES:
            if any(kw.lower() in w.lower() for kw in keywords):
                buckets.setdefault(cat, []).append(w)
                placed = True
                break
        if not placed:
            buckets.setdefault(_DEFAULT_CATEGORY, []).append(w)
    return buckets


def _job_review_summary(job) -> dict:
    """Read review JSON files for *job* and return a merged review dict."""
    paths: list[str] = job.output.review_json_paths or (
        [job.output.review_json_path] if job.output.review_json_path else []
    )
    all_warnings: list[str] = []
    has_unlinked = False
    loaded = 0
    for p in paths:
        data = _load_review_json(p)
        if data is None:
            continue
        loaded += 1
        all_warnings.extend(data.get("warnings", []))
        if data.get("has_unlinked_transfers"):
            has_unlinked = True
    return {
        "has_unlinked_transfers": has_unlinked,
        "warning_count":          len(all_warnings),
        "warnings":               all_warnings,
        "clean":                  not all_warnings and not has_unlinked,
        "source_count":           loaded,
    }


def _job_downloads(job) -> dict:
    """Return available download metadata for *job*.

    Keys:
    - ``html_report_count``  — number of HTML reports produced
    - ``rf1159_count``       — number of RF-1159 JSON files produced
    - ``has_review_json``    — True when at least one review JSON exists
    """
    html_paths  = job.output.report_html_paths or (
        [job.output.report_html_path] if job.output.report_html_path else []
    )
    rf1159_paths = job.output.rf1159_json_paths or (
        [job.output.rf1159_json_path] if job.output.rf1159_json_path else []
    )
    review_paths = job.output.review_json_paths or (
        [job.output.review_json_path] if job.output.review_json_path else []
    )
    return {
        "html_report_count": len(html_paths),
        "rf1159_count":      len(rf1159_paths),
        "has_review_json":   bool(review_paths),
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/summary", summary="Aggregated review summary for a tax year")
def get_review_summary(
    year: int = Query(..., ge=2009, le=2100, description="Tax year to summarise."),
) -> dict:
    """Return an aggregated review state for all completed Norway jobs in *year*.

    Scans every COMPLETED Norway job whose ``input.tax_year`` matches *year*,
    reads each job's review JSON file(s), and merges the results.  Also reads
    the lot persistence store to report assets with missing cost basis.

    Response fields:

    - ``tax_year``                — the requested year
    - ``jobs_with_review``        — number of review JSON files successfully read
    - ``has_unlinked_transfers``  — True when any job flagged unlinked transfers
    - ``unlinked_transfer_jobs``  — list of ``{job_id, case_name}`` dicts
    - ``total_warnings``          — total warning count (deduplicated)
    - ``warnings``                — flat deduplicated list of warning strings
    - ``warnings_by_category``    — warnings grouped by category name
    - ``missing_basis_assets``    — asset symbols with ≥1 missing-basis lot
    - ``missing_basis_detail``    — per-asset detail (lot_count, missing_lots, qty)
    - ``missing_basis_count``     — len(missing_basis_assets)
    - ``clean``                   — True when no warnings, unlinked transfers, or missing basis
    """
    from .storage import SqliteJobStore  # noqa: PLC0415
    from .models import JobStatus, Country  # noqa: PLC0415

    db_path = settings.DATA_DIR / "jobs.db"
    if not db_path.is_file():
        return _empty_summary(year)

    store = SqliteJobStore(db_path)
    all_jobs = store.list(
        status=JobStatus.COMPLETED,
        country=Country.NORWAY,
        query=None,
        limit=10_000,
        offset=0,
    )
    year_jobs = [j for j in all_jobs if j.input.tax_year == year]

    all_warnings: list[str] = []
    unlinked_jobs: list[dict] = []
    loaded_count = 0
    seen_unlinked: set[str] = set()

    for job in year_jobs:
        paths: list[str] = job.output.review_json_paths or (
            [job.output.review_json_path] if job.output.review_json_path else []
        )
        for p in paths:
            data = _load_review_json(p)
            if data is None:
                continue
            loaded_count += 1
            all_warnings.extend(data.get("warnings", []))
            if data.get("has_unlinked_transfers") and job.id not in seen_unlinked:
                seen_unlinked.add(job.id)
                unlinked_jobs.append({
                    "job_id":    job.id,
                    "case_name": job.input.case_name or job.id,
                })

    # Deduplicate warnings while preserving first-seen order.
    seen: dict[str, None] = {}
    for w in all_warnings:
        seen[w] = None
    deduped_warnings = list(seen)

    missing_detail = _missing_basis_detail(year)
    missing_assets = [d["asset"] for d in missing_detail]

    clean = not unlinked_jobs and not deduped_warnings and not missing_assets

    return {
        "tax_year":               year,
        "jobs_with_review":       loaded_count,
        "has_unlinked_transfers": bool(unlinked_jobs),
        "unlinked_transfer_jobs": unlinked_jobs,
        "total_warnings":         len(deduped_warnings),
        "warnings":               deduped_warnings,
        "warnings_by_category":   _categorize_warnings(deduped_warnings),
        "missing_basis_assets":   missing_assets,
        "missing_basis_detail":   missing_detail,
        "missing_basis_count":    len(missing_assets),
        "clean":                  clean,
    }


@router.get("/jobs", summary="Per-job review data for all Norway jobs in a tax year")
def get_review_jobs(
    year: int = Query(..., ge=2009, le=2100, description="Tax year to list jobs for."),
) -> dict:
    """Return per-job review data for all completed Norway jobs in *year*.

    Each entry includes job metadata, an inline review summary (read from the
    job's review JSON files), and download availability flags so the UI can
    render job cards without a per-job fetch.

    Response fields:

    - ``tax_year``  — the requested year
    - ``jobs``      — list of job card dicts, newest-first

    Each job dict:

    - ``job_id``           — unique job identifier
    - ``case_name``        — human-readable label (or job_id when absent)
    - ``created_at``       — ISO-8601 creation timestamp
    - ``pipeline_mode``    — ``"per_file"`` or ``"nor_multi"``
    - ``valuation_mode``   — ``"price_table"``, ``"dummy"``, etc.
    - ``csv_file_count``   — number of CSV files processed
    - ``xrpl_accounts``    — list of XRPL account addresses (may be empty)
    - ``review``           — inline review summary (has_unlinked_transfers, warnings, …)
    - ``downloads``        — available download counts (html_report_count, rf1159_count, …)
    - ``has_review_data``  — True when at least one review JSON was readable
    """
    from .storage import SqliteJobStore  # noqa: PLC0415
    from .models import JobStatus, Country  # noqa: PLC0415

    db_path = settings.DATA_DIR / "jobs.db"
    if not db_path.is_file():
        return {"tax_year": year, "jobs": []}

    store = SqliteJobStore(db_path)
    all_jobs = store.list(
        status=JobStatus.COMPLETED,
        country=Country.NORWAY,
        query=None,
        limit=10_000,
        offset=0,
    )
    year_jobs = [j for j in all_jobs if j.input.tax_year == year]
    # Already newest-first from store.list (ORDER BY created_at DESC).

    result = []
    for job in year_jobs:
        review = _job_review_summary(job)
        downloads = _job_downloads(job)
        result.append({
            "job_id":         job.id,
            "case_name":      job.input.case_name or job.id,
            "created_at":     job.created_at.isoformat() if job.created_at else None,
            "pipeline_mode":  job.input.pipeline_mode.value if job.input.pipeline_mode else "per_file",
            "valuation_mode": job.input.valuation_mode.value if job.input.valuation_mode else "unknown",
            "csv_file_count": len(job.input.csv_files or []),
            "xrpl_accounts":  job.input.xrpl_accounts or [],
            "review":         review,
            "downloads":      downloads,
            "has_review_data": review["source_count"] > 0,
        })

    return {"tax_year": year, "jobs": result}


def _empty_summary(year: int) -> dict:
    """Return a clean empty summary (no DB, no jobs, no lots)."""
    return {
        "tax_year":               year,
        "jobs_with_review":       0,
        "has_unlinked_transfers": False,
        "unlinked_transfer_jobs": [],
        "total_warnings":         0,
        "warnings":               [],
        "warnings_by_category":   {},
        "missing_basis_assets":   [],
        "missing_basis_detail":   [],
        "missing_basis_count":    0,
        "clean":                  True,
    }
