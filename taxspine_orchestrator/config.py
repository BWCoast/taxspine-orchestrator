"""Configuration for the orchestrator.

Settings are loaded from environment variables (uppercased field names).
All paths default to subdirectories under /tmp for local development.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Orchestrator runtime settings.

    Override any field via the corresponding environment variable, e.g.
    ``BLOCKCHAIN_READER_CLI=./my-reader uvicorn …``.
    """

    # ── Directories ───────────────────────────────────────────────────────
    TEMP_DIR: Path = Path("/tmp/taxspine_orchestrator/tmp")
    OUTPUT_DIR: Path = Path("/tmp/taxspine_orchestrator/output")

    # ── External CLI binaries ─────────────────────────────────────────────
    BLOCKCHAIN_READER_CLI: str = "blockchain-reader"
    TAXSPINE_NOR_REPORT_CLI: str = "taxspine-nor-report"
    TAXSPINE_UK_REPORT_CLI: str = "taxspine-uk-report"

    def ensure_dirs(self) -> None:
        """Create TEMP_DIR and OUTPUT_DIR if they do not exist."""
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
