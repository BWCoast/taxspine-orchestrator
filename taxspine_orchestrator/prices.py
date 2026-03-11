"""Price-fetching service for the orchestrator.

Fetches historical daily NOK prices for crypto assets from the Kraken
public REST API and caches them as CSV files in PRICES_DIR.

The CSV format matches what taxspine CLIs expect via --csv-prices:
    date,asset_id,fiat_currency,price_fiat
    2025-01-01,XRP,NOK,7.4200
    ...

Why Kraken?
-----------
- No API key or account required.
- Native NOK trading pairs (XRPNOK, XBTNOK, ETHNOK) — prices are what
  Norwegian users actually transacted at, not a USD→NOK conversion.
- Reliable public OHLC endpoint with no meaningful rate limits for
  single-user private systems.

Caching policy
--------------
- Past years  (year < current year): fetched once, never re-fetched.
- Current year (year == current year): re-fetched if the file is > 24 h old.
"""

from __future__ import annotations

import csv
import datetime
import json
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import settings

# ── Kraken pair map ───────────────────────────────────────────────────────────
# Maps ticker symbol → Kraken trading pair name (always vs NOK).
# Kraken uses "XBT" internally for Bitcoin; all other symbols match standard.
_KRAKEN_PAIR: dict[str, str] = {
    "XRP": "XRPNOK",
    "BTC": "XBTNOK",
    "ETH": "ETHNOK",
}

_STALE_HOURS = 24   # re-fetch current-year file if older than this


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/prices", tags=["prices"])


# ── Request / response models ─────────────────────────────────────────────────


class FetchPricesRequest(BaseModel):
    """Request body for POST /prices/fetch."""

    year: int
    asset: str = "XRP"


class PriceFileInfo(BaseModel):
    """Metadata for a cached price file."""

    asset: str
    year: int
    path: str          # absolute container path, ready for --csv-prices
    rows: int          # number of daily data rows (excludes header)
    age_hours: float   # hours since last fetch (0.0 if just written)
    cached: bool       # True if the file already existed before this request


class FetchPricesResponse(PriceFileInfo):
    """Response body for POST /prices/fetch."""


# ── Core fetch logic (no FastAPI dependency) ──────────────────────────────────


def _price_csv_path(asset: str, year: int) -> Path:
    """Return the canonical cache path for *asset*/*year*."""
    return settings.PRICES_DIR / f"{asset.lower()}_nok_{year}.csv"


def _file_age_hours(path: Path) -> float:
    """Return the age of *path* in hours, or infinity if it does not exist."""
    if not path.exists():
        return float("inf")
    mtime = path.stat().st_mtime
    return (time.time() - mtime) / 3600


def _needs_fetch(path: Path, year: int) -> bool:
    """Return True if *path* should be (re-)fetched."""
    current_year = datetime.date.today().year
    if not path.exists():
        return True
    if year < current_year:
        return False   # past year — never re-fetch
    return _file_age_hours(path) > _STALE_HOURS


def fetch_prices_for_year(asset: str, year: int) -> PriceFileInfo:
    """Fetch (or return cached) daily NOK prices for *asset* in *year*.

    Writes a CSV to PRICES_DIR and returns metadata about the file.
    Raises ValueError for unsupported assets, RuntimeError on network failure.
    """
    asset = asset.upper()
    pair = _KRAKEN_PAIR.get(asset)
    if pair is None:
        supported = ", ".join(sorted(_KRAKEN_PAIR))
        raise ValueError(
            f"Unsupported asset '{asset}'. Supported: {supported}"
        )

    dest = _price_csv_path(asset, year)
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)

    cached = not _needs_fetch(dest, year)
    if not cached:
        _fetch_and_write(pair, asset, year, dest)

    rows = _count_rows(dest)
    age = _file_age_hours(dest)

    return PriceFileInfo(
        asset=asset,
        year=year,
        path=str(dest),
        rows=rows,
        age_hours=round(age, 2),
        cached=cached,
    )


def _fetch_and_write(pair: str, asset: str, year: int, dest: Path) -> None:
    """Call Kraken OHLC endpoint and write the result CSV to *dest*.

    Uses daily candles (interval=1440 minutes).  Kraken returns up to 720
    candles per request — enough to cover a full calendar year in one call.
    """
    tz = datetime.timezone.utc
    # `since` is the Unix timestamp of 1 Jan of the requested year.
    # Kraken returns candles whose open timestamp is >= since.
    since = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp())

    url = (
        f"https://api.kraken.com/0/public/OHLC"
        f"?pair={pair}&interval=1440&since={since}"
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "taxspine-orchestrator/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(
            f"Could not reach Kraken API: {exc}"
        ) from exc

    if body.get("error"):
        raise RuntimeError(
            f"Kraken API error for {pair}: {body['error']}"
        )

    result = body.get("result", {})
    # result keys = one data key (the pair name Kraken uses) + "last"
    data_key = next((k for k in result if k != "last"), None)
    if data_key is None:
        raise RuntimeError(
            f"Kraken returned no candle data for pair {pair}."
        )

    # Candle format: [timestamp, open, high, low, close, vwap, volume, count]
    candles: list[list] = result[data_key]

    year_start = datetime.datetime(year, 1, 1, tzinfo=tz)
    year_end   = datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz)

    rows: list[tuple[str, str]] = []
    for candle in candles:
        ts    = int(candle[0])
        close = candle[4]
        dt    = datetime.datetime.fromtimestamp(ts, tz=tz)
        if dt < year_start or dt > year_end:
            continue
        date_str = dt.strftime("%Y-%m-%d")
        rows.append((date_str, f"{float(close):.4f}"))

    if not rows:
        raise RuntimeError(
            f"Kraken returned no candle data for {pair} in {year}. "
            "The pair may not have been listed that year."
        )

    rows.sort(key=lambda r: r[0])

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, asset, "NOK", price])


def _count_rows(path: Path) -> int:
    """Return the number of data rows in *path* (header excluded)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return max(0, len(lines) - 1)   # subtract header
    except OSError:
        return 0


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/fetch", response_model=FetchPricesResponse, tags=["prices"])
def fetch_prices(body: FetchPricesRequest) -> FetchPricesResponse:
    """Fetch (or return cached) daily NOK prices for an asset and year.

    - Writes a CSV to ``PRICES_DIR/{asset}_nok_{year}.csv``.
    - Past years are fetched once and never re-fetched.
    - Current year is re-fetched if the cached file is older than 24 hours.

    The returned ``path`` is the absolute container path — paste it directly
    into the "Price table path" field when running a report.
    """
    current_year = datetime.date.today().year
    if body.year < 2013 or body.year > current_year:
        raise HTTPException(
            status_code=400,
            detail=f"Year must be between 2013 and {current_year}.",
        )

    try:
        info = fetch_prices_for_year(body.asset, body.year)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return FetchPricesResponse(**info.model_dump())


@router.get("", response_model=list[PriceFileInfo], tags=["prices"])
def list_prices() -> list[PriceFileInfo]:
    """List all cached price CSV files in PRICES_DIR.

    Useful for checking what has already been fetched before running a job.
    """
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    result: list[PriceFileInfo] = []

    for csv_path in sorted(settings.PRICES_DIR.glob("*_nok_*.csv")):
        # Filename pattern: {asset}_nok_{year}.csv
        stem = csv_path.stem  # e.g. "xrp_nok_2025"
        parts = stem.split("_nok_")
        if len(parts) != 2:
            continue
        asset = parts[0].upper()
        try:
            year = int(parts[1])
        except ValueError:
            continue

        result.append(
            PriceFileInfo(
                asset=asset,
                year=year,
                path=str(csv_path),
                rows=_count_rows(csv_path),
                age_hours=round(_file_age_hours(csv_path), 2),
                cached=True,
            )
        )

    return result
