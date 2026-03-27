"""Price-fetching service for the orchestrator.

Fetches historical daily NOK prices for crypto assets by combining free
public APIs.  Four source tiers are supported:

  Tier 1 — Kraken OHLC (USD) × Norges Bank (USD/NOK):
    XRP, BTC, ETH, ADA, LTC  — major assets, highest accuracy.
    Kraken's OHLC window covers only the ~720 most recent daily candles
    from today (~2 years).  For earlier years, CoinGecko market_chart/range
    is used as an automatic fallback (NOK prices returned directly, no
    conversion chain required).

  Tier 2 — OnTheDEX OHLC (XRP) × XRP/USD × USD/NOK:
    Any XRPL IOU token traded on the XRPL DEX (GRIM, xSTIK, SOLO, …).
    Falls back to XRPL.to if OnTheDEX returns no data.

  Tier 3 — Static peg:
    RLUSD → $1.00 USD × USD/NOK (stable-coin, pegged to USD by Ripple).

  Tier 4 — XRPL AMM LP tokens (year-end NAV only):
    Any ``LP.rAmmAccount`` spec.  Uses the XRPL JSON-RPC ``amm_info``
    method to read pool state at the last validated ledger on Dec 31 of
    the requested year.  NAV per LP token is:
        (pool_qty1 × price1_nok + pool_qty2 × price2_nok) / lp_supply
    Pool-asset NOK prices are read from the cached per-asset CSVs
    produced by Tiers 1–3.  Register both underlying assets as workspace
    XRPL assets (or include them in ``extra_xrpl_assets``) to ensure
    their price CSVs exist before the LP NAV is computed.
    The asset_id written to the combined CSV is the LP token's 40-char
    hex currency code, which matches what XRPL transaction parsers emit.

All assets are merged into a single ``combined_nok_{year}.csv`` consumed
by the taxspine CLI via ``--csv-prices``.  Per-asset CSVs are cached so
re-runs for past years are instant.

Why Norges Bank for FX?
-----------------------
Norges Bank (Norwegian central bank) is the official source for FX rates
referenced in Norwegian tax law.  Using these rates aligns with what
Skatteetaten expects when checking valuations.  Business-day-only gaps
are filled by carrying the last known rate forward (standard practice).

OnTheDEX API
------------
- Base URL: https://api.onthedex.live/public/v1
- Endpoint: GET /ohlc?base=SYMBOL.rIssuer&quote=XRP&interval=1440&bars=2000
- interval=1440 = daily (minutes).  bars=2000 covers ~5.5 years.
- Response: {"data": {"ohlc": [{"t": <unix_ts>, "o": .., "h": .., "l": .., "c": ..}]}}
- Returns prices in XRP; we convert via XRP/USD (Kraken) × USD/NOK (Norges Bank).

XRPL.to API (fallback)
-----------------------
- Token lookup: GET /v1/tokens?search=SYMBOL → find md5 matching issuer
- OHLC: GET /v1/ohlc/{md5}?interval=1d&from=YYYY-MM-DD&to=YYYY-MM-DD
- Same XRP-denominated price; same conversion chain.

Caching policy
--------------
- Past years  (year < current year): fetched once, never re-fetched.
- Current year (year == current year): re-fetched if the file is > 24 h old.

CSV format
----------
The combined CSV matches what ``taxspine-*`` CLIs expect via --csv-prices:
    date,asset_id,fiat_currency,price_fiat
    2025-01-01,XRP,NOK,7.4200
    2025-01-01,GRIM,NOK,0.0412
    ...
"""

from __future__ import annotations

import csv
import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from collections.abc import Callable

from .config import settings

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workspace integration hook
# ---------------------------------------------------------------------------
# Set by main.py after both the prices router and workspace store are ready.
# Returns the list of "SYMBOL.rIssuer" specs registered in the workspace.
# Using a callable hook (rather than a direct import of the workspace store)
# avoids circular imports between prices.py → main.py → prices.py.
# ---------------------------------------------------------------------------
_workspace_assets_provider: Callable[[], list[str]] | None = None

# Set by main.py to return the list of registered XRPL account r-addresses.
# When set, every POST /prices/fetch call auto-discovers all IOU tokens held
# by those accounts via XRPL account_lines and includes them in the Tier-2
# price fetch — no manual xrpl_assets registration required.
_workspace_accounts_provider: Callable[[], list[str]] | None = None

# ── Tier-1 assets: Kraken USD pairs ──────────────────────────────────────────

_KRAKEN_USD_PAIR: dict[str, str] = {
    "XRP": "XRPUSD",
    "BTC": "XBTUSD",   # Kraken uses XBT internally
    "ETH": "ETHUSD",
    "ADA": "ADAUSD",
    "LTC": "LTCUSD",
}

_ALL_KRAKEN_ASSETS: list[str] = list(_KRAKEN_USD_PAIR.keys())

# Binance USDT pairs for Tier-1 assets — primary fallback when Kraken's OHLC
# window (~720 days from today) does not cover the requested year.
# Binance klines: free, no API key, daily data available since asset listing.
# USD close price from Binance × USD/NOK from Norges Bank → NOK CSV.
_BINANCE_PAIRS: dict[str, str] = {
    "XRP": "XRPUSDT",
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "ADA": "ADAUSDT",
    "LTC": "LTCUSDT",
}

# CoinCap asset IDs — secondary fallback (may require key in 2025+).
_COINCAP_COIN_IDS: dict[str, str] = {
    "XRP": "ripple",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "ADA": "cardano",
    "LTC": "litecoin",
}

# CoinGecko coin IDs for Tier-1 assets — tertiary fallback if Binance + CoinCap fail.
_COINGECKO_COIN_IDS: dict[str, str] = {
    "XRP": "ripple",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "ADA": "cardano",
    "LTC": "litecoin",
}

# ── Live spot price cache (GET /prices/spot) ──────────────────────────────────
# Kraken Ticker returns result keys that differ from the request pair names.
# This map translates asset symbol → expected result key in the ticker response.
_KRAKEN_TICKER_RESULT_KEYS: dict[str, str] = {
    "XRP": "XXRPZUSD",
    "BTC": "XXBTZUSD",
    "ETH": "XETHZUSD",
    "ADA": "ADAUSD",
    "LTC": "XLTCZUSD",
}

# Module-level in-memory cache — populated by fetch_spot_prices_nok().
# Single-process (uvicorn --workers 1), so no locking needed.
_spot_cache: dict[str, Decimal] | None = None
_spot_cache_ts: float = 0.0
_SPOT_CACHE_TTL: int = 300   # 5 minutes

# ── Tier-3 static pegs ───────────────────────────────────────────────────────

# Assets pegged to a fixed USD price.  Value is the USD peg price.
# Applied for every calendar day in the requested year.
_STATIC_USD_PEGS: dict[str, Decimal] = {
    "RLUSD": Decimal("1.0"),   # Ripple USD stablecoin, pegged 1:1 to USD
}

# ── OnTheDEX API ─────────────────────────────────────────────────────────────

_ONTHEDEX_BASE   = "https://api.onthedex.live/public/v1"
_XRPLTO_BASE     = "https://api.xrpl.to/v1"
_COINGECKO_BASE  = "https://api.coingecko.com/api/v3"
_COINCAP_BASE    = "https://api.coincap.io/v2"
_BINANCE_BASE    = "https://api.binance.com/api/v3"

_STALE_HOURS = 24   # re-fetch current-year files if older than this

# ── Tier-4: XRPL AMM LP tokens ───────────────────────────────────────────────

_XRPL_PUBLIC_RPC   = "https://xrplcluster.com"
_XRPL_EPOCH_OFFSET = 946684800   # XRPL epoch = Jan 1 2000; offset from Unix epoch (Jan 1 1970)


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/prices", tags=["prices"])


# ── Request / response models ─────────────────────────────────────────────────


class FetchPricesRequest(BaseModel):
    """Request body for POST /prices/fetch."""

    year: int
    # Optional list of XRPL IOU asset specs in "SYMBOL.rIssuerAddress" format.
    # Each will be fetched from OnTheDEX (primary) or XRPL.to (fallback) and
    # converted via XRP/USD × USD/NOK.  Pass "RLUSD" (no issuer) to include
    # the static USD peg.  Kraken assets (XRP, BTC, ETH, ADA, LTC) are always
    # included and need not be listed here.
    extra_xrpl_assets: list[str] = []


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
    reason: str


class FetchPricesResponse(PriceFileInfo):
    """Response body for POST /prices/fetch.

    ``unsupported_assets`` lists assets for which no price data could be found.
    These will appear as UNRESOLVED in the tax CLI output.  Source prices
    manually and merge rows into the combined CSV if needed.
    """

    unsupported_assets: list[UnsupportedAssetNote] = []


# ── Path helpers ──────────────────────────────────────────────────────────────


def _asset_csv_path(asset: str, year: int) -> Path:
    """Per-asset Kraken/peg cache file: ``xrp_nok_2025.csv``."""
    return settings.PRICES_DIR / f"{asset.lower()}_nok_{year}.csv"


def _xrpl_iou_csv_path(symbol: str, issuer: str, year: int) -> Path:
    """Per-asset XRPL IOU cache file: ``xrpl_solo_rsolo2s_nok_2025.csv``.

    Includes the first 8 characters of the issuer address so that two tokens
    with the same symbol but different issuers get separate cache files.
    """
    issuer_tag = issuer[:8].lower()
    return settings.PRICES_DIR / f"xrpl_{symbol.lower()}_{issuer_tag}_nok_{year}.csv"


def _combined_csv_path(year: int) -> Path:
    """Merged file passed to the CLI: ``combined_nok_2025.csv``."""
    return settings.PRICES_DIR / f"combined_nok_{year}.csv"


def _asset_csv_path_gbp(asset: str, year: int) -> Path:
    """Per-asset GBP cache file: ``xrp_gbp_2025.csv``."""
    return settings.PRICES_DIR / f"{asset.lower()}_gbp_{year}.csv"


def _combined_csv_path_gbp(year: int) -> Path:
    """Merged GBP file: ``combined_gbp_{year}.csv``."""
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


# ── Asset classification helpers ──────────────────────────────────────────────


def _parse_xrpl_asset(spec: str) -> tuple[str, str | None]:
    """Parse an asset spec into (symbol, issuer_or_None).

    ``"SOLO.rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz"`` → ``("SOLO", "rsoLo2S1...")``
    ``"RLUSD"``                                       → ``("RLUSD", None)``
    ``"solo.rIssuer"``                               → ``("SOLO", "rIssuer")``
    """
    if "." in spec:
        parts = spec.split(".", 1)
        symbol  = parts[0].upper().strip()
        issuer  = parts[1].strip()
        return symbol, issuer if issuer else None
    return spec.upper().strip(), None


def _classify_asset(symbol: str, issuer: str | None) -> str:
    """Return the price source for an asset.

    Returns one of: ``"kraken"``, ``"static_peg"``, ``"onthedex"``, ``"unknown"``.

    Logic:
    - Symbols covered by Kraken (XRP, BTC, ETH, ADA, LTC) always use Kraken,
      regardless of issuer.  This handles GateHub IOUs: BTC.rGatehub → Kraken BTC.
    - Static pegs (RLUSD) use the fixed USD peg.
    - Everything else with an issuer address goes to OnTheDEX → XRPL.to fallback.
    - Assets without an issuer and not in the above lists are unknown.
    """
    if symbol in _KRAKEN_USD_PAIR:
        return "kraken"
    if symbol in _STATIC_USD_PEGS:
        return "static_peg"
    if symbol == "LP" and issuer is not None:
        return "lp_token"
    if issuer is not None:
        return "onthedex"
    return "unknown"


# ── Tier-3: static peg helpers ────────────────────────────────────────────────


def _generate_static_peg_usd_rows(usd_price: Decimal, year: int) -> dict[str, Decimal]:
    """Return {date_str: usd_price} for every calendar day in *year*."""
    start   = datetime.date(year, 1, 1)
    end     = datetime.date(year, 12, 31)
    rows: dict[str, Decimal] = {}
    current = start
    while current <= end:
        rows[current.isoformat()] = usd_price
        current += datetime.timedelta(days=1)
    return rows


def _write_usd_as_nok_csv(
    symbol: str,
    usd_prices: dict[str, Decimal],
    nok_rates: dict[str, Decimal],
    dest: Path,
) -> None:
    """Convert USD prices to NOK and write per-asset CSV.

    Shared by both the static peg path (RLUSD) and could be reused later.
    """
    rows: list[tuple[str, str]] = []
    for date_str, usd_price in sorted(usd_prices.items()):
        nok_rate = nok_rates.get(date_str)
        if nok_rate is None:
            continue
        nok_price = (usd_price * nok_rate).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        rows.append((date_str, str(nok_price)))

    if not rows:
        return

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, symbol, "NOK", price])


# ── Tier-2: OnTheDEX helpers ──────────────────────────────────────────────────


def _fetch_onthedex_xrp_prices(
    symbol: str,
    issuer: str,
    year: int,
) -> dict[str, Decimal]:
    """Fetch daily close prices in XRP from OnTheDEX for one XRPL IOU token.

    Uses interval=1440 (daily, in minutes) with bars=2000 covering ~5.5 years.
    The ``ending`` parameter anchors the window to 31 Dec of the requested year.

    Returns ``{date_str: close_price_xrp}`` for dates within *year*.
    Returns ``{}`` on any error — caller handles the fallback.
    """
    tz     = datetime.timezone.utc
    ending = int(datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz).timestamp())

    params = urllib.parse.urlencode({
        "base":     f"{symbol}.{issuer}",
        "quote":    "XRP",
        "interval": "1440",   # 1 day in minutes
        "bars":     "2000",   # covers ~5.5 years from ending
        "ending":   str(ending),
    })
    url = f"{_ONTHEDEX_BASE}/ohlc?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception:
        return {}

    if not isinstance(body, dict) or body.get("error"):
        return {}

    ohlc_list = body.get("data", {}).get("ohlc") if isinstance(body.get("data"), dict) else None
    if not isinstance(ohlc_list, list):
        return {}

    prices: dict[str, Decimal] = {}
    for candle in ohlc_list:
        ts    = candle.get("t")
        close = candle.get("c")
        if ts is None or close is None:
            continue
        try:
            dt = datetime.datetime.fromtimestamp(int(ts), tz=tz)
            if dt.year != year:
                continue
            prices[dt.strftime("%Y-%m-%d")] = Decimal(str(close))
        except Exception:
            continue

    return prices


def _fetch_xrplto_token_id(symbol: str, issuer: str) -> str | None:
    """Look up the XRPL.to token identifier (md5) for (symbol, issuer).

    Calls GET /v1/tokens?search=SYMBOL and finds the entry whose issuer
    matches exactly.  Returns the ``md5`` field, or None if not found.
    """
    url = f"{_XRPLTO_BASE}/tokens?search={urllib.parse.quote(symbol)}&limit=100"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except Exception:
        return None

    if not isinstance(body, dict):
        return None

    tokens = body.get("tokens")
    if not isinstance(tokens, list):
        return None

    for token in tokens:
        if (
            token.get("currency", "").upper() == symbol.upper()
            and token.get("issuer", "") == issuer
        ):
            return token.get("md5") or token.get("id") or token.get("slug")

    return None


def _fetch_xrplto_xrp_prices(
    symbol: str,
    issuer: str,
    year: int,
) -> dict[str, Decimal]:
    """Fallback: fetch daily XRP prices from XRPL.to for one XRPL IOU token.

    Two-step: (1) look up the token's md5 identifier, (2) fetch OHLC.
    Returns ``{date_str: close_price_xrp}``, or ``{}`` on any error.
    """
    token_id = _fetch_xrplto_token_id(symbol, issuer)
    if not token_id:
        return {}

    tz = datetime.timezone.utc
    params = urllib.parse.urlencode({
        "interval": "1d",
        "from":     f"{year}-01-01",
        "to":       f"{year}-12-31",
    })
    url = f"{_XRPLTO_BASE}/ohlc/{token_id}?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except Exception:
        return {}

    if not isinstance(body, dict):
        return {}

    # XRPL.to response structure: try common field names
    ohlc_data = (
        body.get("ohlc")
        or body.get("data")
        or body.get("candles")
        or []
    )
    if not isinstance(ohlc_data, list):
        return {}

    prices: dict[str, Decimal] = {}
    for candle in ohlc_data:
        ts    = candle.get("t") or candle.get("time") or candle.get("timestamp")
        close = candle.get("c") or candle.get("close")
        if ts is None or close is None:
            continue
        try:
            if isinstance(ts, str) and ("T" in ts or "-" in ts):
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = datetime.datetime.fromtimestamp(int(ts), tz=tz)
            if dt.year != year:
                continue
            prices[dt.strftime("%Y-%m-%d")] = Decimal(str(close))
        except Exception:
            continue

    return prices


def _coingecko_search_coin_id(symbol: str) -> str | None:
    """Return the CoinGecko coin ID for *symbol*, or None if not found.

    Searches ``/search?query=SYMBOL``, prefers an exact symbol match over
    a name/alias match, and returns the first result.  Returns None on any
    network error or if no coins are found.

    Rate limit: CoinGecko free tier allows 30 req/min.  This function is
    called at most once per unknown token per price-fetch run, so staying
    within the limit is straightforward.
    """
    url = f"{_COINGECKO_BASE}/search?query={urllib.parse.quote(symbol)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except Exception:
        return None

    if not isinstance(body, dict):
        return None

    coins = body.get("coins", [])
    if not coins:
        return None

    # Prefer exact symbol match (case-insensitive)
    exact = [c for c in coins if c.get("symbol", "").upper() == symbol.upper()]
    if exact:
        return str(exact[0]["id"])
    return str(coins[0]["id"])


def _fetch_coingecko_nok_prices(symbol: str, year: int) -> dict[str, Decimal]:
    """Tier 2c: fetch daily NOK prices from CoinGecko for *symbol* in *year*.

    Uses ``market_chart/range`` which returns granular (~daily) data for
    ranges > 90 days.  NOK prices are returned directly — no XRP conversion
    required, so this path is independent of Kraken/Norges Bank availability.

    Returns ``{date_str: price_nok}`` or ``{}`` on any error or if the coin
    is not listed on CoinGecko.
    """
    coin_id = _coingecko_search_coin_id(symbol)
    if not coin_id:
        return {}

    tz      = datetime.timezone.utc
    from_ts = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp())
    to_ts   = int(datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz).timestamp())

    url = (
        f"{_COINGECKO_BASE}/coins/{urllib.parse.quote(coin_id)}/market_chart/range"
        f"?vs_currency=nok&from={from_ts}&to={to_ts}"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception:
        return {}

    raw_prices = body.get("prices", [])
    result: dict[str, Decimal] = {}
    for entry in raw_prices:
        if len(entry) < 2:
            continue
        try:
            ts_ms = int(entry[0])
            price = Decimal(str(entry[1]))
            dt    = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=tz)
            if dt.year != year:
                continue
            result[dt.strftime("%Y-%m-%d")] = price
        except Exception:
            continue

    return result


def _fetch_and_write_xrpl_iou(
    symbol: str,
    issuer: str,
    year: int,
    xrp_usd_prices: dict[str, Decimal],
    nok_rates: dict[str, Decimal],
    dest: Path,
) -> bool:
    """Fetch XRPL IOU prices (OnTheDEX → XRPL.to → CoinGecko fallback) and write NOK CSV.

    Conversion: close_xrp × xrp_usd × usd_nok = asset_nok

    Returns True if data was written, False if no price data was found.
    """
    # Tier 2a: OnTheDEX → Tier 2b: XRPL.to (both XRP-denominated)
    xrp_prices = _fetch_onthedex_xrp_prices(symbol, issuer, year)
    if not xrp_prices:
        xrp_prices = _fetch_xrplto_xrp_prices(symbol, issuer, year)

    if xrp_prices:
        rows: list[tuple[str, str]] = []
        for date_str, xrp_price in sorted(xrp_prices.items()):
            xrp_usd  = xrp_usd_prices.get(date_str)
            nok_rate = nok_rates.get(date_str)
            if xrp_usd is None or nok_rate is None:
                continue
            nok_price = (xrp_price * xrp_usd * nok_rate).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            rows.append((date_str, str(nok_price)))

        if not rows:
            return False

        with dest.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
            for date_str, price in rows:
                writer.writerow([date_str, symbol, "NOK", price])

        return True

    # Tier 2c: CoinGecko — NOK direct, no XRP conversion chain required.
    # Used when the token appears on centralised exchanges but not on XRPL DEXes,
    # or when both DEX sources return no data for the requested year.
    _log.info("XRPL DEX sources empty for %s — trying CoinGecko (Tier 2c)", symbol)
    nok_prices = _fetch_coingecko_nok_prices(symbol, year)
    if not nok_prices:
        return False

    cg_rows: list[tuple[str, str]] = []
    for date_str, nok_price in sorted(nok_prices.items()):
        quantized = nok_price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        cg_rows.append((date_str, str(quantized)))

    if not cg_rows:
        return False

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in cg_rows:
            writer.writerow([date_str, symbol, "NOK", price])

    return True


# ── Tier-4: XRPL AMM LP token helpers ────────────────────────────────────────


def _xrpl_rpc(method: str, params: dict, *, timeout: int = 15) -> dict:
    """Send a JSON-RPC request to the XRPL public cluster and return ``result``.

    Raises ``RuntimeError`` on network failure or when the response carries
    an error status.  Only the ``"result"`` sub-dict is returned so callers
    do not have to unwrap the envelope.
    """
    payload = json.dumps({"method": method, "params": [params]}).encode("utf-8")
    req = urllib.request.Request(
        _XRPL_PUBLIC_RPC,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "taxspine-orchestrator/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"XRPL RPC '{method}' failed: {exc}") from exc

    if not isinstance(body, dict):
        raise RuntimeError(
            f"XRPL RPC '{method}' returned unexpected response type {type(body).__name__}"
        )

    result = body.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError(
            f"XRPL RPC '{method}' returned non-dict result: {type(result).__name__}"
        )
    if result.get("status") == "error":
        raise RuntimeError(
            f"XRPL RPC '{method}' error: "
            f"{result.get('error_message') or result.get('error', result)}"
        )
    return result


def _xrpl_year_end_ledger_index(year: int) -> int:
    """Return the index of the last XRPL ledger validated on Dec 31 of *year* (UTC).

    Strategy (uses at most ~15 XRPL API calls in practice):
    1. Return cached index from ``xrpl_ledger_dec31_{year}.txt`` if present.
    2. Validate that Dec 31 of *year* has already occurred.
    3. Fetch the current validated ledger for a close_time reference point.
    4. Estimate the Dec-31 ledger index using ~3.7 s/ledger average.
    5. Refine with up to 3 iterative corrections.
    6. Fine-tune walk (±1 ledger at a time, max 60 steps) to land exactly on
       the last ledger whose close_time falls on or before Dec 31 23:59:59 UTC.
    7. Cache the result for past years (immutable once Dec 31 has passed).
    """
    cache_file = settings.PRICES_DIR / f"xrpl_ledger_dec31_{year}.txt"
    if cache_file.exists():
        try:
            return int(cache_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pass  # corrupt cache — recompute

    today = datetime.date.today()
    if today < datetime.date(year, 12, 31):
        raise RuntimeError(
            f"Cannot find year-end ledger for {year}: "
            f"December 31, {year} has not yet occurred."
        )

    tz = datetime.timezone.utc
    # We want the last ledger before Jan 1 year+1 00:00:00 UTC
    end_of_dec31  = int(datetime.datetime(year + 1, 1, 1, tzinfo=tz).timestamp()) - 1
    # Midpoint of Dec 31 — used as convergence target for the estimate step
    target_unix   = int(datetime.datetime(year, 12, 31, 12, 0, 0, tzinfo=tz).timestamp())

    # Step 1: fetch current validated ledger
    cur        = _xrpl_rpc("ledger", {"ledger_index": "validated", "transactions": False, "expand": False})
    cur_idx    = int(cur["ledger"]["ledger_index"])
    cur_close  = int(cur["ledger"]["close_time"]) + _XRPL_EPOCH_OFFSET

    # Step 2: initial estimate (~3.7 s/ledger average for XRPL)
    _AVG_SECS  = 3.7
    idx = max(32570, cur_idx - int((cur_close - target_unix) / _AVG_SECS))

    # Step 3: up to 3 iterative corrections
    for _ in range(3):
        r          = _xrpl_rpc("ledger", {"ledger_index": idx, "transactions": False, "expand": False})
        close_unix = int(r["ledger"]["close_time"]) + _XRPL_EPOCH_OFFSET
        diff       = target_unix - close_unix
        if abs(diff) < 60:
            break
        idx = max(32570, idx + int(diff / _AVG_SECS))

    # Step 4: fine-tune walk — find the exact last ledger on Dec 31
    for _ in range(60):
        r          = _xrpl_rpc("ledger", {"ledger_index": idx, "transactions": False, "expand": False})
        close_unix = int(r["ledger"]["close_time"]) + _XRPL_EPOCH_OFFSET
        if close_unix > end_of_dec31:
            idx -= 1
            continue
        # close_unix is on Dec 31 — check if idx+1 is also on Dec 31
        r2          = _xrpl_rpc("ledger", {"ledger_index": idx + 1, "transactions": False, "expand": False})
        close_unix2 = int(r2["ledger"]["close_time"]) + _XRPL_EPOCH_OFFSET
        if close_unix2 <= end_of_dec31:
            idx += 1
            continue
        break  # idx is the last ledger on Dec 31

    # Cache for past years (Dec 31 is immutable once it has passed)
    current_year = datetime.date.today().year
    if year < current_year:
        try:
            settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(str(idx), encoding="utf-8")
        except OSError:
            pass  # non-fatal — will recompute next time

    return idx


def _parse_amm_asset(amount: object) -> tuple[str, str | None, Decimal]:
    """Parse an AMM pool ``amount`` field into ``(symbol, issuer_or_None, quantity)``.

    XRPL represents pool asset amounts as:
    - ``str``  → XRP in drops  (1 XRP = 1 000 000 drops)
    - ``dict`` → IOU: ``{"currency": str, "issuer": str, "value": str}``
    """
    if isinstance(amount, str):
        # XRP drops — divide by 1 000 000 to get XRP
        return "XRP", None, Decimal(amount) / Decimal("1000000")
    if isinstance(amount, dict):
        return (
            str(amount["currency"]),
            str(amount["issuer"]),
            Decimal(str(amount["value"])),
        )
    raise ValueError(f"Unexpected AMM asset format: {amount!r}")


def _lp_csv_path(amm_account: str, year: int) -> Path:
    """Per-LP cache file: ``lp_ramm1234_nok_2025.csv``.

    The filename includes the first 8 characters of the AMM account address
    (lower-cased) so that different pools get separate cache files.
    """
    return settings.PRICES_DIR / f"lp_{amm_account[:8].lower()}_nok_{year}.csv"


def _read_dec31_nok_price(symbol: str, issuer: str | None, year: int) -> Decimal | None:
    """Read the Dec 31 NOK price for *symbol* from its cached per-asset CSV.

    Returns ``None`` if the file does not exist or contains no Dec-31 row.
    Uses the Kraken CSV for native Kraken assets (XRP, BTC, ETH, ADA, LTC)
    and the XRPL IOU CSV for everything else.
    """
    if symbol in _KRAKEN_USD_PAIR:
        path = _asset_csv_path(symbol, year)
    elif issuer is not None:
        path = _xrpl_iou_csv_path(symbol, issuer, year)
    else:
        return None

    if not path.exists():
        return None

    dec31_str = f"{year}-12-31"
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date") == dec31_str:
                    return Decimal(row["price_fiat"])
    except Exception:
        return None

    return None


def _fetch_and_write_lp_token(amm_account: str, year: int, dest: Path) -> bool:
    """Query XRPL AMM state at Dec 31 year-end, compute NAV, and write a NOK CSV.

    NAV per LP token = (pool_qty1 × nok1 + pool_qty2 × nok2) / lp_supply

    The asset_id written to the CSV is the LP token's 40-char hex currency
    code (from ``amm_info`` → ``lp_token.currency``).  This matches what
    XRPL transaction parsers emit for LP token balances, so the combined
    price CSV aligns with the generic-events CSV asset identifiers.

    Returns ``True`` if the CSV was written, ``False`` on any error.
    """
    try:
        ledger_idx = _xrpl_year_end_ledger_index(year)
    except RuntimeError:
        return False

    try:
        result = _xrpl_rpc(
            "amm_info",
            {"amm_account": amm_account, "ledger_index": ledger_idx},
        )
    except RuntimeError:
        return False

    amm = result.get("amm")
    if not isinstance(amm, dict):
        return False

    try:
        sym1, iss1, qty1 = _parse_amm_asset(amm["amount"])
        sym2, iss2, qty2 = _parse_amm_asset(amm["amount2"])
        lp_base  = amm.get("lp_token", {})
        lp_supply = Decimal(str(lp_base.get("value", "0")))
        lp_currency = str(lp_base.get("currency", amm_account))
    except (KeyError, ValueError, Exception):
        return False

    if lp_supply <= 0:
        return False

    price1 = _read_dec31_nok_price(sym1, iss1, year)
    price2 = _read_dec31_nok_price(sym2, iss2, year)

    if price1 is None or price2 is None:
        return False

    nok_per_lp = ((qty1 * price1 + qty2 * price2) / lp_supply).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )

    dec31_str = f"{year}-12-31"
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        writer.writerow([dec31_str, lp_currency, "NOK", str(nok_per_lp)])

    return True


# ── XRPL account trust-line discovery ────────────────────────────────────────


def _decode_xrpl_currency(code: str) -> str:
    """Convert a raw XRPL currency code to a human-readable symbol.

    XRPL stores non-standard token names as 40-char hex strings
    (UTF-8 bytes zero-padded to 20 bytes).  Standard 3-char ISO codes
    ("XRP", "USD") are returned as-is.

    Examples::

        "534F4C4F000000000000000000000000000000000000" → "SOLO"
        "785354494B00000000000000000000000000000000" → "xSTIK"
        "USD" → "USD"
    """
    code = code.strip()
    if len(code) == 40 and all(c in "0123456789abcdefABCDEF" for c in code):
        try:
            raw = bytes.fromhex(code).rstrip(b"\x00")
            decoded = raw.decode("utf-8").strip()
            return decoded if decoded else code
        except (ValueError, UnicodeDecodeError):
            return code   # return hex as-is; caller will route to "unknown"
    return code


def _fetch_account_trust_lines(account: str) -> list[str]:
    """Return ``SYMBOL.rISSUER`` specs for all non-zero IOU balances on *account*.

    Calls XRPL ``account_lines`` and paginates via ``marker`` until all
    trust lines are collected (max 20 pages × 400 lines = 8 000 trust lines,
    which is far beyond any real account).

    Returns an empty list on any network or API error — the caller degrades
    gracefully (skips auto-discovery, manual xrpl_assets still work).

    Currency codes are decoded via :func:`_decode_xrpl_currency` so that
    non-standard XRPL names (SOLO, xSTIK, SHROOMIES …) appear as readable
    symbols in the asset spec, matching what the taxspine CLI emits.
    """
    specs: list[str] = []
    marker = None

    for _ in range(20):   # pagination guard — real accounts rarely exceed 2–3 pages
        params: dict = {
            "account":       account,
            "ledger_index":  "validated",
            "limit":         400,
        }
        if marker is not None:
            params["marker"] = marker

        try:
            result = _xrpl_rpc("account_lines", params, timeout=15)
        except RuntimeError as exc:
            _log.warning("account_lines failed for %s: %s", account, exc)
            break

        for line in result.get("lines", []):
            currency = line.get("currency", "")
            issuer   = line.get("account", "")   # 'account' field = issuer address
            balance  = line.get("balance", "0")

            # Skip zero-balance trust lines (authorised but no holding)
            try:
                if Decimal(balance) == Decimal("0"):
                    continue
            except Exception:
                continue

            symbol = _decode_xrpl_currency(currency)
            if not symbol or not issuer:
                continue
            if symbol.upper() == "XRP":
                continue   # XRP is native, not an IOU

            specs.append(f"{symbol}.{issuer}")

        marker = result.get("marker")
        if marker is None:
            break

    return specs


# ── Top-level entry points ─────────────────────────────────────────────────────


def fetch_all_prices_for_year(
    year: int,
    extra_xrpl_assets: list[str] | None = None,
) -> FetchPricesResponse:
    """Fetch (or return cached) daily NOK prices for all supported assets.

    Always fetches: XRP, BTC, ETH, ADA, LTC (Kraken × Norges Bank).

    ``extra_xrpl_assets`` is a list of ``"SYMBOL.rIssuerAddress"`` strings for
    XRPL IOU tokens.  Each is routed as follows:

    - Symbol in _KRAKEN_USD_PAIR (e.g. BTC, ETH, LTC as GateHub IOUs):
        → skipped here, already covered by Kraken tier.
    - Symbol in _STATIC_USD_PEGS (RLUSD):
        → pegged to fixed USD price × USD/NOK.
    - Everything else with an issuer address:
        → OnTheDEX (primary) → XRPL.to (fallback); XRP-denominated × XRP/USD × USD/NOK.
    - Symbol without issuer and not a Kraken/peg asset:
        → reported as unsupported; cannot be priced automatically.

    Past years are cached indefinitely.  Current year is re-fetched if >24 h old.
    """
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    extra_xrpl_assets = list(extra_xrpl_assets or [])

    # ── Auto-discover tokens from registered XRPL accounts ───────────────────
    # Pull trust-line holdings for every r-address in the workspace.  This
    # ensures ALL IOU tokens the user actually holds are priced without
    # requiring manual xrpl_assets registration for each one.
    # Manually registered xrpl_assets are passed in by the caller (route
    # handler or services.py); this step only adds account-discovered tokens
    # not already in the list.
    if _workspace_accounts_provider is not None:
        _seen_discovered: set[str] = set(extra_xrpl_assets)
        for _acct in _workspace_accounts_provider():
            _discovered = _fetch_account_trust_lines(_acct)
            for _spec in _discovered:
                if _spec not in _seen_discovered:
                    extra_xrpl_assets.append(_spec)
                    _seen_discovered.add(_spec)
                    _log.info("Auto-discovered XRPL token from %s: %s", _acct, _spec)

    any_fetched      = False
    available_paths: list[Path] = []
    failed_assets:   list[str]  = []
    _fetch_errors:   dict[str, str] = {}  # asset → error message for diagnostics

    # ── Step 1: Kraken assets (CoinGecko fallback for historical years) ──────
    # Kraken OHLC returns the 720 most recent daily candles from today (~2 years).
    # For years outside that window, fall back to CoinGecko market_chart/range,
    # which has no rolling-window limit and returns NOK prices directly.
    for asset in _ALL_KRAKEN_ASSETS:
        dest = _asset_csv_path(asset, year)
        if _needs_fetch(dest, year):
            pair_usd = _KRAKEN_USD_PAIR[asset]
            try:
                _fetch_and_write(pair_usd, asset, year, dest)
                any_fetched = True
            except RuntimeError as _kraken_exc:
                # Kraken OHLC window doesn't cover this year.
                # Try three fallbacks in order; stop at the first that succeeds.
                _fallback_ok   = False
                _fallback_errs: list[str] = [f"Kraken: {_kraken_exc}"]

                # Fallback 1: Binance klines (USD close × Norges Bank).
                # Free, no API key, covers history since pair listing (~2017-2018).
                binance_pair = _BINANCE_PAIRS.get(asset)
                if not _fallback_ok and binance_pair is not None:
                    _log.info(
                        "Kraken unavailable for %s %s — trying Binance "
                        "(pair=%s). Kraken error: %s",
                        asset, year, binance_pair, _kraken_exc,
                    )
                    try:
                        _fetch_and_write_binance(binance_pair, asset, year, dest)
                        any_fetched  = True
                        _fallback_ok = True
                        _log.info("Binance fallback succeeded for %s %s", asset, year)
                    except RuntimeError as _exc2:
                        _fallback_errs.append(f"Binance: {_exc2}")
                        _log.warning("Binance fallback failed for %s %s: %s", asset, year, _exc2)

                # Fallback 2: CoinCap (USD daily × Norges Bank).
                coincap_id = _COINCAP_COIN_IDS.get(asset)
                if not _fallback_ok and coincap_id is not None:
                    _log.info("Trying CoinCap fallback for %s %s (id=%s)", asset, year, coincap_id)
                    try:
                        _fetch_and_write_coincap(coincap_id, asset, year, dest)
                        any_fetched  = True
                        _fallback_ok = True
                        _log.info("CoinCap fallback succeeded for %s %s", asset, year)
                    except RuntimeError as _exc3:
                        _fallback_errs.append(f"CoinCap: {_exc3}")
                        _log.warning("CoinCap fallback failed for %s %s: %s", asset, year, _exc3)

                # Fallback 3: CoinGecko NOK direct (no USD conversion).
                cg_id = _COINGECKO_COIN_IDS.get(asset)
                if not _fallback_ok and cg_id is not None:
                    _log.info("Trying CoinGecko fallback for %s %s (id=%s)", asset, year, cg_id)
                    n_rows = _fetch_and_write_coingecko_nok(cg_id, asset, year, dest)
                    if n_rows > 0:
                        any_fetched  = True
                        _fallback_ok = True
                        _log.info("CoinGecko fallback succeeded for %s %s: %d rows", asset, year, n_rows)
                    else:
                        _fallback_errs.append("CoinGecko: no data returned")

                if not _fallback_ok:
                    failed_assets.append(asset)
                    _fetch_errors[asset] = "; ".join(_fallback_errs)
                    continue
        if dest.exists():
            available_paths.append(dest)

    # ── Step 2: XRPL IOU assets ──────────────────────────────────────────────
    unsupported: list[UnsupportedAssetNote] = []

    if extra_xrpl_assets:
        # Pre-classify all requested assets to decide whether we need conversion
        # rates (XRP/USD + USD/NOK).  Kraken assets and unknowns don't need them.
        _needs_conversion = any(
            _classify_asset(*_parse_xrpl_asset(s)) in ("onthedex", "static_peg")
            for s in extra_xrpl_assets
        )

        xrp_usd_prices: dict[str, Decimal] = {}
        nok_rates:       dict[str, Decimal] = {}

        if _needs_conversion:
            try:
                xrp_usd_prices = _fetch_kraken_usd_prices("XRPUSD", year)
            except RuntimeError:
                pass  # assets that need it will be marked unsupported below

            try:
                raw_fx    = _fetch_norges_bank_usd_nok(year)
                nok_rates = _fill_calendar_gaps(raw_fx, year)
            except RuntimeError:
                pass

        seen_specs: set[str] = set()
        for spec in extra_xrpl_assets:
            if spec in seen_specs:
                continue
            seen_specs.add(spec)

            symbol, issuer = _parse_xrpl_asset(spec)
            classification = _classify_asset(symbol, issuer)

            if classification == "kraken":
                # Already in Tier-1; nothing to do.
                continue

            if classification == "static_peg":
                dest = _asset_csv_path(symbol, year)
                if _needs_fetch(dest, year):
                    if nok_rates:
                        usd_price  = _STATIC_USD_PEGS[symbol]
                        # TL-07: Warn whenever a static peg is used for tax
                        # computation.  If the peg breaks (e.g. RLUSD de-pegs),
                        # computed tax figures will be wrong with no other
                        # indication.  The WARNING is emitted once per fetch so
                        # it appears in job execution logs and container stdout.
                        _log.warning(
                            "TL-07: %s is valued using a static USD peg "
                            "(USD %.4f). If the peg breaks, tax figures for "
                            "this asset will be incorrect. Verify the current "
                            "market price before filing.",
                            symbol, usd_price,
                        )
                        usd_prices = _generate_static_peg_usd_rows(usd_price, year)
                        _write_usd_as_nok_csv(symbol, usd_prices, nok_rates, dest)
                        any_fetched = True
                    else:
                        unsupported.append(UnsupportedAssetNote(
                            asset=spec,
                            reason=(
                                f"Static peg for {symbol} could not be written: "
                                "Norges Bank USD/NOK rates unavailable."
                            ),
                        ))
                        continue
                if dest.exists():
                    available_paths.append(dest)
                continue

            if classification == "onthedex":
                assert issuer is not None
                dest = _xrpl_iou_csv_path(symbol, issuer, year)
                if _needs_fetch(dest, year):
                    if xrp_usd_prices and nok_rates:
                        ok = _fetch_and_write_xrpl_iou(
                            symbol, issuer, year, xrp_usd_prices, nok_rates, dest
                        )
                        if ok:
                            any_fetched = True
                        else:
                            unsupported.append(UnsupportedAssetNote(
                                asset=spec,
                                reason=(
                                    f"No price data found on OnTheDEX or XRPL.to "
                                    f"for {symbol} in {year}. "
                                    "The token may have had no DEX trades that year."
                                ),
                            ))
                            continue
                    else:
                        unsupported.append(UnsupportedAssetNote(
                            asset=spec,
                            reason=(
                                "XRP/USD or USD/NOK rates unavailable; "
                                f"cannot convert {symbol} from XRP to NOK."
                            ),
                        ))
                        continue
                if dest.exists():
                    available_paths.append(dest)
                continue

            if classification == "lp_token":
                continue  # Deferred to Step 2b — LP NAV needs pool-asset CSVs first

            # classification == "unknown"
            unsupported.append(UnsupportedAssetNote(
                asset=spec,
                reason=(
                    f"Cannot price '{symbol}': no issuer address provided "
                    "and symbol is not a Kraken asset or static peg. "
                    "Pass as 'SYMBOL.rIssuerAddress' to enable DEX price lookup."
                ),
            ))

        # ── Step 2b: LP tokens (deferred – NAV requires pool-asset CSVs) ─────
        # Processed after all XRPL IOU assets so that pool-asset price CSVs
        # (from Steps 1 and 2) are available for the NAV calculation.
        _seen_lp: set[str] = set()
        for spec in extra_xrpl_assets:
            _sym, _iss = _parse_xrpl_asset(spec)
            if _classify_asset(_sym, _iss) != "lp_token":
                continue
            if spec in _seen_lp:
                continue
            _seen_lp.add(spec)

            if _iss is None:
                unsupported.append(UnsupportedAssetNote(
                    asset=spec,
                    reason=(
                        "LP token spec must include the AMM account address: "
                        "'LP.rAmmAccountAddress'."
                    ),
                ))
                continue

            dest = _lp_csv_path(_iss, year)
            if _needs_fetch(dest, year):
                ok = _fetch_and_write_lp_token(_iss, year, dest)
                if ok:
                    any_fetched = True
                else:
                    unsupported.append(UnsupportedAssetNote(
                        asset=spec,
                        reason=(
                            f"Could not compute NAV for LP token at AMM {_iss}. "
                            "Ensure the pool exists on XRPL and that both pool "
                            f"assets have cached NOK price data for {year}. "
                            "Register the underlying assets as workspace XRPL assets."
                        ),
                    ))
                    continue
            if dest.exists():
                available_paths.append(dest)

    # ── Step 3: Add advisory notes ───────────────────────────────────────────
    # Warn about RLUSD if not explicitly requested (it's a common oversight).
    requested_symbols = {_parse_xrpl_asset(s)[0] for s in extra_xrpl_assets}
    if "RLUSD" not in requested_symbols:
        unsupported.append(UnsupportedAssetNote(
            asset="RLUSD",
            reason=(
                "RLUSD is not included. To add it, pass 'RLUSD' in "
                "extra_xrpl_assets (no issuer needed — it uses a static $1.00 peg)."
            ),
        ))

    for failed in failed_assets:
        unsupported.append(UnsupportedAssetNote(
            asset=failed,
            reason=(
                f"{failed} could not be fetched from Kraken for {year}. "
                "The pair may not have been listed that year; "
                "source prices manually."
            ),
        ))

    # ── Step 4: Merge all per-asset CSVs into combined ───────────────────────
    if not available_paths:
        detail = "; ".join(f"{a}: {e}" for a, e in _fetch_errors.items())
        raise RuntimeError(
            f"Could not fetch price data for any asset in {year}. "
            + (f"Errors: {detail}" if detail else "Check network connectivity.")
        )

    combined   = _combined_csv_path(year)
    total_rows = _write_combined_csv(available_paths, combined)

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
    tz    = datetime.timezone.utc
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

    if not isinstance(body, dict):
        raise RuntimeError(f"Kraken API returned unexpected response type {type(body).__name__}")

    if body.get("error"):
        raise RuntimeError(f"Kraken API error for {pair}: {body['error']}")

    result   = body.get("result", {})
    data_key = next((k for k in result if k != "last"), None)
    if data_key is None:
        raise RuntimeError(f"Kraken returned no candle data for {pair}.")

    year_start = datetime.datetime(year, 1, 1, tzinfo=tz)
    year_end   = datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz)

    prices: dict[str, Decimal] = {}
    for candle in result[data_key]:
        # [timestamp, open, high, low, close, vwap, volume, count]
        ts    = int(candle[0])
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
        rates[dates[int(idx_str)]] = Decimal(str(value_list[0]))

    if not rates:
        raise RuntimeError(f"Norges Bank returned no USD/NOK rates for {year}.")
    return rates


def _fetch_norges_bank_usd_nok_current() -> Decimal:
    """Return the latest available USD/NOK rate from Norges Bank (today or last business day).

    Queries the same SDMX API as ``_fetch_norges_bank_usd_nok`` but covers a
    7-day window ending today so that weekends and public holidays resolve to
    the most recent published rate.
    """
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=7)).isoformat()
    end   = today.isoformat()

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
        raise RuntimeError(f"Could not reach Norges Bank API (current): {exc}") from exc

    try:
        structure  = body["data"]["structure"]
        time_vals  = structure["dimensions"]["observation"][0]["values"]  # noqa: F841 – used below via obs index mapping
        dataset    = body["data"]["dataSets"][0]
        series_key = next(iter(dataset["series"]))
        obs        = dataset["series"][series_key]["observations"]
    except (KeyError, IndexError, StopIteration) as exc:
        raise RuntimeError(
            f"Unexpected Norges Bank response format (current): {exc}"
        ) from exc

    if not obs:
        raise RuntimeError("Norges Bank returned no USD/NOK rates for the last 7 days.")

    latest_idx = max(int(k) for k in obs)
    return Decimal(str(obs[str(latest_idx)][0]))


def _fetch_kraken_spot_usd() -> dict[str, Decimal]:
    """Fetch live USD spot prices for all Tier-1 assets from Kraken Ticker.

    Uses a single ``/0/public/Ticker`` call for all five pairs (XRP, BTC, ETH,
    ADA, LTC) to minimise API round-trips.  Returns ``{asset: usd_price}``
    for assets whose result key is present in the response.
    """
    pairs_param = ",".join(_KRAKEN_USD_PAIR.values())
    url = f"https://api.kraken.com/0/public/Ticker?pair={urllib.parse.quote(pairs_param)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"Could not reach Kraken Ticker API: {exc}") from exc

    if not isinstance(body, dict):
        raise RuntimeError(f"Kraken Ticker returned unexpected response type {type(body).__name__}")

    if body.get("error"):
        raise RuntimeError(f"Kraken Ticker error: {body['error']}")

    result = body.get("result", {})
    prices: dict[str, Decimal] = {}
    for asset, result_key in _KRAKEN_TICKER_RESULT_KEYS.items():
        entry = result.get(result_key)
        if entry and "c" in entry:
            # c[0] = last trade price; c[1] = lot volume — we want the price.
            prices[asset] = Decimal(str(entry["c"][0]))

    return prices


def fetch_spot_prices_nok(assets: list[str]) -> tuple[dict[str, Decimal], str]:
    """Return live NOK spot prices for *assets* and an ISO-8601 ``as_of`` timestamp.

    Fetches Kraken Ticker (USD) × Norges Bank (USD/NOK) in one pass and
    caches the result for ``_SPOT_CACHE_TTL`` seconds (default 5 min).

    Only Tier-1 assets (BTC, ETH, XRP, ADA, LTC) are covered.  Unknown
    assets are silently absent from the returned dict.

    Returns:
        (prices_dict, as_of_iso_string)
    """
    global _spot_cache, _spot_cache_ts

    now = time.time()
    if _spot_cache is not None and (now - _spot_cache_ts) < _SPOT_CACHE_TTL:
        as_of = datetime.datetime.fromtimestamp(
            _spot_cache_ts, tz=datetime.timezone.utc
        ).isoformat()
        return {a: p for a, p in _spot_cache.items() if a in assets}, as_of

    usd_prices = _fetch_kraken_spot_usd()
    usd_nok    = _fetch_norges_bank_usd_nok_current()

    fresh: dict[str, Decimal] = {
        asset: (usd * usd_nok).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        for asset, usd in usd_prices.items()
    }

    _spot_cache    = fresh
    _spot_cache_ts = now
    as_of = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).isoformat()
    return {a: p for a, p in fresh.items() if a in assets}, as_of


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
            dt       = datetime.datetime.strptime(date_raw, "%d %b %Y")
            date_str = dt.strftime("%Y-%m-%d")
            rates[date_str] = Decimal(val_raw)
        except (ValueError, Exception):
            continue

    if not rates:
        raise RuntimeError(f"Bank of England returned no USD/GBP rates for {year}.")
    return rates


def _fetch_binance_usd_prices(pair: str, year: int) -> dict[str, Decimal]:
    """Return {date_str: close_usd} from Binance daily klines for *year*.

    Uses GET /api/v3/klines?symbol={pair}&interval=1d&startTime={ms}&endTime={ms}.
    Binance klines are free, require no API key, and cover full history since
    each pair's listing date (BTC/USDT: 2017, XRP/USDT: 2018, etc.).

    Returns ``{}`` on any error — caller decides whether to try another source.
    """
    tz      = datetime.timezone.utc
    from_ms = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp()) * 1000
    to_ms   = int(datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz).timestamp()) * 1000

    url = (
        f"{_BINANCE_BASE}/klines"
        f"?symbol={urllib.parse.quote(pair)}&interval=1d"
        f"&startTime={from_ms}&endTime={to_ms}&limit=1000"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        _log.warning("Binance request failed for %s %s: %s", pair, year, exc)
        return {}

    if not isinstance(body, list) or not body:
        _log.warning("Binance returned no data for %s %s", pair, year)
        return {}

    prices: dict[str, Decimal] = {}
    for candle in body:
        # Kline format: [open_time, open, high, low, close, volume, ...]
        if len(candle) < 5:
            continue
        try:
            open_time_ms = int(candle[0])
            close_price  = Decimal(str(candle[4]))
            dt = datetime.datetime.fromtimestamp(open_time_ms / 1000, tz=tz)
            if dt.year != year:
                continue
            prices[dt.strftime("%Y-%m-%d")] = close_price
        except Exception:
            continue

    return prices


def _fetch_and_write_binance(
    pair: str, asset: str, year: int, dest: Path
) -> None:
    """Fetch USD close prices from Binance klines + FX from Norges Bank → NOK CSV.

    Mirrors ``_fetch_and_write`` but uses Binance instead of Kraken.
    Raises ``RuntimeError`` if no data is obtained or no NOK prices can be computed.
    """
    usd_prices = _fetch_binance_usd_prices(pair, year)
    if not usd_prices:
        raise RuntimeError(
            f"Binance returned no USD prices for {pair} in {year}."
        )

    raw_fx    = _fetch_norges_bank_usd_nok(year)
    nok_rates = _fill_calendar_gaps(raw_fx, year)

    rows: list[tuple[str, str]] = []
    for date_str, usd_price in sorted(usd_prices.items()):
        nok_rate = nok_rates.get(date_str)
        if nok_rate is None:
            continue
        nok_price = (usd_price * nok_rate).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        rows.append((date_str, str(nok_price)))

    if not rows:
        raise RuntimeError(
            f"No NOK prices computed for {asset} {year}: "
            "no overlap between Binance candles and Norges Bank rates."
        )

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, asset, "NOK", price])


def _fetch_coincap_usd_prices(coincap_id: str, year: int) -> dict[str, Decimal]:
    """Return {date_str: close_usd} from CoinCap daily history for *year*.

    Uses GET /assets/{id}/history?interval=d1&start={ms}&end={ms}.
    CoinCap is free, requires no API key, and stores full history since ~2013.

    Returns ``{}`` on any error — caller decides whether to try another source.
    """
    tz      = datetime.timezone.utc
    from_ms = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp()) * 1000
    to_ms   = int(datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz).timestamp()) * 1000

    url = (
        f"{_COINCAP_BASE}/assets/{urllib.parse.quote(coincap_id)}/history"
        f"?interval=d1&start={from_ms}&end={to_ms}"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        _log.warning("CoinCap request failed for %s %s: %s", coincap_id, year, exc)
        return {}

    if not isinstance(body, dict):
        _log.warning("CoinCap returned unexpected type %s for %s %s", type(body).__name__, coincap_id, year)
        return {}

    data = body.get("data")
    if not isinstance(data, list) or not data:
        _log.warning(
            "CoinCap returned no data for %s %s (body keys: %s)",
            coincap_id, year, list(body.keys()),
        )
        return {}

    prices: dict[str, Decimal] = {}
    for entry in data:
        price_str = entry.get("priceUsd")
        time_ms   = entry.get("time")
        if price_str is None or time_ms is None:
            continue
        try:
            dt = datetime.datetime.fromtimestamp(int(time_ms) / 1000, tz=tz)
            if dt.year != year:
                continue
            prices[dt.strftime("%Y-%m-%d")] = Decimal(str(price_str))
        except Exception:
            continue

    return prices


def _fetch_and_write_coincap(
    coincap_id: str, asset: str, year: int, dest: Path
) -> None:
    """Fetch USD prices from CoinCap + FX from Norges Bank → write NOK CSV.

    Mirrors ``_fetch_and_write`` but uses CoinCap instead of Kraken.
    Raises ``RuntimeError`` if no data is obtained or no NOK prices can be computed.
    """
    usd_prices = _fetch_coincap_usd_prices(coincap_id, year)
    if not usd_prices:
        raise RuntimeError(
            f"CoinCap returned no USD prices for {coincap_id} in {year}."
        )

    raw_fx    = _fetch_norges_bank_usd_nok(year)
    nok_rates = _fill_calendar_gaps(raw_fx, year)

    rows: list[tuple[str, str]] = []
    for date_str, usd_price in sorted(usd_prices.items()):
        nok_rate = nok_rates.get(date_str)
        if nok_rate is None:
            continue
        nok_price = (usd_price * nok_rate).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        rows.append((date_str, str(nok_price)))

    if not rows:
        raise RuntimeError(
            f"No NOK prices computed for {asset} {year}: "
            "no overlap between CoinCap candles and Norges Bank rates."
        )

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, asset, "NOK", price])


def _fetch_and_write_coingecko_nok(
    coin_id: str, symbol: str, year: int, dest: Path
) -> int:
    """Fetch daily NOK prices from CoinGecko using a known coin_id and write CSV.

    Bypasses the search step — uses a pre-known ``coin_id`` (e.g. "ripple",
    "bitcoin") for reliability.  Called as a Tier-1 fallback when Kraken's
    OHLC window does not cover *year* (~720-day rolling window from today).

    Returns the number of rows written, or 0 on any error or empty response.
    """
    tz      = datetime.timezone.utc
    from_ts = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp())
    to_ts   = int(datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=tz).timestamp())

    url = (
        f"{_COINGECKO_BASE}/coins/{urllib.parse.quote(coin_id)}/market_chart/range"
        f"?vs_currency=nok&from={from_ts}&to={to_ts}"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taxspine-orchestrator/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception:
        return 0

    if not isinstance(body, dict):
        return 0

    raw_prices = body.get("prices", [])
    rows: list[tuple[str, str]] = []
    for entry in raw_prices:
        if len(entry) < 2:
            continue
        try:
            ts_ms = int(entry[0])
            price = Decimal(str(entry[1])).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=tz)
            if dt.year != year:
                continue
            rows.append((dt.strftime("%Y-%m-%d"), str(price)))
        except Exception:
            continue

    if not rows:
        return 0

    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "asset_id", "fiat_currency", "price_fiat"])
        for date_str, price in rows:
            writer.writerow([date_str, symbol, "NOK", price])

    return len(rows)


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

    Fetches XRP, BTC, ETH, ADA, LTC from Kraken (USD) × Bank of England (USD/GBP).
    XRPL IOU tokens are not supported for GBP (use NOK pipeline for XRPL accounts).

    RLUSD is always reported as unsupported (no direct Kraken USD pair).
    """
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)

    any_fetched      = False
    available_paths: list[Path] = []
    failed_assets:   list[str]  = []

    for asset in _ALL_KRAKEN_ASSETS:
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

    combined   = _combined_csv_path_gbp(year)
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
    for failed in failed_assets:
        unsupported.append(UnsupportedAssetNote(
            asset=failed,
            reason=(
                f"{failed} could not be fetched from Kraken for {year}. "
                "The pair may not have been listed that year; "
                "source prices manually."
            ),
        ))

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

    Always fetches XRP, BTC, ETH, ADA, LTC via Kraken × Norges Bank.
    Pass ``extra_xrpl_assets`` as a list of ``"SYMBOL.rIssuerAddress"`` strings
    to also fetch XRPL IOU tokens via OnTheDEX → XRPL.to.  Pass ``"RLUSD"``
    (no issuer needed) for the static USD peg.

    XRPL assets registered in the workspace (``POST /workspace/xrpl-assets``) are
    automatically included without needing to repeat them in every request body.

    Past years are cached indefinitely.  Current year is re-fetched if >24 h old.
    """
    current_year = datetime.date.today().year
    if body.year < 2013 or body.year > current_year:
        raise HTTPException(
            status_code=400,
            detail=f"Year must be between 2013 and {current_year}.",
        )

    # Merge explicitly-requested assets with workspace-registered assets.
    # Account trust-line discovery happens inside fetch_all_prices_for_year
    # via _workspace_accounts_provider so it benefits all call sites.
    _ws_extra: list[str] = (
        _workspace_assets_provider() if _workspace_assets_provider is not None else []
    )
    _seen: dict[str, None] = {}
    for _s in (*body.extra_xrpl_assets, *_ws_extra):
        _seen[_s] = None
    all_extra = list(_seen)

    try:
        info = fetch_all_prices_for_year(
            body.year,
            extra_xrpl_assets=all_extra or None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _log.exception("Unexpected error in fetch_all_prices_for_year(year=%s)", body.year)
        raise HTTPException(
            status_code=500,
            detail=f"Internal error fetching prices: {type(exc).__name__}: {exc}",
        ) from exc

    return info


@router.post("/fetch-gbp", response_model=FetchPricesResponse, tags=["prices"])
def fetch_prices_gbp(body: FetchPricesRequest) -> FetchPricesResponse:
    """Fetch (or return cached) daily GBP prices for all supported assets.

    TL-19: UK jobs require GBP price tables.  Fetches XRP, BTC, ETH, ADA, LTC
    via Kraken × Bank of England (XUDLUSS).  No API key required.

    Past years are cached indefinitely.  Current year is re-fetched if >24 h old.
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


@router.get("/spot", tags=["prices"])
def get_spot_prices() -> dict:
    """Return live NOK spot prices for Tier-1 assets (BTC, ETH, XRP, ADA, LTC).

    Fetches from Kraken Ticker × Norges Bank USD/NOK and caches results for
    5 minutes.  Suitable for a live portfolio market-value display.

    Response fields:
    - ``prices``     — ``{asset: nok_price_str}`` for available assets
    - ``as_of``      — ISO-8601 UTC timestamp of the last fetch
    - ``from_cache`` — ``true`` when the response came from the in-memory cache
    """
    from_cache = (
        _spot_cache is not None
        and (time.time() - _spot_cache_ts) < _SPOT_CACHE_TTL
    )
    try:
        all_assets = list(_KRAKEN_TICKER_RESULT_KEYS.keys())
        prices, as_of = fetch_spot_prices_nok(all_assets)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not fetch live spot prices: {exc}",
        ) from exc

    return {
        "prices":     {asset: str(price) for asset, price in sorted(prices.items())},
        "as_of":      as_of,
        "from_cache": from_cache,
    }


@router.get("", response_model=list[PriceFileInfo], tags=["prices"])
def list_prices() -> list[PriceFileInfo]:
    """List all cached price CSV files in PRICES_DIR."""
    settings.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    result: list[PriceFileInfo] = []

    all_csv_paths = sorted(
        list(settings.PRICES_DIR.glob("*_nok_*.csv"))
        + list(settings.PRICES_DIR.glob("*_gbp_*.csv"))
    )
    for csv_path in all_csv_paths:
        stem  = csv_path.stem
        sep   = "_nok_" if "_nok_" in stem else "_gbp_" if "_gbp_" in stem else None
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
