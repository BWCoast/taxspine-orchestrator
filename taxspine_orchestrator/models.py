"""Domain models for the orchestrator."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# XRPL address validation
# ---------------------------------------------------------------------------
# Base58-encoded XRPL account addresses start with 'r' and are 25–34
# characters long (including the leading 'r').  The alphabet excludes 0, O,
# I, and l to avoid visual ambiguity.
_XRPL_ADDRESS_RE = re.compile(r'^r[1-9A-HJ-NP-Za-km-z]{24,33}$')


# ── Enums ────────────────────────────────────────────────────────────────────


class CsvSourceType(str, Enum):
    """Exchange format of an uploaded CSV file.

    Determines which taxspine CLI flag is used to process the file:

    - ``GENERIC_EVENTS`` — ``--generic-events-csv PATH`` (spine's own schema)
    - ``COINBASE_CSV``   — ``--coinbase-csv PATH`` (Coinbase RAWTX export)
    - ``FIRI_CSV``       — ``--input PATH --source-type firi_csv``
    """

    GENERIC_EVENTS = "generic_events"
    COINBASE_CSV = "coinbase_csv"
    FIRI_CSV = "firi_csv"


class Country(str, Enum):
    """Supported tax jurisdictions."""

    NORWAY = "norway"
    UK = "uk"


class ValuationMode(str, Enum):
    """How the tax CLIs should value assets.

    - ``DUMMY`` — use the built-in dummy/default valuation (current behaviour).
    - ``PRICE_TABLE`` — use an external CSV price table via ``--csv-prices``.
    """

    DUMMY = "dummy"
    PRICE_TABLE = "price_table"


class PipelineMode(str, Enum):
    """How CSV-only Norway jobs are executed.

    - ``PER_FILE``  — run ``taxspine-nor-report`` once per CSV file (default,
                      backward-compatible).  Each file gets its own HTML report.
    - ``NOR_MULTI`` — run ``taxspine-nor-multi`` once with all CSV files via
                      ``--source TYPE:PATH`` args.  Produces a single combined
                      HTML report and a unified FIFO lot pool across all sources.
    """

    PER_FILE = "per_file"
    NOR_MULTI = "nor_multi"


class JobStatus(str, Enum):
    """Lifecycle states of a tax job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── CSV file spec ────────────────────────────────────────────────────────────


class CsvFileSpec(BaseModel):
    """A CSV file path together with its exchange source type.

    Bare string paths (from older API calls or workspace JSON) are coerced
    automatically to ``CsvFileSpec(path=..., source_type=GENERIC_EVENTS)``.
    """

    path: str
    source_type: CsvSourceType = CsvSourceType.GENERIC_EVENTS


def _coerce_csv_file_list(v: object) -> list:
    """Pydantic ``mode='before'`` validator: coerce ``List[str | dict]`` to ``List[CsvFileSpec]``."""
    if not isinstance(v, list):
        return v  # let Pydantic produce the type error
    result = []
    for item in v:
        if isinstance(item, str):
            result.append({"path": item, "source_type": CsvSourceType.GENERIC_EVENTS})
        else:
            result.append(item)
    return result


# ── Request / response models ────────────────────────────────────────────────


class JobInput(BaseModel):
    """User-supplied inputs when creating a tax job.

    A job may include XRPL accounts, generic-events CSV files, or both.
    At least one of ``xrpl_accounts`` or ``csv_files`` must be non-empty
    for execution to succeed.
    """

    xrpl_accounts: List[str] = Field(default_factory=list)
    tax_year: int

    @field_validator("xrpl_accounts", mode="before")
    @classmethod
    def validate_xrpl_accounts(cls, v: object) -> object:
        """Validate every address in the xrpl_accounts list."""
        if not isinstance(v, list):
            return v  # let Pydantic produce the type error
        for addr in v:
            if isinstance(addr, str) and not _XRPL_ADDRESS_RE.match(addr):
                raise ValueError(f"Invalid XRPL address: {addr}")
        return v
    country: Country
    csv_files: List[CsvFileSpec] = Field(
        default_factory=list,
        description=(
            "CSV files to include in this job, each with an exchange source type. "
            "Bare string paths (legacy) are automatically coerced to CsvFileSpec "
            "with source_type=GENERIC_EVENTS."
        ),
    )

    @field_validator("csv_files", mode="before")
    @classmethod
    def coerce_csv_files(cls, v: object) -> object:
        return _coerce_csv_file_list(v)
    case_name: Optional[str] = Field(
        default=None,
        description=(
            "Human-friendly label for the job, e.g. "
            "'2025 Norway – main wallets'.  Useful for dashboard display "
            "and free-text filtering."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "When True the job skips actual CLI execution and only writes "
            "an execution log listing the commands that *would* have been "
            "run.  Useful for testing and previewing the pipeline."
        ),
    )
    debug_valuation: bool = Field(
        default=False,
        description=(
            "When True, passes --debug-valuation to the tax CLI so that "
            "valuation diagnostics are written to stderr / the execution log."
        ),
    )
    valuation_mode: ValuationMode = Field(
        default=ValuationMode.DUMMY,
        description=(
            "Valuation strategy for the tax CLIs.  'dummy' uses the "
            "built-in default; 'price_table' passes a CSV price table "
            "via --csv-prices."
        ),
    )
    csv_prices_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to a CSV price table on disk.  Only meaningful when "
            "valuation_mode is 'price_table'.  The orchestrator checks "
            "that the file exists but does not validate its contents."
        ),
    )
    include_trades: bool = Field(
        default=False,
        description=(
            "When True, passes --include-trades to taxspine-xrpl-nor so that "
            "DEX swap events (XRPL OfferCreate) are fetched and included in "
            "the pipeline alongside payment events.  Has no effect on CSV-only "
            "jobs."
        ),
    )
    pipeline_mode: PipelineMode = Field(
        default=PipelineMode.PER_FILE,
        description=(
            "CSV-only Norway execution strategy.  'per_file' (default) runs "
            "taxspine-nor-report once per CSV file.  'nor_multi' runs a single "
            "taxspine-nor-multi invocation with all CSV files, producing a "
            "unified FIFO lot pool and a single combined HTML report.  "
            "Has no effect on XRPL jobs or UK jobs."
        ),
    )


class JobOutput(BaseModel):
    """Paths/IDs produced by a completed job (all optional until filled in)."""

    gains_csv_path: Optional[str] = None
    wealth_csv_path: Optional[str] = None
    summary_json_path: Optional[str] = None
    report_html_path: Optional[str] = None   # first HTML report (backward compat)
    report_html_paths: List[str] = Field(    # all HTML reports (one per account/CSV)
        default_factory=list,
        description=(
            "All HTML report paths produced by this job, in execution order. "
            "Jobs with multiple XRPL accounts or CSV files generate one report "
            "each.  ``report_html_path`` is kept as a backward-compatible alias "
            "for the first element of this list."
        ),
    )
    # RF-1159 JSON export (Norway jobs only; not produced by XRPL-only jobs).
    rf1159_json_path: Optional[str] = None   # first/only RF-1159 JSON (compat)
    rf1159_json_paths: List[str] = Field(    # all RF-1159 JSON paths (one per invocation)
        default_factory=list,
        description=(
            "RF-1159 (Altinn) export JSON paths, one per Norway CLI invocation. "
            "NOR_MULTI mode produces a single combined path; PER_FILE mode "
            "produces one per CSV file.  ``rf1159_json_path`` is a "
            "backward-compatible alias for the first element."
        ),
    )
    log_path: Optional[str] = None
    error_message: Optional[str] = None


class Job(BaseModel):
    """Top-level job record combining inputs, status, and outputs."""

    id: str
    status: JobStatus
    input: JobInput
    output: JobOutput = Field(default_factory=JobOutput)
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None


# ── Workspace ────────────────────────────────────────────────────────────────


class WorkspaceConfig(BaseModel):
    """Persistent workspace state — survives server restarts.

    Tracks the XRPL accounts and CSV file paths that are registered for
    continuous year-over-year tracking.  Stored as JSON on disk.
    """

    xrpl_accounts: List[str] = Field(
        default_factory=list,
        description="XRPL account addresses registered for tracking.",
    )
    csv_files: List[CsvFileSpec] = Field(
        default_factory=list,
        description="CSV files registered for tracking, each with a source type.",
    )

    @field_validator("csv_files", mode="before")
    @classmethod
    def coerce_csv_files(cls, v: object) -> object:
        return _coerce_csv_file_list(v)
