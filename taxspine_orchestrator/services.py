"""Job orchestration service layer.

This module owns all job-lifecycle logic.  It calls blockchain-reader and
taxspine-* CLIs via ``subprocess.run`` to produce tax-report artefacts.

Supported job types
-------------------
- **XRPL-only** — blockchain-reader exports events, tax CLI processes them.
- **CSV-only** — generic-events CSVs are passed straight to the tax CLI.
- **Combined** — both XRPL events and CSVs are merged by the tax CLI.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .models import Country, Job, JobInput, JobOutput, JobStatus, ValuationMode
from .storage import InMemoryJobStore


class JobService:
    """Create, query, and execute tax jobs."""

    def __init__(self, store: InMemoryJobStore) -> None:
        self.store = store

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create_job(self, job_input: JobInput) -> Job:
        """Create a new job in PENDING state."""
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            input=job_input,
            created_at=now,
            updated_at=now,
        )
        return self.store.add(job)

    def get_job(self, job_id: str) -> Job | None:
        return self.store.get(job_id)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        country: Country | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        return self.store.list(
            status=status,
            country=country,
            query=query,
            limit=limit,
            offset=offset,
        )

    # ── Execution pipeline ───────────────────────────────────────────────

    def start_job_execution(self, job_id: str) -> Job | None:
        """Run the full pipeline synchronously.

        Lifecycle: PENDING → RUNNING → COMPLETED | FAILED.

        Supported input combinations:
        - XRPL-only  — runs ``taxspine-xrpl-nor`` for each account,
                        collecting one HTML report per account.
        - CSV-only   — runs ``taxspine-nor-report`` (or UK equivalent)
                        for each generic-events CSV file.
        - Combined   — XRPL accounts first, then CSV files.
        - Neither    → immediate FAILED.

        When ``job.input.dry_run`` is True the pipeline logs which commands
        *would* be executed but does not call any subprocess.  The job
        completes immediately with only ``log_path`` populated.
        """
        job = self.store.get(job_id)
        if job is None:
            return None

        # Only start from PENDING — return current state otherwise.
        if job.status != JobStatus.PENDING:
            return job

        has_xrpl = bool(job.input.xrpl_accounts)
        has_csv = bool(job.input.csv_files)

        # ── Mark RUNNING ──────────────────────────────────────────────────
        self.store.update_status(job_id, JobStatus.RUNNING)

        work_dir = self._job_work_dir(job_id)
        output_dir = self._job_output_dir(job_id)
        log_lines: list[str] = []

        try:
            # ── Guard: valuation_mode consistency ────────────────────────
            if job.input.valuation_mode == ValuationMode.PRICE_TABLE:
                if job.input.csv_prices_path is None:
                    return self._fail_job(
                        job_id,
                        error="valuation_mode=price_table requires csv_prices_path",
                        log_lines=log_lines,
                        output_dir=output_dir,
                    )
                if not Path(job.input.csv_prices_path).is_file():
                    return self._fail_job(
                        job_id,
                        error=f"CSV price table not found: {job.input.csv_prices_path}",
                        log_lines=log_lines,
                        output_dir=output_dir,
                    )

            # ── Guard: no inputs ──────────────────────────────────────────
            if not has_xrpl and not has_csv:
                return self._fail_job(
                    job_id,
                    error="job has no inputs (no XRPL accounts and no CSV files)",
                    log_lines=log_lines,
                    output_dir=output_dir,
                )

            # ── dry_run: preview commands only ────────────────────────────
            if job.input.dry_run:
                return self._execute_dry_run(
                    job_id,
                    job=job,
                    has_xrpl=has_xrpl,
                    work_dir=work_dir,
                    output_dir=output_dir,
                    log_lines=log_lines,
                )

            # ── Guard: verify CSV files exist ─────────────────────────────
            for csv_path_str in job.input.csv_files:
                if not Path(csv_path_str).is_file():
                    return self._fail_job(
                        job_id,
                        error=f"CSV file not found: {csv_path_str}",
                        log_lines=log_lines,
                        output_dir=output_dir,
                    )

            # ── Step 1: XRPL accounts → taxspine-xrpl-nor ────────────────
            # taxspine-xrpl-nor handles the full XRPL → Norway pipeline
            # internally (no separate blockchain-reader step required).
            # When multiple accounts are present we run once per account
            # and write separate HTML reports.
            report_html_path: Path | None = None

            if has_xrpl:
                for idx, account in enumerate(job.input.xrpl_accounts):
                    suffix = f"_{idx}" if len(job.input.xrpl_accounts) > 1 else ""
                    html_dest = output_dir / f"report{suffix}.html"

                    xrpl_cmd = self._build_xrpl_command(
                        job.input,
                        account=account,
                        html_path=html_dest,
                    )
                    log_lines.append(f"$ {' '.join(str(c) for c in xrpl_cmd)}")

                    xrpl_result = subprocess.run(
                        xrpl_cmd, capture_output=True, text=True, check=False,
                    )
                    log_lines.append(f"  rc={xrpl_result.returncode}")
                    if xrpl_result.stdout:
                        log_lines.append(f"  stdout:\n{xrpl_result.stdout.rstrip()}")
                    if xrpl_result.stderr:
                        log_lines.append(f"  stderr:\n{xrpl_result.stderr.rstrip()}")

                    if xrpl_result.returncode != 0:
                        return self._fail_job(
                            job_id,
                            error=(
                                f"taxspine-xrpl-nor failed for {account} "
                                f"(rc={xrpl_result.returncode})"
                            ),
                            log_lines=log_lines,
                            output_dir=output_dir,
                        )

                    # Use the first account's report as the primary report.
                    if report_html_path is None and html_dest.exists():
                        report_html_path = html_dest

            # ── Step 2: generic-events CSVs → taxspine-nor-report ─────────
            if has_csv:
                for csv_path_str in job.input.csv_files:
                    csv_stem = Path(csv_path_str).stem
                    html_dest = output_dir / f"report_{csv_stem}.html"

                    csv_cmd = self._build_csv_command(
                        job.input,
                        csv_path=Path(csv_path_str),
                        html_path=html_dest,
                    )
                    log_lines.append(f"$ {' '.join(str(c) for c in csv_cmd)}")

                    csv_result = subprocess.run(
                        csv_cmd, capture_output=True, text=True, check=False,
                    )
                    log_lines.append(f"  rc={csv_result.returncode}")
                    if csv_result.stdout:
                        log_lines.append(f"  stdout:\n{csv_result.stdout.rstrip()}")
                    if csv_result.stderr:
                        log_lines.append(f"  stderr:\n{csv_result.stderr.rstrip()}")

                    if csv_result.returncode != 0:
                        return self._fail_job(
                            job_id,
                            error=(
                                f"taxspine-nor-report failed for {csv_path_str} "
                                f"(rc={csv_result.returncode})"
                            ),
                            log_lines=log_lines,
                            output_dir=output_dir,
                        )

                    if report_html_path is None and html_dest.exists():
                        report_html_path = html_dest

            # ── Step 3: write log + build output record ───────────────────
            log_path = self._write_log(output_dir, log_lines)

            output = JobOutput(
                report_html_path=str(report_html_path) if report_html_path else None,
                log_path=str(log_path),
            )
            return self.store.update_job(
                job_id, status=JobStatus.COMPLETED, output=output,
            )

        except Exception as exc:  # noqa: BLE001
            log_lines.append(f"  exception: {exc}")
            return self._fail_job(
                job_id,
                error=f"unexpected error: {exc}",
                log_lines=log_lines,
                output_dir=output_dir,
            )

    # ── Command builders ─────────────────────────────────────────────────

    @staticmethod
    def _build_xrpl_command(
        job_input: JobInput,
        *,
        account: str,
        html_path: Path,
    ) -> list[str]:
        """Build a ``taxspine-xrpl-nor`` command for a single XRPL account.

        taxspine-xrpl-nor handles the full pipeline internally:
        it fetches transactions from the ledger and runs the Norway
        tax pipeline.  No blockchain-reader step is needed.

        Real CLI flags (as of Phase 2):
            --account ADDRESS  (required)
            --year    YEAR     (required)
            --csv-prices PATH  (optional; CSV format price table)
            --debug-valuation  (optional)
            --html-output PATH (optional; self-contained HTML report)
        """
        cmd: list[str] = [
            settings.TAXSPINE_XRPL_NOR_CLI,
            "--account", account,
            "--year", str(job_input.tax_year),
            "--html-output", str(html_path),
        ]

        if (
            job_input.valuation_mode == ValuationMode.PRICE_TABLE
            and job_input.csv_prices_path is not None
        ):
            cmd.extend(["--csv-prices", job_input.csv_prices_path])

        if job_input.include_trades:
            cmd.append("--include-trades")

        if job_input.debug_valuation:
            cmd.append("--debug-valuation")

        return cmd

    @staticmethod
    def _build_csv_command(
        job_input: JobInput,
        *,
        csv_path: Path,
        html_path: Path,
    ) -> list[str]:
        """Build a ``taxspine-nor-report`` command for a generic-events CSV.

        Real CLI flags (as of Phase 2):
            --input      PATH  (required; CSV file)
            --year       YEAR  (required)
            --csv-prices PATH  (optional; CSV format price table)
            --debug-valuation  (optional)
            --html-output PATH (optional; self-contained HTML report)
        """
        if job_input.country == Country.NORWAY:
            cmd: list[str] = [settings.TAXSPINE_NOR_REPORT_CLI]
        elif job_input.country == Country.UK:
            cmd = [settings.TAXSPINE_UK_REPORT_CLI]
        else:
            raise ValueError(f"Unsupported country: {job_input.country}")

        cmd.extend([
            "--input", str(csv_path),
            "--year", str(job_input.tax_year),
            "--html-output", str(html_path),
        ])

        if (
            job_input.valuation_mode == ValuationMode.PRICE_TABLE
            and job_input.csv_prices_path is not None
        ):
            cmd.extend(["--csv-prices", job_input.csv_prices_path])

        if job_input.debug_valuation:
            cmd.append("--debug-valuation")

        return cmd

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _job_work_dir(job_id: str) -> Path:
        """Create and return a temporary working directory for *job_id*."""
        work_dir = settings.TEMP_DIR / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    @staticmethod
    def _job_output_dir(job_id: str) -> Path:
        """Create and return the final output directory for *job_id*."""
        out_dir = settings.OUTPUT_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    @staticmethod
    def _write_log(output_dir: Path, lines: list[str]) -> Path:
        """Write *lines* to ``execution.log`` inside *output_dir*."""
        log_path = output_dir / "execution.log"
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path

    def _fail_job(
        self,
        job_id: str,
        *,
        error: str,
        log_lines: list[str],
        output_dir: Path,
    ) -> Job | None:
        """Mark a job as FAILED, persisting the error and log."""
        log_path = self._write_log(output_dir, log_lines)
        output = JobOutput(
            error_message=error,
            log_path=str(log_path),
        )
        return self.store.update_job(
            job_id, status=JobStatus.FAILED, output=output,
        )

    def _execute_dry_run(
        self,
        job_id: str,
        *,
        job: Job,
        has_xrpl: bool,
        work_dir: Path,
        output_dir: Path,
        log_lines: list[str],
    ) -> Job | None:
        """Complete a dry-run job without calling any subprocesses.

        Writes an execution log listing the commands that *would* have
        been run, then marks the job as COMPLETED.  No output file paths
        are set — dry_run is intended for testing and previewing only.
        """
        log_lines.append("[DRY RUN] — no subprocesses will be executed.")

        if has_xrpl:
            for account in job.input.xrpl_accounts:
                html_path = output_dir / "report.html"
                cmd = self._build_xrpl_command(job.input, account=account, html_path=html_path)
                log_lines.append(f"[would run] $ {' '.join(str(c) for c in cmd)}")

        for csv_path_str in job.input.csv_files:
            html_path = output_dir / f"report_{Path(csv_path_str).stem}.html"
            cmd = self._build_csv_command(
                job.input, csv_path=Path(csv_path_str), html_path=html_path,
            )
            log_lines.append(f"[would run] $ {' '.join(str(c) for c in cmd)}")

        log_path = self._write_log(output_dir, log_lines)
        output = JobOutput(log_path=str(log_path))
        return self.store.update_job(
            job_id, status=JobStatus.COMPLETED, output=output,
        )
