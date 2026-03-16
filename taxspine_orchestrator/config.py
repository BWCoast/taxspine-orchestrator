"""Configuration for the orchestrator.

Settings are loaded from environment variables (uppercased field names).
Paths default to C:\\tmp\\taxspine_orchestrator\\ for local Windows development.
Override any setting via the matching environment variable, e.g.::

    set OUTPUT_DIR=D:\\taxspine\\output
    uvicorn taxspine_orchestrator.main:app --reload
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings

# Base temp root — prefer C:\tmp on Windows, /tmp elsewhere.
_DEFAULT_BASE = Path(r"C:\tmp\taxspine_orchestrator") if os.name == "nt" else Path("/tmp/taxspine_orchestrator")


class Settings(BaseSettings):
    """Orchestrator runtime settings.

    All path settings are Windows-compatible absolute paths.
    Override any field via the corresponding environment variable.
    """

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
    # DEDUP_DIR stores per-source SQLite deduplication databases.
    # Each source type (generic_events, coinbase_csv, firi_csv, xrpl_{account})
    # gets its own .db file under this directory to prevent duplicate events
    # when the same CSV is re-uploaded or XRPL data is re-fetched.
    DEDUP_DIR: Path = _DEFAULT_BASE / "data" / "dedup"

    # ── Security ──────────────────────────────────────────────────────────
    # Empty string = auth disabled (dev/local mode).
    # Set to a non-empty value to require X-Orchestrator-Key on all mutating
    # endpoints.
    ORCHESTRATOR_KEY: str = ""
    # Allowed CORS origins.  Override with a comma-separated env var or
    # by subclassing Settings.
    CORS_ORIGINS: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

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

    def ensure_dirs(self) -> None:
        """Create all working directories if they do not exist."""
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.PRICES_DIR.mkdir(parents=True, exist_ok=True)
        self.DEDUP_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
