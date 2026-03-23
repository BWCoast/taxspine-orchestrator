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

GET /lots/{year}/portfolio
    Per-asset aggregated holdings.  Accepts ``?include_prices=true`` to
    enrich each asset entry with year-end NOK market value and unrealised
    gain/loss derived from the cached price CSV.
"""

from __future__ import annotations

import csv
import datetime
import logging

from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query

from .config import settings

_log = logging.getLogger(__name__)

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


# ── Year-end price loading ─────────────────────────────────────────────────────


def _load_year_end_prices(year: int) -> dict[str, Decimal]:
    """Load NOK prices for *year* from the cached combined price CSV.

    Reads ``combined_nok_{year}.csv`` from ``settings.PRICES_DIR``.  For each
    asset, uses the Dec 31 price when available, otherwise the latest date
    present in the file (useful for the current year where Dec 31 has not
    yet occurred).

    Returns a plain ``{asset_symbol: price_nok}`` dict.
    Returns ``{}`` on any error (missing file, parse error, etc.).
    """
    csv_path = settings.PRICES_DIR / f"combined_nok_{year}.csv"
    if not csv_path.is_file():
        return {}

    target_date = f"{year}-12-31"
    # Two-pass: collect all rows, then select the best date per asset.
    # {asset: {date_str: price}}
    rows_by_asset: dict[str, dict[str, Decimal]] = {}

    try:
        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                asset = (row.get("asset_id") or "").strip().upper()
                date  = (row.get("date") or "").strip()
                price_str = (row.get("price_fiat") or "").strip()
                if not asset or not date or not price_str:
                    continue
                try:
                    price = Decimal(price_str)
                except Exception:  # noqa: BLE001
                    continue
                if asset not in rows_by_asset:
                    rows_by_asset[asset] = {}
                rows_by_asset[asset][date] = price
    except Exception as exc:  # noqa: BLE001
        _log.warning("lots: could not read price CSV %s: %s", csv_path, exc)
        return {}

    # Select Dec 31 when available; fall back to the latest date in the file.
    result: dict[str, Decimal] = {}
    for asset, dates in rows_by_asset.items():
        if target_date in dates:
            result[asset] = dates[target_date]
        elif dates:
            result[asset] = dates[max(dates)]

    return result


# ── Portfolio (per-asset aggregated holdings) ─────────────────────────────────


@router.get("/{year}/portfolio", summary="Per-asset holdings portfolio for a tax year")
def get_portfolio(
    year: int,
    include_prices: bool = Query(
        default=False,
        description=(
            "When true, enrich each asset entry with NOK market value "
            "and unrealised gain/loss.  Use ``price_type`` to control the price source."
        ),
    ),
    price_type: str = Query(
        default="year_end",
        description=(
            "Price source when ``include_prices=true``. "
            "``year_end`` (default) reads Dec 31 prices from the cached "
            "``combined_nok_{year}.csv`` file. "
            "``current`` fetches live spot prices from Kraken Ticker × Norges Bank "
            "(Tier-1 assets only: BTC, ETH, XRP, ADA, LTC; 5-minute cache)."
        ),
    ),
) -> dict:
    """Return per-asset aggregated holdings from carry-forward lots.

    Aggregates all active (remaining_quantity > 0) lots for *year* into one
    row per asset.  Useful as a portfolio snapshot before running the next
    tax year — shows what positions are being carried forward, their total
    quantity, and total cost basis.

    Each asset entry includes:

    - ``asset``                — asset symbol (e.g. "BTC")
    - ``lot_count``            — number of active lots
    - ``total_quantity``       — sum of remaining_quantity (string, full precision)
    - ``total_cost_basis_nok`` — sum of remaining_cost_basis_nok for lots that
                                  have a resolved basis (partial when
                                  ``has_missing_basis`` is true)
    - ``avg_cost_nok_per_unit``— total_cost_basis_nok / total_quantity (nullable)
    - ``has_missing_basis``    — true when one or more lots lack a cost basis

    When ``include_prices=true`` the following fields are added:

    - ``year_end_price_nok``   — Dec 31 (or latest) NOK price per unit (nullable)
    - ``market_value_nok``     — total_quantity × year_end_price (nullable)
    - ``unrealized_gain_nok``  — market_value − cost_basis (nullable)
    - ``has_missing_price``    — true when no price is available for this asset

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
            lots = store.load_carry_forward(year)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read portfolio for {year}: {exc}",
        ) from exc

    # Optionally load prices — source depends on price_type.
    prices: dict[str, Decimal] = {}
    prices_as_of: str | None = None
    if include_prices:
        if price_type == "current":
            from .prices import fetch_spot_prices_nok as _fetch_spot
            all_assets = [lot.asset for lot in lots]
            try:
                prices, prices_as_of = _fetch_spot(all_assets)
            except Exception as exc:  # noqa: BLE001
                _log.warning("lots: could not fetch live spot prices: %s", exc)
                # Fall back to year-end prices so the panel still shows data
                prices = _load_year_end_prices(year)
                prices_as_of = f"{year}-12-31T00:00:00+00:00"
        else:
            prices = _load_year_end_prices(year)
            prices_as_of = f"{year}-12-31T00:00:00+00:00"

    # Aggregate per asset — accumulate resolved basis separately from total qty.
    aggregates: dict[str, dict] = {}
    for lot in lots:
        if lot.asset not in aggregates:
            aggregates[lot.asset] = {
                "asset": lot.asset,
                "lot_count": 0,
                "total_quantity": Decimal("0"),
                "total_cost_basis_nok": Decimal("0"),
                "has_missing_basis": False,
            }
        agg = aggregates[lot.asset]
        agg["lot_count"] += 1
        agg["total_quantity"] += lot.remaining_quantity
        if lot.remaining_cost_basis_nok is not None:
            agg["total_cost_basis_nok"] += lot.remaining_cost_basis_nok
        else:
            agg["has_missing_basis"] = True

    # Build sorted output list.
    result = []
    for key in sorted(aggregates):
        agg    = aggregates[key]
        qty    = agg["total_quantity"]
        basis  = agg["total_cost_basis_nok"]
        avg    = (basis / qty).quantize(Decimal("0.01")) if qty > 0 else None

        entry: dict = {
            "asset":                 agg["asset"],
            "lot_count":             agg["lot_count"],
            "total_quantity":        str(qty),
            "total_cost_basis_nok":  str(basis),
            "avg_cost_nok_per_unit": str(avg) if avg is not None else None,
            "has_missing_basis":     agg["has_missing_basis"],
        }

        if include_prices:
            symbol = agg["asset"]
            price  = prices.get(symbol)
            if price is not None and qty > 0:
                market_val       = (qty * price).quantize(Decimal("0.01"))
                unrealized_gain  = (market_val - basis).quantize(Decimal("0.01"))
                entry["year_end_price_nok"]  = str(price)  # kept for backward compat
                entry["price_nok"]           = str(price)  # canonical alias
                entry["market_value_nok"]    = str(market_val)
                entry["unrealized_gain_nok"] = str(unrealized_gain)
                entry["has_missing_price"]   = False
            else:
                entry["year_end_price_nok"]  = None
                entry["price_nok"]           = None
                entry["market_value_nok"]    = None
                entry["unrealized_gain_nok"] = None
                entry["has_missing_price"]   = True

        result.append(entry)

    response: dict = {
        "tax_year":    year,
        "asset_count": len(result),
        "assets":      result,
        "price_type":  price_type if include_prices else None,
        "prices_as_of": prices_as_of,
    }

    if include_prices:
        # Aggregate market totals — only assets with resolved prices.
        priced = [r for r in result if not r.get("has_missing_price")]
        if priced:
            total_market = sum(Decimal(r["market_value_nok"]) for r in priced)
            total_basis  = sum(Decimal(r["total_cost_basis_nok"]) for r in priced)
            response["total_market_value_nok"]   = str(total_market.quantize(Decimal("0.01")))
            response["total_unrealized_gain_nok"] = str((total_market - total_basis).quantize(Decimal("0.01")))
            response["prices_partial"] = len(priced) < len(result)
        else:
            response["total_market_value_nok"]    = None
            response["total_unrealized_gain_nok"]  = None
            response["prices_partial"]             = bool(result)

    return response
