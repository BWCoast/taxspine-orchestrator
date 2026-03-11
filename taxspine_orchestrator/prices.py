"""Price-fetching service for the orchestrator.

Fetches historical daily NOK prices for crypto assets from the CoinGecko
public API and caches them as CSV files in PRICES_DIR.

The CSV format matches what taxspine CLIs expect via --csv-prices:
    date,asset_id,fiat_currency,price_fiat
    2025-01-01,XRP,NOK,7.4200
    ...

Caching policy
--------------
- Past years  (year < current year): fetched once, never re-fetched.
- Current year (year == current year): re-fetched if the file is > 24 h old.
  This keeps the price table fresh during the tax year without hammering the API.

CoinGecko free API
------------------
No API key required for the /coins/{id}/market_chart/range endpoint.
Rate limit: ~30 requests/minute on the free tier.
For a private single-user system this is more than sufficient.
"""

from __future__ import annotations

import csv
import datetime
import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import settings

# ── CoinGecko asset map ────────────────────────────────────────────────────────
# Maps ticker symbol → CoinGecko coin ID.
# Extend this dict to support additional assets (e.g. ETH, BTC).
_COINGECKO_ID: dict[str, str] = {
    "XRP": "ripple",
    "BTC": "bitcoin",
    "ETH": "ethereum",
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
    coin_id = _COINGECKO_ID.get(asset)
    if coin_id is None:
        supported = ", ".join(sorted(_COINGECKO_ID))
        raise ValueError(
            f"Unsupported asset '{asset}'. Supported: {supported}"
        )

    dest = _price_csv_path(asset, year)
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)

    cached = not _needs_fetch(dest, year)
    if not cached:
        _fetch_and_write(coin_id, asset, year, dest)

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


def _fetch_and_write(coin_id: str, asset: str, year: int, dest: Path) -> None:
    """Call CoinGecko and write the result CSV to *dest*."""
    tz = datetime.timezone.utc
    start = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp())
    end   = int(datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz).timestamp())

    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        f"/market_chart/range"
        f"?vs_currency=nok&from={start}&to={end}&precision=4"
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
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"CoinGecko returned HTTP {exc.code} for {coin_id}/NOK {year}. "
            "You may be rate-limited — wait a minute and retry."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Could not reach CoinGecko: {exc}"
        ) from exc

    prices: list[list] = body.get("prices", [])
    if not prices:
        raise RuntimeError(
            f"CoinGecko returned no price data for {coin_id}/NOK {year}."
        )

    # Deduplicate to one entry per calendar date (keep first occurrence).
    seen: set[str] = set()
    rows: list[tuple[str, str]] = []
    for ts_ms, price in prices:
        date_str = datetime.datetime.fromtimestamp(
            ts_ms / 1000, tz=tz
        ).strftime("%Y-%m-%d")
        if date_str not in seen:
            seen.add(date_str)
            rows.append((date_str, f"{float(price):.4f}"))

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
