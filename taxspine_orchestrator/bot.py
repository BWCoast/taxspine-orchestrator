"""Bot DuckDB sync — pull trading fills and LP events into the generic-events pipeline.

The trading bot writes completed fills and LP events to its own DuckDB database.
This module provides:

- ``BotDuckDbSyncWorker``: reads ``bot.duckdb`` (read-only), maps rows to the
  generic-events CSV schema, and writes them to ``DATA_DIR/bot_generic_events.csv``
  for the next tax run.
- ``POST /bot/sync``: trigger a sync manually (or via scheduler).
- ``GET /bot/sync/status``: last sync result.
- ``GET /bot/lot-impact/{symbol}``: check whether a symbol has prior FIFO lots
  before adding it to the trading whitelist.

Architecture note (Norwegian tax law)
--------------------------------------
Norway uses a **single FIFO pool per asset** across ALL accounts and strategies.
If you already hold BTC from personal buys, the bot's first BTC purchase creates a
new lot that shares the same pool.  The bot's first BTC sell will consume the
**oldest personal lot**, not the bot entry price.

The ``GET /bot/lot-impact/{symbol}`` endpoint surfaces this before you whitelist a
token for bot trading.  Record the response in the bot's
``token_whitelist.had_prior_lots`` column as a permanent audit trail.

LP event representation
-----------------------
LP events carry ``complex_tax_treatment=True`` because they involve AMM pool
shares that require manual FMV confirmation in the review queue.  Two-sided events
encode the secondary asset and amount in the ``note`` field
(``asset_b:<symbol>:<amount>``) so the tax core receives a complete picture via a
single generic-events row, which the reviewer can split if needed.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from .config import settings

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/bot", tags=["bot"])


# ── DuckDB optional dependency ────────────────────────────────────────────────

try:
    import duckdb as _duckdb  # type: ignore[import]
    _DUCKDB_AVAILABLE = True
except ImportError:
    _duckdb = None  # type: ignore[assignment]
    _DUCKDB_AVAILABLE = False


# ── Generic-events CSV column order ──────────────────────────────────────────
# Must match tax_spine.importers.generic_csv exactly.

_GENERIC_EVENTS_COLUMNS = [
    "event_id",
    "timestamp",
    "event_type",
    "source",
    "account",
    "asset_in",
    "amount_in",
    "asset_out",
    "amount_out",
    "fee_asset",
    "fee_amount",
    "tx_hash",
    "exchange_tx_id",
    "label",
    "complex_tax_treatment",
    "note",
]


# ── Result type ───────────────────────────────────────────────────────────────


@dataclasses.dataclass
class BotSyncResult:
    """Result of a single bot sync operation."""

    synced_at: str
    fills_written: int
    lp_events_written: int
    total_events: int
    output_path: str
    error: Optional[str] = None


# Module-level cache — survives across requests, reset on server restart.
_last_sync_result: Optional[BotSyncResult] = None


# ── Worker ────────────────────────────────────────────────────────────────────


class BotDuckDbSyncWorker:
    """Read-only DuckDB reader that maps bot trades to the generic-events CSV.

    Parameters
    ----------
    db_path:
        Path to the bot's DuckDB database file.  Opened read-only so the
        worker never modifies the bot's own data.
    output_path:
        Destination CSV path.  Overwritten on each sync (**snapshot model**).
        Idempotency is handled upstream by the orchestrator's dedup store
        (Tier-1 key = ``fill_id`` / ``event_id``).
    """

    def __init__(self, db_path: str | Path, output_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._output_path = Path(output_path)

    # ── Public ────────────────────────────────────────────────────────────

    def sync(self) -> BotSyncResult:
        """Pull fills + LP events and overwrite the generic-events CSV."""
        synced_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        try:
            con = _duckdb.connect(str(self._db_path), read_only=True)
        except Exception as exc:  # noqa: BLE001
            return BotSyncResult(
                synced_at=synced_at,
                fills_written=0,
                lp_events_written=0,
                total_events=0,
                output_path=str(self._output_path),
                error=f"Could not open DuckDB: {exc}",
            )

        try:
            fill_rows = self._fetch_fills(con)
            lp_rows = self._fetch_lp_events(con)
        except Exception as exc:  # noqa: BLE001
            con.close()
            return BotSyncResult(
                synced_at=synced_at,
                fills_written=0,
                lp_events_written=0,
                total_events=0,
                output_path=str(self._output_path),
                error=f"Query error: {exc}",
            )
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

        fill_events = self._fill_to_events(fill_rows)
        lp_events = self._lp_to_events(lp_rows)
        all_events = fill_events + lp_events

        try:
            self._write_csv(all_events)
        except Exception as exc:  # noqa: BLE001
            return BotSyncResult(
                synced_at=synced_at,
                fills_written=len(fill_events),
                lp_events_written=len(lp_events),
                total_events=len(all_events),
                output_path=str(self._output_path),
                error=f"CSV write error: {exc}",
            )

        return BotSyncResult(
            synced_at=synced_at,
            fills_written=len(fill_events),
            lp_events_written=len(lp_events),
            total_events=len(all_events),
            output_path=str(self._output_path),
        )

    # ── DuckDB fetch helpers ──────────────────────────────────────────────

    @staticmethod
    def _fetch_fills(con: Any) -> list[dict]:
        """Fetch all rows from the ``fills`` table, ordered by execution time."""
        sql = """
            SELECT fill_id, strategy, venue, chain_id,
                   asset_in, amount_in, asset_out, amount_out,
                   fee_asset, fee_amount, tx_hash, order_id, executed_at
            FROM fills
            ORDER BY executed_at
        """
        cursor = con.execute(sql)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    @staticmethod
    def _fetch_lp_events(con: Any) -> list[dict]:
        """Fetch all rows from the ``lp_events`` table, ordered by execution time."""
        sql = """
            SELECT event_id, lp_action, pool_address, venue, chain_id,
                   asset_a, amount_a, asset_b, amount_b,
                   lp_token, lp_amount, tx_hash, executed_at
            FROM lp_events
            ORDER BY executed_at
        """
        cursor = con.execute(sql)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    # ── Mapping helpers ───────────────────────────────────────────────────

    @staticmethod
    def _fill_to_events(fills: list[dict]) -> list[dict]:
        """Map bot fill rows to generic-events rows.

        Each fill becomes one ``TRADE`` event.  The strategy name is preserved
        in the ``note`` field so it survives into the tax core for audit.

        source format: ``bot:<venue>``  (e.g. ``bot:kraken``, ``bot:xrpl``)
        """
        events: list[dict] = []
        for fill in fills:
            strategy = str(fill.get("strategy") or "")
            venue = str(fill.get("venue") or "")
            events.append({
                "event_id":              str(fill.get("fill_id") or ""),
                "timestamp":             _format_ts(fill.get("executed_at")),
                "event_type":            "TRADE",
                "source":                f"bot:{venue}",
                "account":               strategy,
                "asset_in":              str(fill.get("asset_in") or ""),
                "amount_in":             _format_decimal(fill.get("amount_in")),
                "asset_out":             str(fill.get("asset_out") or ""),
                "amount_out":            _format_decimal(fill.get("amount_out")),
                "fee_asset":             str(fill.get("fee_asset") or ""),
                "fee_amount":            _format_decimal(fill.get("fee_amount")),
                "tx_hash":               str(fill.get("tx_hash") or ""),
                "exchange_tx_id":        str(fill.get("order_id") or ""),
                "label":                 "",
                "complex_tax_treatment": "False",
                "note":                  f"strategy:{strategy}" if strategy else "",
            })
        return events

    @staticmethod
    def _lp_to_events(lp_events: list[dict]) -> list[dict]:
        """Map bot LP event rows to generic-events rows.

        All LP events carry ``complex_tax_treatment=True`` — they involve AMM
        pool shares that require manual FMV confirmation in the review queue.

        ``lp_action`` values: ``"deposit"`` | ``"withdraw"``
        Sidedness is detected by whether ``asset_b`` is NULL.

        Deposit (one-sided):
            ``asset_a → lp_token``; 1 TRADE event; label ``lp_deposit``

        Deposit (two-sided):
            ``asset_a + asset_b → lp_token``; 1 TRADE event (asset_a as
            primary); ``asset_b`` and ``amount_b`` encoded in ``note`` as
            ``asset_b:<symbol>:<amount>``

        Withdrawal (one-sided):
            ``lp_token → asset_a``; 1 TRADE event; label ``lp_withdraw``

        Withdrawal (two-sided):
            ``lp_token → asset_a + asset_b``; 1 TRADE event (asset_a as
            primary); secondary leg encoded in ``note``
        """
        events: list[dict] = []
        for row in lp_events:
            lp_action = str(row.get("lp_action") or "deposit").lower()
            venue = str(row.get("venue") or "")
            chain_id = str(row.get("chain_id") or "")
            asset_a = str(row.get("asset_a") or "")
            amount_a = _format_decimal(row.get("amount_a"))
            asset_b = row.get("asset_b")
            amount_b = row.get("amount_b")
            lp_token = str(row.get("lp_token") or "")
            lp_amount = _format_decimal(row.get("lp_amount"))

            is_two_sided = asset_b is not None and str(asset_b).strip() != ""

            note_parts = [f"pool:{row.get('pool_address', '')}"]
            if is_two_sided:
                note_parts.append(f"asset_b:{asset_b}:{_format_decimal(amount_b)}")
            note = " ".join(note_parts)

            if "deposit" in lp_action:
                event: dict = {
                    "event_id":              str(row.get("event_id") or ""),
                    "timestamp":             _format_ts(row.get("executed_at")),
                    "event_type":            "TRADE",
                    "source":                f"bot:{venue}",
                    "account":               chain_id,
                    "asset_in":              asset_a,
                    "amount_in":             amount_a,
                    "asset_out":             lp_token,
                    "amount_out":            lp_amount,
                    "fee_asset":             "",
                    "fee_amount":            "",
                    "tx_hash":               str(row.get("tx_hash") or ""),
                    "exchange_tx_id":        "",
                    "label":                 "lp_deposit",
                    "complex_tax_treatment": "True",
                    "note":                  note,
                }
            else:  # withdraw
                event = {
                    "event_id":              str(row.get("event_id") or ""),
                    "timestamp":             _format_ts(row.get("executed_at")),
                    "event_type":            "TRADE",
                    "source":                f"bot:{venue}",
                    "account":               chain_id,
                    "asset_in":              lp_token,
                    "amount_in":             lp_amount,
                    "asset_out":             asset_a,
                    "amount_out":            amount_a,
                    "fee_asset":             "",
                    "fee_amount":            "",
                    "tx_hash":               str(row.get("tx_hash") or ""),
                    "exchange_tx_id":        "",
                    "label":                 "lp_withdraw",
                    "complex_tax_treatment": "True",
                    "note":                  note,
                }
            events.append(event)
        return events

    def _write_csv(self, rows: list[dict]) -> None:
        """Overwrite the output CSV with *rows* (snapshot model)."""
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=_GENERIC_EVENTS_COLUMNS,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)


# ── Formatting helpers ────────────────────────────────────────────────────────


def _format_ts(value: Any) -> str:
    """Normalise a timestamp value to an ISO 8601 string.

    DuckDB TIMESTAMPTZ columns are returned as ``datetime.datetime`` objects.
    Plain strings and None are also handled gracefully.
    """
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return value.isoformat()
    return str(value)


def _format_decimal(value: Any) -> str:
    """Normalise a numeric value to a Decimal string, or empty string for None."""
    if value is None:
        return ""
    try:
        return str(Decimal(str(value)))
    except InvalidOperation:
        return str(value)


# ── Endpoint helpers ──────────────────────────────────────────────────────────


def _require_duckdb_and_bot_db() -> BotDuckDbSyncWorker:
    """Create a :class:`BotDuckDbSyncWorker` from current settings.

    Raises ``HTTPException 503`` when:
    - ``duckdb`` is not installed
    - ``BOT_DB`` is not configured (``None``)
    - ``BOT_DB`` file does not exist on disk
    """
    if not _DUCKDB_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "duckdb package is not installed. "
                "Install it with: pip install duckdb"
            ),
        )
    bot_db = settings.BOT_DB
    if bot_db is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "BOT_DB is not configured. "
                "Set the BOT_DB environment variable to the path of the bot's DuckDB file."
            ),
        )
    bot_db_path = Path(str(bot_db))
    if not bot_db_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"BOT_DB file not found: {bot_db}",
        )
    output_path = settings.DATA_DIR / "bot_generic_events.csv"
    return BotDuckDbSyncWorker(db_path=bot_db_path, output_path=output_path)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/sync", summary="Sync bot fills and LP events to generic-events CSV")
def sync_bot_events() -> dict:
    """Pull all fills and LP events from the bot's DuckDB into a generic-events CSV.

    The CSV is written to ``DATA_DIR/bot_generic_events.csv`` and overwrites any
    previous sync (**snapshot model**).  When the CSV is processed by a tax job,
    the orchestrator's dedup store prevents duplicate events from being counted
    twice.

    **Requirements:**

    - ``duckdb`` package installed (``pip install duckdb``)
    - ``BOT_DB`` environment variable pointing to the bot's ``.duckdb`` file

    Returns a :class:`BotSyncResult` with fill count, LP event count, and the
    output path.  On error, ``error`` is non-null but HTTP 200 is still returned
    so the UI can display the error inline without losing context.
    """
    global _last_sync_result

    worker = _require_duckdb_and_bot_db()
    result = worker.sync()
    _last_sync_result = result

    if result.error:
        _log.warning("bot: sync error: %s", result.error)

    return dataclasses.asdict(result)


@router.get("/sync/status", summary="Last bot sync result")
def get_sync_status() -> dict:
    """Return the result of the most recent bot sync.

    Returns ``{"synced": false, "last_sync": null}`` when no sync has been
    performed since the server started.
    """
    if _last_sync_result is None:
        return {"synced": False, "last_sync": None}
    return {"synced": True, "last_sync": dataclasses.asdict(_last_sync_result)}


@router.get(
    "/lot-impact/{symbol}",
    summary="Prior FIFO lot impact check for a trading symbol",
)
def get_lot_impact(symbol: str) -> dict:
    """Check whether *symbol* has open FIFO lots before adding it to the whitelist.

    Norwegian tax law requires a **single FIFO pool per asset** across all
    accounts and strategies.  If you already hold ETH from personal buys, the
    bot's first ETH purchase creates a lot that shares the same pool.  The
    bot's first ETH sell will consume the oldest personal lot — not the bot's
    entry price.

    Use this endpoint to make an informed whitelist decision.  Record the result
    in the bot's ``token_whitelist.had_prior_lots`` column as a permanent audit
    trail of the decision.

    **Response fields**

    - ``has_prior_lots`` — ``true`` when at least one open lot exists.
    - ``lot_count`` — number of open (``remaining_quantity > 0``) lots.
    - ``total_qty`` — sum of remaining quantities (full Decimal precision).
    - ``oldest_lot_date`` — ``acquired_timestamp`` of the oldest open lot.
    - ``oldest_lot_cost_basis_nok`` — ``remaining_cost_basis_nok`` of the oldest
      lot, or ``null`` when basis is missing.
    - ``warning`` — human-readable warning string when ``has_prior_lots`` is true.
    - ``db_exists`` — ``false`` when no lot store has been created yet.
    """
    symbol_upper = symbol.strip().upper()

    try:
        from tax_spine.pipeline.lot_store import LotPersistenceStore  # noqa: PLC0415
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"tax_spine package not available: {exc}",
        ) from exc

    db_path = settings.LOT_STORE_DB
    if not db_path.is_file():
        return _lot_impact_empty(symbol_upper, db_exists=False)

    try:
        with LotPersistenceStore(str(db_path)) as store:
            years = store.list_years()
            if not years:
                return _lot_impact_empty(symbol_upper, db_exists=True)
            latest_year = max(years)
            lots = store.load_carry_forward(latest_year)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read lot store: {exc}",
        ) from exc

    matching = [lot for lot in lots if lot.asset.upper() == symbol_upper]

    if not matching:
        return _lot_impact_empty(symbol_upper, db_exists=True)

    # Sort by acquired_timestamp (ascending) to find the oldest lot.
    matching.sort(key=lambda lot: str(getattr(lot, "acquired_timestamp", "") or ""))
    oldest = matching[0]

    total_qty: Decimal = sum(  # type: ignore[assignment]
        (getattr(lot, "remaining_quantity", Decimal("0")) or Decimal("0"))
        for lot in matching
    )

    oldest_basis_raw = getattr(oldest, "remaining_cost_basis_nok", None)
    oldest_basis_str = str(oldest_basis_raw) if oldest_basis_raw is not None else None
    oldest_date = str(getattr(oldest, "acquired_timestamp", "") or "") or None

    return {
        "symbol":                    symbol_upper,
        "has_prior_lots":            True,
        "lot_count":                 len(matching),
        "total_qty":                 str(total_qty),
        "oldest_lot_date":           oldest_date,
        "oldest_lot_cost_basis_nok": oldest_basis_str,
        "warning": (
            f"{symbol_upper} has {len(matching)} open lot(s) from tax year "
            f"{latest_year}. Bot trades will share the same FIFO pool — the "
            f"bot's first disposal will consume your oldest personal lot, not "
            f"the bot entry price. Set had_prior_lots=true in the bot whitelist "
            f"to record this decision."
        ),
        "db_exists": True,
    }


def _lot_impact_empty(symbol: str, *, db_exists: bool) -> dict:
    """Return a no-impact response for *symbol*."""
    return {
        "symbol":                    symbol,
        "has_prior_lots":            False,
        "lot_count":                 0,
        "total_qty":                 "0",
        "oldest_lot_date":           None,
        "oldest_lot_cost_basis_nok": None,
        "warning":                   None,
        "db_exists":                 db_exists,
    }
