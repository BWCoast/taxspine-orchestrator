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

    # ── External CLI binaries ─────────────────────────────────────────────
    # taxspine-xrpl-nor: single-command XRPL → Norway pipeline
    TAXSPINE_XRPL_NOR_CLI: str = "taxspine-xrpl-nor"
    # taxspine-nor-report: generic-events CSV → Norway pipeline
    TAXSPINE_NOR_REPORT_CLI: str = "taxspine-nor-report"
    TAXSPINE_UK_REPORT_CLI: str = "taxspine-uk-report"
    # blockchain-reader: kept for reference; not called by default pipeline
    BLOCKCHAIN_READER_CLI: str = "blockchain-reader"

    def ensure_dirs(self) -> None:
        """Create TEMP_DIR, OUTPUT_DIR, and UPLOAD_DIR if they do not exist."""
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
