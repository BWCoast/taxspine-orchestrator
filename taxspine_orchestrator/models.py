"""Domain models for the orchestrator."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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
    # API-05: CANCELLED is a terminal state set by POST /jobs/{id}/cancel.
    # Distinct from FAILED so that:
    #   (a) callers can distinguish user-initiated cancellation from execution errors, and
    #   (b) the execution thread can detect that a cancel arrived mid-run and avoid
    #       overwriting the CANCELLED terminal state with COMPLETED.
    CANCELLED = "cancelled"


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
    tax_year: int = Field(
        ...,
        ge=2009,
        le=2100,
        description=(
            "Tax year to report (e.g. 2025).  Must be 2009 or later "
            "(Bitcoin genesis year) and no later than 2100."
        ),
    )

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
        default=ValuationMode.PRICE_TABLE,
        description=(
            "Valuation strategy for the tax CLIs.  'price_table' (default) "
            "fetches daily NOK prices automatically and passes them via "
            "--csv-prices.  'dummy' skips price lookup and produces zero "
            "valuations (useful for testing or when prices are not needed)."
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
    # Review JSON (Norway jobs only — machine-readable NorwayReviewSummary).
    review_json_path: Optional[str] = None   # first/only review JSON (compat)
    review_json_paths: List[str] = Field(    # all review JSON paths (one per invocation)
        default_factory=list,
        description=(
            "Review summary JSON paths, one per Norway CLI invocation. "
            "Contains has_unlinked_transfers, warning_count, warnings, clean. "
            "``review_json_path`` is a backward-compatible alias for the first element."
        ),
    )
    log_path: Optional[str] = None
    error_message: Optional[str] = None

    @model_validator(mode="after")
    def _sync_singular_from_plural(self) -> "JobOutput":
        # API-21: keep the backward-compat singular alias fields in sync with
        # their plural counterparts.  When only the plural list is populated
        # (e.g. by a code path that sets report_html_paths without explicitly
        # setting report_html_path), the singular is derived from index 0 so
        # the two representations are always consistent.
        if self.report_html_paths and self.report_html_path is None:
            self.report_html_path = self.report_html_paths[0]
        if self.rf1159_json_paths and self.rf1159_json_path is None:
            self.rf1159_json_path = self.rf1159_json_paths[0]
        if self.review_json_paths and self.review_json_path is None:
            self.review_json_path = self.review_json_paths[0]
        return self
    # TL-01 / TL-02: provenance metadata — which valuation engine and price
    # source were used.  Surfaced in the job response so callers can detect
    # dummy-valuation output without inspecting the RF-1159 JSON directly.
    valuation_mode_used: Optional[str] = Field(
        default=None,
        description=(
            "Valuation mode that was applied during execution "
            "('dummy' or 'price_table').  'dummy' output MUST NOT be filed."
        ),
    )
    price_source: Optional[str] = Field(
        default=None,
        description=(
            "Price source used for NOK valuation "
            "('norges_bank_usd_nok' or 'price_table_csv' or 'dummy')."
        ),
    )
    price_table_path: Optional[str] = Field(
        default=None,
        description="Path to the CSV price table used, if valuation_mode=price_table.",
    )
    # TL-05: UK tax year boundaries.  Populated only for country=uk jobs so that
    # callers know which April-to-April window was used (e.g. tax_year=2025 →
    # 2025-04-06 to 2026-04-05).  None for Norway jobs (calendar year = obvious).
    tax_period_start: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 date of the first day of the tax period (UK jobs only). "
            "For tax_year=2025 this is '2025-04-06'. None for Norway."
        ),
    )
    tax_period_end: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 date of the last day of the tax period (UK jobs only). "
            "For tax_year=2025 this is '2026-04-05'. None for Norway."
        ),
    )
    # LC-10: Draft disclaimer for RF-1159 output.  Populated whenever an
    # RF-1159 JSON is produced so API clients can surface the disclaimer
    # without reading the report file.  None for jobs that produce no
    # RF-1159 output (UK jobs, jobs where the pipeline found no virtual
    # currency activity, etc.).
    draft_disclaimer: Optional[str] = Field(
        default=None,
        description=(
            "Populated for Norway jobs that produce RF-1159 output. "
            "This disclaimer MUST be shown to users before any filing."
        ),
    )
    # TL-06: Warning for jobs that include events with complex tax treatment
    # (staking rewards, airdrops, DeFi yield, etc.) that require manual review.
    # Populated at execution time by scanning the input generic-events CSV files.
    # None when no complex treatment events are detected.
    complex_treatment_warning: Optional[str] = Field(
        default=None,
        description=(
            "Populated when input CSV files contain events with a non-standard "
            "complex_tax_treatment label (e.g. STAKING, AIRDROP, DEFI_YIELD). "
            "These events may require specialist tax advice and manual review "
            "before filing. None when all events use standard treatment."
        ),
    )
    # TL-05: pipeline mode actually used for this job execution.  Populated for
    # Norway jobs; None for UK jobs (UK always uses PER_FILE).  Complements
    # valuation_mode_used so callers can understand exactly which execution
    # path produced the output.
    pipeline_mode_used: Optional[str] = Field(
        default=None,
        description=(
            "Pipeline mode used: 'per_file' or 'nor_multi'. "
            "None for UK jobs. Determines FIFO lot pool scope."
        ),
    )
    # TL-08: warn when a UK job is run before the tax year has fully elapsed.
    # The UK tax year N runs 6 Apr N → 5 Apr N+1.  Running before 5 Apr N+1
    # produces a partial-year result; transactions after the run date are not
    # included.  None when the year is complete or for Norway jobs.
    partial_year_warning: Optional[str] = Field(
        default=None,
        description=(
            "Populated for UK jobs run before the tax year end (5 April of "
            "tax_year + 1). Indicates that the report covers only transactions "
            "up to the run date and must be re-run after the year closes."
        ),
    )
    # TL-09: filing-completeness warnings extracted from RF-1159 JSON output.
    # Populated whenever the tax pipeline detects conditions that mean the
    # RF-1159 output is incomplete or unreliable (unresolved cost basis,
    # unresolved income valuations, zero formue with non-empty portfolio, etc.).
    # Empty list when the pipeline produced a clean, fully-valued result.
    # None when no RF-1159 output was produced (UK jobs, no virtual currency).
    rf1159_warnings: Optional[List[str]] = Field(
        default=None,
        description=(
            "Completeness warnings from the RF-1159 pipeline output. "
            "Non-empty means the filing has unresolved items that must be "
            "reviewed before submission. None for jobs that produce no "
            "RF-1159 output (UK jobs, jobs with no virtual currency activity)."
        ),
    )


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


# ── API-22: typed response models for previously-untyped endpoints ────────────


class StartJobResponse(BaseModel):
    """Response body for POST /jobs/{id}/start (HTTP 202 Accepted)."""

    status: str = Field(description="'accepted' or the current job status value")
    job_id: str = Field(description="The job ID that was accepted for execution")


class CancelledJobResponse(BaseModel):
    """Response body for POST /jobs/{id}/cancel."""

    status: str = Field(description="Always 'cancelled'")
    job_id: str = Field(description="The job ID that was cancelled")


class DeletedJobResponse(BaseModel):
    """Response body for DELETE /jobs/{id}."""

    deleted: bool = Field(description="Always True on success")
    id: str = Field(description="The job ID that was deleted")
    files_removed: int = Field(description="Number of output/input files removed from disk")


class JobReviewResponse(BaseModel):
    """Response body for GET /jobs/{id}/review — aggregated pipeline review summary."""

    has_unlinked_transfers: bool = Field(
        description="True if any invocation detected unlinked cross-venue transfers"
    )
    warning_count: int = Field(description="Total number of warnings across all invocations")
    warnings: List[str] = Field(description="Flat list of warning strings")
    clean: bool = Field(description="True when there are no warnings and no unlinked transfers")
    source_count: int = Field(description="Number of review JSON files successfully read")


class WorkspaceConfig(BaseModel):
    """Persistent workspace state — survives server restarts.

    Tracks the XRPL accounts, CSV file paths, and XRPL token assets that are
    registered for continuous year-over-year tracking.  Stored as JSON on disk.
    """

    xrpl_accounts: List[str] = Field(
        default_factory=list,
        description="XRPL account addresses registered for tracking.",
    )
    csv_files: List[CsvFileSpec] = Field(
        default_factory=list,
        description="CSV files registered for tracking, each with a source type.",
    )
    xrpl_assets: List[str] = Field(
        default_factory=list,
        description=(
            "XRPL token asset specs registered for price tracking, in "
            "'SYMBOL.rIssuerAddress' format (e.g. 'SOLO.rHXuEaRYZBzZzb4vDiJFi8KRpU2mQhBpL'). "
            "These are automatically included in every NOK price fetch so that "
            "year-end reports cover all tracked tokens without per-job configuration."
        ),
    )

    @field_validator("csv_files", mode="before")
    @classmethod
    def coerce_csv_files(cls, v: object) -> object:
        return _coerce_csv_file_list(v)
