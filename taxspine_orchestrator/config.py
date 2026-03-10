"""Basic configuration / settings for the orchestrator."""

from __future__ import annotations

from pathlib import Path


# ── Directory layout (defaults) ──────────────────────────────────────────────
# These will likely move to env vars / a Settings class once we need it.

#: Temporary working directory for in-progress jobs.
TEMP_DIR: Path = Path("/tmp/taxspine_orchestrator/tmp")

#: Final output directory for completed job artifacts.
OUTPUT_DIR: Path = Path("/tmp/taxspine_orchestrator/output")

# TODO: Add paths / settings for external CLI binaries once we wire them in:
#   BLOCKCHAIN_READER_BIN: str = "xrpl-reader"
#   TAXSPINE_NOR_BIN: str = "taxspine-nor"
#   TAXSPINE_UK_BIN: str = "taxspine-uk"
