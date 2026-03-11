"""Price-fetching service for the orchestrator.

Fetches historical daily NOK prices for crypto assets by combining:
  1. Kraken public OHLC API  — daily close prices in USD (no key required)
  2. Norges Bank SDMX API    — daily official USD/NOK FX rates (no key required)

Then multiplies: crypto_USD × USD_NOK = crypto_NOK

Why this combination?
---------------------
- Kraken's public API has no key requirement and covers XRP, BTC, ETH, ADA.
- Norges Bank (Norwegian central bank) is the official source for FX rates used
  in Norwegian tax calculations — these are the same rates Skatteetaten uses.
- Norges Bank only publishes business-day rates; weekend/holiday gaps are filled
  by carrying the last known rate forward (standard accounting practice).

The CSV format matches what taxspine CLIs expect via --csv-prices:
    date,asset_id,fiat_currency,price_fiat
    2025-01-01,XRP,NOK,7.4200
    ...

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

# ── Asset → Kraken USD pair map ───────────────────────────────────────────────
# Kraken uses "XBT" for Bitcoin; all other symbols match standard tickers.
# Only USD pairs are listed here — NOK conversion comes from Norges Bank.
_KRAKEN_USD_PAIR: dict[str, str] = {
    "XRP": "XRPUSD",
    "BTC": "XBTUSD",   # Kraken uses XBT, not BTC
    "ETH": "ETHUSD",
    "ADA": "ADAUSD",
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
    pair_usd = _KRAKEN_USD_PAIR.get(asset)
    if pair_usd is None:
        supported = ", ".join(sorted(_KRAKEN_USD_PAIR))
        raise ValueError(
            f"Unsupported asset '{asset}'. Supported: {supported}"
        )

    dest = _price_csv_path(asset, year)
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)

    cached = not _needs_fetch(dest, year)
    if not cached:
        _fetch_and_write(pair_usd, asset, year, dest)

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


# ── Data sources ──────────────────────────────────────────────────────────────


def _fetch_kraken_usd_prices(pair: str, year: int) -> dict[str, float]:
    """Return daily close prices in USD from Kraken for *pair* in *year*.

    Uses daily (1440-min) OHLC candles.  Kraken returns up to 720 candles
    per call — enough to cover a full year in one request.

    Returns {date_str: close_price_usd}.
    """
    tz = datetime.timezone.utc
    since = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp())

    url = (
        f"https://api.kraken.com/0/public/OHLC"
        f"?pair={pair}&interval=1440&since={since}"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"Could not reach Kraken API: {exc}") from exc

    if body.get("error"):
        raise RuntimeError(f"Kraken API error for {pair}: {body['error']}")

    result = body.get("result", {})
    data_key = next((k for k in result if k != "last"), None)
    if data_key is None:
        raise RuntimeError(f"Kraken returned no candle data for {pair}.")

    year_start = datetime.datetime(year, 1, 1, tzinfo=tz)
    year_end   = datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz)

    prices: dict[str, float] = {}
    for candle in result[data_key]:
        # [timestamp, open, high, low, close, vwap, volume, count]
        ts    = int(candle[0])
        close = float(candle[4])
        dt    = datetime.datetime.fromtimestamp(ts, tz=tz)
        if year_start <= dt <= year_end:
            prices[dt.strftime("%Y-%m-%d")] = close

    if not prices:
        raise RuntimeError(
            f"Kraken returned no {pair} candles for {year}. "
            "The pair may not have been listed that year."
        )
    return prices


def _fetch_norges_bank_usd_nok(year: int) -> dict[str, float]:
    """Return daily official USD/NOK rates from Norges Bank for *year*.

    Uses Norges Bank's SDMX JSON API — the same rates published by the
    Norwegian central bank and used as the reference in Norwegian tax law.

    Only business days are published; weekends and public holidays are absent
    from the result.  Call ``_fill_calendar_gaps`` to fill those gaps.

    Returns {date_str: rate} where rate is NOK per 1 USD.
    """
    start = f"{year}-01-01"
    end   = f"{year}-12-31"

    url = (
        "https://data.norges-bank.no/api/data/EXR/B.USD.NOK.SP"
        f"?format=sdmx-json&startPeriod={start}&endPeriod={end}&locale=en"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"Could not reach Norges Bank API: {exc}") from exc

    # Parse SDMX JSON structure.
    # dates  = data.structure.dimensions.observation[0].values  → [{id: "YYYY-MM-DD"}, ...]
    # series = data.dataSets[0].series["0:0:0:0"].observations  → {"0": [rate, ...], ...}
    try:
        structure  = body["data"]["structure"]
        time_vals  = structure["dimensions"]["observation"][0]["values"]
        dates      = [v["id"] for v in time_vals]

        dataset    = body["data"]["dataSets"][0]
        series_key = next(iter(dataset["series"]))
        obs        = dataset["series"][series_key]["observations"]
    except (KeyError, IndexError, StopIteration) as exc:
        raise RuntimeError(
            f"Unexpected Norges Bank response format: {exc}"
        ) from exc

    rates: dict[str, float] = {}
    for idx_str, value_list in obs.items():
        idx  = int(idx_str)
        rate = float(value_list[0])
        rates[dates[idx]] = rate

    if not rates:
        raise RuntimeError(
            f"Norges Bank returned no USD/NOK rates for {year}."
        )
    return rates


def _fill_calendar_gaps(rates: dict[str, float], year: int) -> dict[str, float]:
    """Fill weekend/holiday gaps by carrying the last known rate forward.

    Norges Bank publishes business-day rates only.  Standard accounting
    practice is to use the most recent published rate for non-trading days.
    """
    start   = datetime.date(year, 1, 1)
    end     = datetime.date(year, 12, 31)
    filled: dict[str, float] = {}
    last_rate: float | None = None

    current = start
    while current <= end:
        date_str = current.isoformat()
        if date_str in rates:
            last_rate = rates[date_str]
        if last_rate is not None:
            filled[date_str] = last_rate
        current += datetime.timedelta(days=1)

    return filled


# ── Orchestrated fetch + write ─────────────────────────────────────────────────


def _fetch_and_write(pair_usd: str, asset: str, year: int, dest: Path) -> None:
    """Combine Kraken USD prices + Norges Bank FX → NOK price CSV at *dest*."""
    usd_prices  = _fetch_kraken_usd_prices(pair_usd, year)
    raw_fx      = _fetch_norges_bank_usd_nok(year)
    nok_rates   = _fill_calendar_gaps(raw_fx, year)

    rows: list[tuple[str, str]] = []
    for date_str, usd_price in sorted(usd_prices.items()):
        nok_rate = nok_rates.get(date_str)
        if nok_rate is None:
            continue   # no FX rate — skip (only affects early Jan if no prior-year rate)
        nok_price = usd_price * nok_rate
        rows.append((date_str, f"{nok_price:.4f}"))

    if not rows:
        raise RuntimeError(
            f"No NOK prices could be computed for {asset} in {year} "
            "(no overlap between Kraken and Norges Bank data)."
        )

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, asset, "NOK", price])


def _count_rows(path: Path) -> int:
    """Return the number of data rows in *path* (header excluded)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return max(0, len(lines) - 1)
    except OSError:
        return 0


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/fetch", response_model=FetchPricesResponse, tags=["prices"])
def fetch_prices(body: FetchPricesRequest) -> FetchPricesResponse:
    """Fetch (or return cached) daily NOK prices for an asset and year.

    Combines Kraken USD prices with official Norges Bank USD/NOK rates.
    No API key required for either source.

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
    """List all cached price CSV files in PRICES_DIR."""
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    result: list[PriceFileInfo] = []

    for csv_path in sorted(settings.PRICES_DIR.glob("*_nok_*.csv")):
        stem  = csv_path.stem   # e.g. "xrp_nok_2025"
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
