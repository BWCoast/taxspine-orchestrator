"""Domain models for the orchestrator."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────


class Country(str, Enum):
    """Supported tax jurisdictions."""

    NORWAY = "norway"
    UK = "uk"


class JobStatus(str, Enum):
    """Lifecycle states of a tax job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Request / response models ────────────────────────────────────────────────


class JobInput(BaseModel):
    """User-supplied inputs when creating a tax job."""

    xrpl_accounts: List[str] = Field(default_factory=list)
    tax_year: int
    country: Country
    csv_files: List[str] = Field(
        default_factory=list,
        description="Placeholder for uploaded CSV paths or IDs.",
    )


class JobOutput(BaseModel):
    """Paths/IDs produced by a completed job (all optional until filled in)."""

    gains_csv_path: Optional[str] = None
    wealth_csv_path: Optional[str] = None
    summary_json_path: Optional[str] = None
    log_path: Optional[str] = None
    error_message: Optional[str] = None


class Job(BaseModel):
    """Top-level job record combining inputs, status, and outputs."""

    id: str
    status: JobStatus
    input: JobInput
    output: JobOutput = Field(default_factory=JobOutput)
