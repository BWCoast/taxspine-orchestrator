"""Configuration for the orchestrator.

Settings are loaded from environment variables (uppercased field names).
Paths default to C:\\tmp\\taxspine_orchestrator\\ for local Windows development.
Override any setting via the matching environment variable, e.g.::

    set OUTPUT_DIR=D:\\taxspine\\output
    uvicorn taxspine_orchestrator.main:app --reload
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base temp root — prefer C:\tmp on Windows, /tmp elsewhere.
_DEFAULT_BASE = Path(r"C:\tmp\taxspine_orchestrator") if os.name == "nt" else Path("/tmp/taxspine_orchestrator")


class Settings(BaseSettings):
    """Orchestrator runtime settings.

    All path settings are Windows-compatible absolute paths.
    Override any field via the corresponding environment variable.
    """

    model_config = SettingsConfigDict(
        # Load .env from the working directory automatically.
        # This makes the orchestrator work correctly when launched from
        # Task Scheduler or any context where env vars are not pre-set.
        env_file=".env",
        env_file_encoding="utf-8",
        # Treat empty env vars as "not set" — fall through to the field default.
        # Prevents CORS_ORIGINS="" in a minimal environment from crashing startup.
        env_ignore_empty=True,
        extra="ignore",
    )

    # ── Directories ───────────────────────────────────────────────────────
    TEMP_DIR: Path = _DEFAULT_BASE / "tmp"
    OUTPUT_DIR: Path = _DEFAULT_BASE / "output"
    UPLOAD_DIR: Path = _DEFAULT_BASE / "uploads"
    # DATA_DIR stores persistent state: jobs.db and workspace.json
    DATA_DIR: Path = _DEFAULT_BASE / "data"
    # PRICES_DIR stores cached price CSVs fetched from external APIs
    PRICES_DIR: Path = _DEFAULT_BASE / "prices"
    # LOT_STORE_DB stores FIFO lots for year-over-year carry-forward
    LOT_STORE_DB: Path = _DEFAULT_BASE / "data" / "lots.db"
    # PRICES_DB stores multi-currency daily prices (ADR-0016).
    # Populated by POST /prices/collect; consumed by valuation_mode="db".
    PRICES_DB: Path = _DEFAULT_BASE / "data" / "prices.db"
    # DEDUP_DIR stores per-source SQLite deduplication databases.
    # Each source type (generic_events, coinbase_csv, firi_csv, xrpl_{account})
    # gets its own .db file under this directory to prevent duplicate events
    # when the same CSV is re-uploaded or XRPL data is re-fetched.
    DEDUP_DIR: Path = _DEFAULT_BASE / "data" / "dedup"

    # PERSONAL_REPORTS_DIR: root of the Project F per-year report directory.
    # When set, GET /sidecar/{year} reads dashboard_summary.json from
    # {PERSONAL_REPORTS_DIR}/{year}/dashboard_summary.json and serves it to
    # the ALTANA dashboard (gains · losses · tax figures without re-parsing RF-1159).
    # Leave empty/unset to disable the sidecar endpoint.
    # Example: PERSONAL_REPORTS_DIR=%USERPROFILE%\Documents\Project F\personal\_reports
    PERSONAL_REPORTS_DIR: Optional[Path] = None
    # BOT_DB is the path to the trading bot's DuckDB database file.
    # When set, POST /bot/sync reads fills and LP events from this file
    # (read-only) and writes them to DATA_DIR/bot_generic_events.csv for
    # inclusion in the next tax run.  None = bot integration disabled.
    BOT_DB: Optional[Path] = None

    # ── Security ──────────────────────────────────────────────────────────
    # Empty string = auth disabled (dev/local mode).
    # Set to a non-empty value to require X-Api-Key on all mutating endpoints.
    ORCHESTRATOR_KEY: str = ""

    # ── AI analysis (optional) ─────────────────────────────────────────────
    # Anthropic API key for the POST /ai/analyze endpoint.
    # When empty the endpoint returns 503 and the Analyze button is hidden.
    # Claude is advisory only — it never participates in tax calculations.
    ANTHROPIC_API_KEY: str = ""
    # Set REQUIRE_AUTH=true to refuse startup when ORCHESTRATOR_KEY is empty.
    # Always set this in production or any network-reachable deployment.
    # When false (default), an empty key is allowed but a loud warning is emitted.
    REQUIRE_AUTH: bool = False
    # Allowed CORS origins.  Override with a comma-separated env var or
    # by subclassing Settings.
    CORS_ORIGINS: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> object:
        """Accept both JSON list and comma-separated string from .env."""
        if not isinstance(v, str):
            return v
        v = v.strip()
        if v.startswith("["):
            return json.loads(v)
        return [o.strip() for o in v.split(",") if o.strip()]

    # ── External CLI binaries ─────────────────────────────────────────────
    # taxspine-xrpl-nor: single-command XRPL → Norway pipeline
    TAXSPINE_XRPL_NOR_CLI: str = "taxspine-xrpl-nor"
    # taxspine-nor-report: generic-events CSV → Norway pipeline (single file)
    TAXSPINE_NOR_REPORT_CLI: str = "taxspine-nor-report"
    # taxspine-nor-multi: multi-source Norway pipeline (all CSVs in one invocation)
    TAXSPINE_NOR_MULTI_CLI: str = "taxspine-nor-multi"
    TAXSPINE_UK_REPORT_CLI: str = "taxspine-uk-report"
    # blockchain-reader: kept for reference; not called by default pipeline
    BLOCKCHAIN_READER_CLI: str = "blockchain-reader"

    # ── Subprocess execution ───────────────────────────────────────────────
    # SEC-20: all subprocess.run() calls are capped at this timeout (seconds).
    # A hung or infinitely-looping tax CLI will be killed after this many
    # seconds and the job will be marked FAILED with a descriptive error.
    # Override via SUBPROCESS_TIMEOUT_SECONDS env var for very large datasets.
    # Default raised to 3600s: XRPL accounts with multi-year history can take
    # 10-20 min to fetch all transactions from the public nodes (s1/s2/cluster).
    # rwvEXAMPLEAAAAAAAAAAAAAAAAAAAAAAAA timed out on 2022 at 1800s (v7 run).
    SUBPROCESS_TIMEOUT_SECONDS: int = 3600

    def ensure_dirs(self) -> None:
        """Create all working directories if they do not exist."""
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.PRICES_DIR.mkdir(parents=True, exist_ok=True)
        self.DEDUP_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
