"""FIFO lot store inspection endpoints.

Provides read-only visibility into the year-end lot snapshots stored by the
Norway pipeline.  Useful for verifying carry-forward state before running a
new tax year, and for auditing historical cost basis.

Endpoints
---------
GET /lots/years
    List calendar years that have saved lot snapshots.

GET /lots/{year}
    Summary for a specific year: total lot count, active vs depleted,
    per-asset breakdown.

GET /lots/{year}/carry-forward
    The actual carry-forward lots (remaining_quantity > 0) that would be
    fed into the FIFO engine as ``initial_lots`` for year+1.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .config import settings

router = APIRouter(prefix="/lots", tags=["lots"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _open_store():
    """Open a read-only connection to the lot persistence store."""
    try:
        from tax_spine.pipeline.lot_store import LotPersistenceStore
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"tax_spine package not available: {exc}",
        ) from exc

    db_path = settings.LOT_STORE_DB
    if not db_path.is_file():
        return None  # store not yet initialised

    return LotPersistenceStore(str(db_path))


# ── Years list ────────────────────────────────────────────────────────────────


@router.get("/years", summary="List years with saved lot snapshots")
def list_lot_years() -> dict:
    """Return the calendar years for which FIFO lot snapshots are stored.

    An empty ``years`` list means no tax runs have completed yet (or the lot
    store path is new / never written to).
    """
    store = _open_store()
    if store is None:
        return {"years": [], "db_exists": False}

    try:
        with store:
            years = store.list_years()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read lot store: {exc}",
        ) from exc

    return {"years": years, "db_exists": True, "db_path": str(settings.LOT_STORE_DB)}


# ── Year summary ──────────────────────────────────────────────────────────────


@router.get("/{year}", summary="Lot snapshot summary for a tax year")
def get_lot_year_summary(year: int) -> dict:
    """Return a summary of the lot snapshot for *year*.

    Includes:
    - ``total_lots``    — all lots saved (active + depleted)
    - ``active_lots``   — lots with remaining_quantity > 0 (carry-forward eligible)
    - ``depleted_lots`` — fully consumed lots (remaining_quantity == 0)
    - ``assets``        — per-asset breakdown of total / active lot counts

    Raises 404 when no snapshot exists for *year*.
    """
    store = _open_store()
    if store is None:
        raise HTTPException(
            status_code=404,
            detail=f"No lot store found at {settings.LOT_STORE_DB}",
        )

    try:
        with store:
            years = store.list_years()
            if year not in years:
                raise HTTPException(
                    status_code=404,
                    detail=f"No lot snapshot found for tax year {year}. "
                           f"Available years: {years or 'none'}",
                )
            lots = store.load_all_lots(year)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read lot store for {year}: {exc}",
        ) from exc

    # Aggregate stats.
    active = [lot for lot in lots if lot.remaining_quantity > 0]
    depleted = [lot for lot in lots if lot.remaining_quantity == 0]

    assets: dict[str, dict[str, int]] = {}
    for lot in lots:
        if lot.asset not in assets:
            assets[lot.asset] = {"total_lots": 0, "active_lots": 0}
        assets[lot.asset]["total_lots"] += 1
        if lot.remaining_quantity > 0:
            assets[lot.asset]["active_lots"] += 1

    return {
        "tax_year": year,
        "total_lots": len(lots),
        "active_lots": len(active),
        "depleted_lots": len(depleted),
        "assets": assets,
    }


# ── Carry-forward lots ────────────────────────────────────────────────────────


@router.get("/{year}/carry-forward", summary="Carry-forward lots for a tax year")
def get_carry_forward_lots(year: int) -> list[dict]:
    """Return the lots with remaining inventory from *year*'s snapshot.

    These are exactly the lots that would be passed as ``initial_lots`` to
    ``fifo_run()`` when processing tax year *year+1*.  An empty list means
    all positions were fully disposed of during *year* (or the snapshot does
    not exist).

    Each lot includes:
    - ``lot_id``, ``asset``, ``acquired_timestamp``
    - ``original_quantity``, ``remaining_quantity`` (as strings, full Decimal precision)
    - ``basis_status`` — ``"resolved"`` or ``"missing"``
    - ``original_cost_basis_nok``, ``remaining_cost_basis_nok`` (nullable strings)
    """
    store = _open_store()
    if store is None:
        raise HTTPException(
            status_code=404,
            detail=f"No lot store found at {settings.LOT_STORE_DB}",
        )

    try:
        with store:
            years = store.list_years()
            if year not in years:
                raise HTTPException(
                    status_code=404,
                    detail=f"No lot snapshot found for tax year {year}. "
                           f"Available years: {years or 'none'}",
                )
            lots = store.load_carry_forward(year)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read carry-forward lots for {year}: {exc}",
        ) from exc

    return [
        {
            "lot_id": lot.lot_id,
            "asset": lot.asset,
            "acquired_timestamp": lot.acquired_timestamp,
            "origin_event_id": lot.origin_event_id,
            "origin_type": lot.origin_type,
            "original_quantity": str(lot.original_quantity),
            "remaining_quantity": str(lot.remaining_quantity),
            "original_cost_basis_nok": (
                str(lot.original_cost_basis_nok)
                if lot.original_cost_basis_nok is not None
                else None
            ),
            "remaining_cost_basis_nok": (
                str(lot.remaining_cost_basis_nok)
                if lot.remaining_cost_basis_nok is not None
                else None
            ),
            "basis_status": lot.basis_status,
        }
        for lot in lots
    ]
