"""Price-fetching service for the orchestrator.

Fetches historical daily NOK prices for all supported crypto assets by
combining two free, key-less public APIs:

  1. Kraken public OHLC API  — daily close prices in USD
  2. Norges Bank SDMX API    — daily official USD/NOK FX rates

Then multiplies: crypto_USD × USD_NOK = crypto_NOK per asset per day.

All supported assets are fetched in one call and merged into a single
``combined_nok_{year}.csv`` file, which is what the taxspine CLI receives
via ``--csv-prices``.  Individual per-asset CSVs are cached separately so
re-runs are instant for past years.

Why Norges Bank for FX?
-----------------------
Norges Bank (Norwegian central bank) is the official source for FX rates
referenced in Norwegian tax law.  Using these rates aligns with what
Skatteetaten expects when checking valuations.  Business-day-only gaps
are filled by carrying the last known rate forward (standard practice).

Caching policy
--------------
- Past years  (year < current year): fetched once, never re-fetched.
- Current year (year == current year): re-fetched if the file is > 24 h old.

CSV format
----------
The combined CSV matches what ``taxspine-*`` CLIs expect via --csv-prices:
    date,asset_id,fiat_currency,price_fiat
    2025-01-01,XRP,NOK,7.4200
    2025-01-01,BTC,NOK,1234567.00
    ...
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
# These are the assets we fetch prices for on every call.
# Kraken uses "XBT" for Bitcoin; all others match standard tickers.
_KRAKEN_USD_PAIR: dict[str, str] = {
    "XRP": "XRPUSD",
    "BTC": "XBTUSD",   # Kraken uses XBT internally, but XBTUSD works as altname
    "ETH": "ETHUSD",
    "ADA": "ADAUSD",
}

_ALL_ASSETS: list[str] = list(_KRAKEN_USD_PAIR.keys())  # ["XRP", "BTC", "ETH", "ADA"]

_STALE_HOURS = 24   # re-fetch current-year files if older than this


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/prices", tags=["prices"])


# ── Request / response models ─────────────────────────────────────────────────


class FetchPricesRequest(BaseModel):
    """Request body for POST /prices/fetch."""

    year: int


class PriceFileInfo(BaseModel):
    """Metadata for a cached price file."""

    asset: str
    year: int
    path: str          # absolute container path, ready for --csv-prices
    rows: int          # number of daily data rows (excludes header)
    age_hours: float   # hours since last fetch (0.0 if just written)
    cached: bool       # True if no API calls were made (all files were fresh)


class FetchPricesResponse(PriceFileInfo):
    """Response body for POST /prices/fetch."""


# ── Path helpers ──────────────────────────────────────────────────────────────


def _asset_csv_path(asset: str, year: int) -> Path:
    """Per-asset cache file: ``xrp_nok_2025.csv``."""
    return settings.PRICES_DIR / f"{asset.lower()}_nok_{year}.csv"


def _combined_csv_path(year: int) -> Path:
    """Merged file passed to the CLI: ``combined_nok_2025.csv``."""
    return settings.PRICES_DIR / f"combined_nok_{year}.csv"


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
        return False   # past year — immutable, never re-fetch
    return _file_age_hours(path) > _STALE_HOURS


# ── Top-level entry point ─────────────────────────────────────────────────────


def fetch_all_prices_for_year(year: int) -> PriceFileInfo:
    """Fetch (or return cached) daily NOK prices for all supported assets.

    Fetches XRP, BTC, ETH, ADA from Kraken (USD) × Norges Bank (USD/NOK),
    writes individual per-asset CSVs (cached), then merges into one combined
    CSV that the taxspine CLI can consume via ``--csv-prices``.

    Returns metadata about the combined file.
    """
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)

    any_fetched = False
    available_paths: list[Path] = []

    for asset in _ALL_ASSETS:
        dest = _asset_csv_path(asset, year)
        if _needs_fetch(dest, year):
            pair_usd = _KRAKEN_USD_PAIR[asset]
            try:
                _fetch_and_write(pair_usd, asset, year, dest)
                any_fetched = True
            except RuntimeError:
                # If one asset fails (e.g. ADA not listed in early years),
                # continue with the others rather than aborting entirely.
                continue
        if dest.exists():
            available_paths.append(dest)

    if not available_paths:
        raise RuntimeError(
            f"Could not fetch price data for any asset in {year}. "
            "Check network connectivity."
        )

    combined = _combined_csv_path(year)
    total_rows = _write_combined_csv(available_paths, combined)

    return PriceFileInfo(
        asset="COMBINED",
        year=year,
        path=str(combined),
        rows=total_rows,
        age_hours=round(_file_age_hours(combined), 2),
        cached=not any_fetched,
    )


# ── Data sources ──────────────────────────────────────────────────────────────


def _fetch_kraken_usd_prices(pair: str, year: int) -> dict[str, float]:
    """Return {date_str: close_usd} from Kraken daily OHLC for *pair* in *year*."""
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
    """Return {date_str: usd_nok_rate} from Norges Bank for business days in *year*.

    Uses the official SDMX JSON API.  Only business days are published;
    call ``_fill_calendar_gaps`` to fill weekends and public holidays.
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
        rates[dates[int(idx_str)]] = float(value_list[0])

    if not rates:
        raise RuntimeError(f"Norges Bank returned no USD/NOK rates for {year}.")
    return rates


def _fill_calendar_gaps(rates: dict[str, float], year: int) -> dict[str, float]:
    """Fill weekend/public-holiday gaps by carrying the last known rate forward."""
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


def _fetch_and_write(pair_usd: str, asset: str, year: int, dest: Path) -> None:
    """Fetch USD prices from Kraken + FX from Norges Bank → write NOK CSV."""
    usd_prices = _fetch_kraken_usd_prices(pair_usd, year)
    raw_fx     = _fetch_norges_bank_usd_nok(year)
    nok_rates  = _fill_calendar_gaps(raw_fx, year)

    rows: list[tuple[str, str]] = []
    for date_str, usd_price in sorted(usd_prices.items()):
        nok_rate = nok_rates.get(date_str)
        if nok_rate is None:
            continue
        rows.append((date_str, f"{usd_price * nok_rate:.4f}"))

    if not rows:
        raise RuntimeError(
            f"No NOK prices computed for {asset} {year}: "
            "no overlap between Kraken candles and Norges Bank rates."
        )

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, asset, "NOK", price])


def _write_combined_csv(source_paths: list[Path], dest: Path) -> int:
    """Merge per-asset CSVs into a single file.  Returns number of data rows."""
    total = 0
    with dest.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for path in source_paths:
            with path.open("r", encoding="utf-8", newline="") as src:
                reader = csv.DictReader(src)
                for row in reader:
                    writer.writerow([
                        row["date"],
                        row["asset_id"],
                        row["fiat_currency"],
                        row["price_fiat"],
                    ])
                    total += 1
    return total


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
    """Fetch (or return cached) daily NOK prices for all supported assets.

    Fetches XRP, BTC, ETH, and ADA using Kraken USD prices × official
    Norges Bank USD/NOK rates.  No API key required for either source.

    Writes individual per-asset CSVs (cached) and merges them into a
    single ``combined_nok_{year}.csv``.  The returned ``path`` is the
    combined file — paste it into the "Price table path" field.

    Past years are cached indefinitely.  Current year is re-fetched if
    the cached files are older than 24 hours.
    """
    current_year = datetime.date.today().year
    if body.year < 2013 or body.year > current_year:
        raise HTTPException(
            status_code=400,
            detail=f"Year must be between 2013 and {current_year}.",
        )

    try:
        info = fetch_all_prices_for_year(body.year)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return FetchPricesResponse(**info.model_dump())


@router.get("", response_model=list[PriceFileInfo], tags=["prices"])
def list_prices() -> list[PriceFileInfo]:
    """List all cached price CSV files in PRICES_DIR."""
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    result: list[PriceFileInfo] = []

    for csv_path in sorted(settings.PRICES_DIR.glob("*_nok_*.csv")):
        stem  = csv_path.stem   # e.g. "xrp_nok_2025" or "combined_nok_2025"
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
