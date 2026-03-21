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
from decimal import Decimal, ROUND_HALF_UP
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


class UnsupportedAssetNote(BaseModel):
    """Advisory note for an asset that could not be priced automatically."""

    asset: str
    reason: str  # e.g. "Not available on Kraken; source prices manually"


class FetchPricesResponse(PriceFileInfo):
    """Response body for POST /prices/fetch.

    ``unsupported_assets`` lists assets that were requested (or are known
    to be used) but cannot be fetched from the automatic sources.  The
    caller should source these prices manually and merge them into the
    combined CSV before passing ``--csv-prices`` to the tax CLI.
    """

    unsupported_assets: list[UnsupportedAssetNote] = []


# ── Path helpers ──────────────────────────────────────────────────────────────


def _asset_csv_path(asset: str, year: int) -> Path:
    """Per-asset cache file: ``xrp_nok_2025.csv``."""
    return settings.PRICES_DIR / f"{asset.lower()}_nok_{year}.csv"


def _combined_csv_path(year: int) -> Path:
    """Merged file passed to the CLI: ``combined_nok_2025.csv``."""
    return settings.PRICES_DIR / f"combined_nok_{year}.csv"


def _asset_csv_path_gbp(asset: str, year: int) -> Path:
    """Per-asset GBP cache file: ``xrp_gbp_2025.csv``."""
    return settings.PRICES_DIR / f"{asset.lower()}_gbp_{year}.csv"


def _combined_csv_path_gbp(year: int) -> Path:
    """Merged GBP file passed to the CLI: ``combined_gbp_{year}.csv``."""
    return settings.PRICES_DIR / f"combined_gbp_{year}.csv"


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


def fetch_all_prices_for_year(year: int) -> FetchPricesResponse:
    """Fetch (or return cached) daily NOK prices for all supported assets.

    Fetches XRP, BTC, ETH, ADA from Kraken (USD) × Norges Bank (USD/NOK),
    writes individual per-asset CSVs (cached), then merges into one combined
    CSV that the taxspine CLI can consume via ``--csv-prices``.

    Returns metadata about the combined file, including ``unsupported_assets``
    — a list of assets that could not be priced automatically.  RLUSD is
    always included here because Kraken does not have a direct RLUSD/USD pair;
    callers should source RLUSD prices manually (e.g. via the RLUSD/USD pair
    on a DEX) and add them to the price CSV before running the tax CLI.
    """
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)

    any_fetched = False
    available_paths: list[Path] = []
    failed_assets: list[str] = []

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
                failed_assets.append(asset)
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

    # Build unsupported_assets list.
    # RLUSD is always unsupported: Kraken has no direct RLUSD/USD pair.
    # Any assets that failed during this fetch run are also included.
    unsupported: list[UnsupportedAssetNote] = [
        UnsupportedAssetNote(
            asset="RLUSD",
            reason=(
                "Not available on Kraken; source prices manually via the "
                "RLUSD/USD pair on XRPL DEX or another exchange and add rows "
                "to the combined CSV before running the tax CLI."
            ),
        ),
    ]
    for failed_asset in failed_assets:
        unsupported.append(
            UnsupportedAssetNote(
                asset=failed_asset,
                reason=(
                    f"{failed_asset} could not be fetched from Kraken for {year}. "
                    "The pair may not have been listed that year; "
                    "source prices manually."
                ),
            )
        )

    return FetchPricesResponse(
        asset="COMBINED",
        year=year,
        path=str(combined),
        rows=total_rows,
        age_hours=round(_file_age_hours(combined), 2),
        cached=not any_fetched,
        unsupported_assets=unsupported,
    )


# ── Data sources ──────────────────────────────────────────────────────────────


def _fetch_kraken_usd_prices(pair: str, year: int) -> dict[str, Decimal]:
    """Return {date_str: close_usd} from Kraken daily OHLC for *pair* in *year*.

    Prices are returned as ``Decimal`` (not float) so that subsequent
    multiplication with NOK/USD rates is exact and audit-traceable.
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

    prices: dict[str, Decimal] = {}
    for candle in result[data_key]:
        # [timestamp, open, high, low, close, vwap, volume, count]
        ts    = int(candle[0])
        # Convert via str() to avoid floating-point rounding artefacts.
        close = Decimal(str(candle[4]))
        dt    = datetime.datetime.fromtimestamp(ts, tz=tz)
        if year_start <= dt <= year_end:
            prices[dt.strftime("%Y-%m-%d")] = close

    if not prices:
        raise RuntimeError(
            f"Kraken returned no {pair} candles for {year}. "
            "The pair may not have been listed that year."
        )
    return prices


def _fetch_norges_bank_usd_nok(year: int) -> dict[str, Decimal]:
    """Return {date_str: usd_nok_rate} from Norges Bank for business days in *year*.

    Uses the official SDMX JSON API.  Only business days are published;
    call ``_fill_calendar_gaps`` to fill weekends and public holidays.

    Rates are returned as ``Decimal`` to avoid float rounding when
    multiplied with USD prices.
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

    rates: dict[str, Decimal] = {}
    for idx_str, value_list in obs.items():
        # Convert via str() to avoid float rounding artefacts.
        rates[dates[int(idx_str)]] = Decimal(str(value_list[0]))

    if not rates:
        raise RuntimeError(f"Norges Bank returned no USD/NOK rates for {year}.")
    return rates


def _fill_calendar_gaps(rates: dict[str, Decimal], year: int) -> dict[str, Decimal]:
    """Fill weekend/public-holiday gaps by carrying the last known rate forward.

    TL-15: seeds ``last_rate`` from the earliest available rate in the dataset
    so that early-January days before the first Norges Bank publication are not
    left blank.  When January 1–2 fall on a weekend the first publication date
    is typically January 3; seeding from that value ensures January 1 and 2
    receive a rate rather than being omitted entirely (which would cause
    UNRESOLVED valuations for any transactions on those days).

    Using the chronologically-first available rate as a backward-fill seed is a
    reasonable approximation consistent with how financial data providers handle
    bank-holiday gaps at the start of a year.
    """
    start   = datetime.date(year, 1, 1)
    end     = datetime.date(year, 12, 31)
    filled: dict[str, Decimal] = {}
    # TL-15: seed from the earliest known rate so pre-publication days
    # (e.g. 1–2 Jan when the first Norges Bank business day is 3 Jan) are
    # covered instead of being silently omitted.
    last_rate: Decimal | None = rates[min(rates)] if rates else None

    current = start
    while current <= end:
        date_str = current.isoformat()
        if date_str in rates:
            last_rate = rates[date_str]
        if last_rate is not None:
            filled[date_str] = last_rate
        current += datetime.timedelta(days=1)

    return filled


def _fetch_bank_of_england_usd_gbp(year: int) -> dict[str, Decimal]:
    """Return {date_str: usd_per_gbp_rate} from Bank of England for business days in *year*.

    TL-19: Bank of England XUDLUSS series = USD per 1 GBP (spot rate).
    To convert a USD price to GBP: gbp_price = usd_price / usd_per_gbp_rate.

    Uses the public IADB CSV API — no API key required.
    Only business days are published; call ``_fill_calendar_gaps`` to extend.
    Rates are returned as ``Decimal`` to avoid float rounding artefacts.
    """
    start = f"01/Jan/{year}"
    end   = f"31/Dec/{year}"
    url = (
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp"
        f"?csv.x=yes&Datefrom={start}&Dateto={end}"
        "&SeriesCodes=XUDLUSS&UsingCodes=Y&CSVF=TN&html.x=1&html.y=1"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "text/csv,text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"Could not reach Bank of England API: {exc}") from exc

    rates: dict[str, Decimal] = {}
    lines = text.splitlines()
    if len(lines) < 2:
        raise RuntimeError(
            f"Bank of England returned insufficient data for {year}."
        )
    # Header row is "Date,XUDLUSS" (or similar) — skip it.
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 2:
            continue
        date_raw = parts[0].strip().strip('"')
        val_raw  = parts[1].strip().strip('"')
        if not val_raw or val_raw.lower() in ("n/a", "na", ""):
            continue
        try:
            # BoE date format: "02 Jan 2025"
            dt = datetime.datetime.strptime(date_raw, "%d %b %Y")
            date_str = dt.strftime("%Y-%m-%d")
            rates[date_str] = Decimal(val_raw)
        except (ValueError, Exception):
            continue  # skip unparseable rows

    if not rates:
        raise RuntimeError(f"Bank of England returned no USD/GBP rates for {year}.")
    return rates


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
        nok_price = (usd_price * nok_rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        rows.append((date_str, str(nok_price)))

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


def _fetch_and_write_gbp(pair_usd: str, asset: str, year: int, dest: Path) -> None:
    """Fetch USD prices from Kraken + FX from Bank of England → write GBP CSV.

    TL-19: mirrors _fetch_and_write() but uses BoE XUDLUSS (USD/GBP) rates.
    GBP price = USD price / (USD per GBP rate).
    """
    usd_prices  = _fetch_kraken_usd_prices(pair_usd, year)
    raw_fx      = _fetch_bank_of_england_usd_gbp(year)
    gbp_rates   = _fill_calendar_gaps(raw_fx, year)

    rows: list[tuple[str, str]] = []
    for date_str, usd_price in sorted(usd_prices.items()):
        usd_per_gbp = gbp_rates.get(date_str)
        if usd_per_gbp is None or usd_per_gbp == Decimal("0"):
            continue
        gbp_price = (usd_price / usd_per_gbp).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        rows.append((date_str, str(gbp_price)))

    if not rows:
        raise RuntimeError(
            f"No GBP prices computed for {asset} {year}: "
            "no overlap between Kraken candles and Bank of England rates."
        )

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, asset, "GBP", price])


def fetch_all_gbp_prices_for_year(year: int) -> FetchPricesResponse:
    """Fetch (or return cached) daily GBP prices for all supported assets.

    TL-19: UK jobs require GBP-denominated price tables.  This function
    mirrors ``fetch_all_prices_for_year()`` but uses Bank of England
    XUDLUSS (USD/GBP) rates instead of Norges Bank USD/NOK rates.

    Fetches XRP, BTC, ETH, ADA from Kraken (USD) × Bank of England (USD/GBP),
    writes individual per-asset GBP CSVs (cached), then merges into one
    ``combined_gbp_{year}.csv`` file for ``--csv-prices``.

    RLUSD is always reported as unsupported (no direct Kraken USD pair).
    """
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)

    any_fetched = False
    available_paths: list[Path] = []
    failed_assets: list[str] = []

    for asset in _ALL_ASSETS:
        dest = _asset_csv_path_gbp(asset, year)
        if _needs_fetch(dest, year):
            pair_usd = _KRAKEN_USD_PAIR[asset]
            try:
                _fetch_and_write_gbp(pair_usd, asset, year, dest)
                any_fetched = True
            except RuntimeError:
                failed_assets.append(asset)
                continue
        if dest.exists():
            available_paths.append(dest)

    if not available_paths:
        raise RuntimeError(
            f"Could not fetch GBP price data for any asset in {year}. "
            "Check network connectivity."
        )

    combined = _combined_csv_path_gbp(year)
    total_rows = _write_combined_csv(available_paths, combined)

    unsupported: list[UnsupportedAssetNote] = [
        UnsupportedAssetNote(
            asset="RLUSD",
            reason=(
                "Not available on Kraken; source GBP prices manually via the "
                "RLUSD/USD pair on XRPL DEX and convert using BoE USD/GBP rate."
            ),
        ),
    ]
    for failed_asset in failed_assets:
        unsupported.append(
            UnsupportedAssetNote(
                asset=failed_asset,
                reason=(
                    f"{failed_asset} could not be fetched from Kraken for {year}. "
                    "The pair may not have been listed that year; "
                    "source prices manually."
                ),
            )
        )

    return FetchPricesResponse(
        asset="COMBINED",
        year=year,
        path=str(combined),
        rows=total_rows,
        age_hours=round(_file_age_hours(combined), 2),
        cached=not any_fetched,
        unsupported_assets=unsupported,
    )


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

    return info


@router.post("/fetch-gbp", response_model=FetchPricesResponse, tags=["prices"])
def fetch_prices_gbp(body: FetchPricesRequest) -> FetchPricesResponse:
    """Fetch (or return cached) daily GBP prices for all supported assets.

    TL-19: UK jobs require GBP price tables.  This endpoint fetches XRP, BTC,
    ETH, and ADA using Kraken USD prices × official Bank of England (XUDLUSS)
    USD/GBP rates.  No API key required for either source.

    Writes individual per-asset CSVs (cached) and merges them into a single
    ``combined_gbp_{year}.csv``.  The returned ``path`` is the combined file —
    paste it into the "Price table path" field when running a UK job.

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
        info = fetch_all_gbp_prices_for_year(body.year)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return info


@router.get("", response_model=list[PriceFileInfo], tags=["prices"])
def list_prices() -> list[PriceFileInfo]:
    """List all cached price CSV files in PRICES_DIR."""
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    result: list[PriceFileInfo] = []

    # List both NOK and GBP price files.
    all_csv_paths = sorted(
        list(settings.PRICES_DIR.glob("*_nok_*.csv"))
        + list(settings.PRICES_DIR.glob("*_gbp_*.csv"))
    )
    for csv_path in all_csv_paths:
        stem  = csv_path.stem
        # Detect currency separator: _nok_ or _gbp_
        sep = "_nok_" if "_nok_" in stem else "_gbp_" if "_gbp_" in stem else None
        if sep is None:
            continue
        parts = stem.split(sep)
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
