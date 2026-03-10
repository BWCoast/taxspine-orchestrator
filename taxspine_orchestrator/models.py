"""Domain models for the orchestrator."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────


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


class JobStatus(str, Enum):
    """Lifecycle states of a tax job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Request / response models ────────────────────────────────────────────────


class JobInput(BaseModel):
    """User-supplied inputs when creating a tax job.

    A job may include XRPL accounts, generic-events CSV files, or both.
    At least one of ``xrpl_accounts`` or ``csv_files`` must be non-empty
    for execution to succeed.
    """

    xrpl_accounts: List[str] = Field(default_factory=list)
    tax_year: int
    country: Country
    csv_files: List[str] = Field(
        default_factory=list,
        description=(
            "Paths to generic-events CSV files (already in the canonical "
            "schema understood by the taxspine CLIs).  These are passed "
            "as --generic-events-csv flags and are not parsed or validated "
            "by the orchestrator."
        ),
    )
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


class JobOutput(BaseModel):
    """Paths/IDs produced by a completed job (all optional until filled in)."""

    gains_csv_path: Optional[str] = None
    wealth_csv_path: Optional[str] = None
    summary_json_path: Optional[str] = None
    report_html_path: Optional[str] = None  # self-contained HTML tax report
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
