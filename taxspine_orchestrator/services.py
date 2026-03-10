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
from .models import Country, Job, JobInput, JobOutput, JobStatus
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
        - XRPL-only  (xrpl_accounts non-empty, csv_files empty)
        - CSV-only   (xrpl_accounts empty, csv_files non-empty)
        - Combined   (both non-empty)
        - Neither    → immediate FAILED

        Only PENDING jobs can be started.  If the job is already RUNNING,
        COMPLETED, or FAILED the call returns the job unchanged (idempotent).

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
            # ── Guard: no inputs at all ───────────────────────────────────
            # dry_run does NOT override the no-inputs check — there is
            # nothing useful to preview when there are no inputs.
            if not has_xrpl and not has_csv:
                return self._fail_job(
                    job_id,
                    error=(
                        "job has no inputs "
                        "(no XRPL accounts and no CSV files)"
                    ),
                    log_lines=log_lines,
                    output_dir=output_dir,
                )

            # ── dry_run: log the would-be commands and finish ─────────────
            # dry_run is intended for testing and "previewing" commands,
            # not for generating tax outputs.  No subprocess calls are made.
            if job.input.dry_run:
                return self._execute_dry_run(
                    job_id,
                    job=job,
                    has_xrpl=has_xrpl,
                    work_dir=work_dir,
                    output_dir=output_dir,
                    log_lines=log_lines,
                )

            # ── Guard: verify CSV files exist before calling any CLI ──────
            for csv_path_str in job.input.csv_files:
                csv_path = Path(csv_path_str)
                if not csv_path.is_file():
                    return self._fail_job(
                        job_id,
                        error=f"CSV file not found: {csv_path_str}",
                        log_lines=log_lines,
                        output_dir=output_dir,
                    )

            # ── Step 1: blockchain-reader → events.json (XRPL only) ──────
            events_path: Path | None = None

            if has_xrpl:
                events_path = work_dir / "events.json"
                reader_cmd = self._build_reader_command(
                    job.input, events_path,
                )
                log_lines.append(f"$ {' '.join(reader_cmd)}")

                reader_result = subprocess.run(
                    reader_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                log_lines.append(f"  rc={reader_result.returncode}")
                if reader_result.stdout:
                    log_lines.append(
                        f"  stdout: {reader_result.stdout.rstrip()}"
                    )
                if reader_result.stderr:
                    log_lines.append(
                        f"  stderr: {reader_result.stderr.rstrip()}"
                    )

                if reader_result.returncode != 0:
                    return self._fail_job(
                        job_id,
                        error=(
                            "blockchain-reader failed "
                            f"(rc={reader_result.returncode})"
                        ),
                        log_lines=log_lines,
                        output_dir=output_dir,
                    )

            # ── Step 2: taxspine report CLI → CSV / JSON ──────────────────
            gains_path = work_dir / "gains.csv"
            wealth_path = work_dir / "wealth.csv"
            summary_path = work_dir / "summary.json"

            report_cmd = self._build_report_command(
                job.input,
                events_path=events_path,
                gains_path=gains_path,
                wealth_path=wealth_path,
                summary_path=summary_path,
            )
            log_lines.append(
                f"$ {' '.join(str(c) for c in report_cmd)}"
            )

            report_result = subprocess.run(
                report_cmd, capture_output=True, text=True, check=False,
            )
            log_lines.append(f"  rc={report_result.returncode}")
            if report_result.stdout:
                log_lines.append(
                    f"  stdout: {report_result.stdout.rstrip()}"
                )
            if report_result.stderr:
                log_lines.append(
                    f"  stderr: {report_result.stderr.rstrip()}"
                )

            if report_result.returncode != 0:
                return self._fail_job(
                    job_id,
                    error=(
                        "tax report CLI failed "
                        f"(rc={report_result.returncode})"
                    ),
                    log_lines=log_lines,
                    output_dir=output_dir,
                )

            # ── Step 3: copy artefacts to output dir ──────────────────────
            for src in (gains_path, wealth_path, summary_path):
                if src.exists():
                    shutil.copy2(src, output_dir / src.name)

            log_path = self._write_log(output_dir, log_lines)

            output = JobOutput(
                gains_csv_path=str(output_dir / "gains.csv"),
                wealth_csv_path=str(output_dir / "wealth.csv"),
                summary_json_path=str(output_dir / "summary.json"),
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
    def _build_reader_command(
        job_input: JobInput,
        events_path: Path,
    ) -> list[str]:
        """Assemble the blockchain-reader CLI command.

        TODO: The exact flags will be finalised once blockchain-reader
              exposes a multi-account scenario export mode.  For now we
              pass each account as a repeated ``--xrpl-account`` flag.
        """
        cmd: list[str] = [
            settings.BLOCKCHAIN_READER_CLI,
            "--mode", "scenario",
        ]
        for acct in job_input.xrpl_accounts:
            cmd.extend(["--xrpl-account", acct])
        cmd.extend(["--output", str(events_path)])
        return cmd

    @staticmethod
    def _build_report_command(
        job_input: JobInput,
        *,
        events_path: Path | None,
        gains_path: Path,
        wealth_path: Path,
        summary_path: Path,
    ) -> list[str]:
        """Assemble the country-specific tax-report CLI command.

        ``events_path`` is ``None`` when the job is CSV-only (no XRPL
        accounts).  In that case the ``--xrpl-scenario`` flag is omitted.

        Each entry in ``job_input.csv_files`` is appended as a
        ``--generic-events-csv <path>`` argument.  The tax CLI merges
        XRPL events and generic CSVs internally.

        TODO: Flag names are aspirational — adjust once the taxspine CLIs
              are finalised.
        """
        if job_input.country == Country.NORWAY:
            cmd: list[str] = [settings.TAXSPINE_NOR_REPORT_CLI]
        elif job_input.country == Country.UK:
            cmd = [settings.TAXSPINE_UK_REPORT_CLI]
        else:
            # Unreachable thanks to the Country enum, but be safe.
            raise ValueError(f"Unsupported country: {job_input.country}")

        # XRPL scenario (only when blockchain-reader ran)
        if events_path is not None:
            cmd.extend(["--xrpl-scenario", str(events_path)])

        # Generic events CSVs
        for csv_path in job_input.csv_files:
            cmd.extend(["--generic-events-csv", csv_path])

        # Common flags
        cmd.extend(["--tax-year", str(job_input.tax_year)])

        # Country-specific output flags
        if job_input.country == Country.NORWAY:
            cmd.extend([
                "--gains-csv", str(gains_path),
                "--wealth-csv", str(wealth_path),
                "--summary-json", str(summary_path),
            ])
        elif job_input.country == Country.UK:
            cmd.extend([
                "--uk-gains-csv", str(gains_path),
                "--uk-wealth-csv", str(wealth_path),
                "--uk-summary-json", str(summary_path),
            ])

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
        been run, then marks the job as COMPLETED.  No gains / wealth /
        summary output paths are set — dry_run is intended for testing
        and previewing the pipeline, not for generating tax outputs.
        """
        log_lines.append("[DRY RUN] — no subprocesses will be executed.")

        events_path: Path | None = None
        if has_xrpl:
            events_path = work_dir / "events.json"
            reader_cmd = self._build_reader_command(job.input, events_path)
            log_lines.append(f"[would run] $ {' '.join(reader_cmd)}")

        gains_path = work_dir / "gains.csv"
        wealth_path = work_dir / "wealth.csv"
        summary_path = work_dir / "summary.json"

        report_cmd = self._build_report_command(
            job.input,
            events_path=events_path,
            gains_path=gains_path,
            wealth_path=wealth_path,
            summary_path=summary_path,
        )
        log_lines.append(
            f"[would run] $ {' '.join(str(c) for c in report_cmd)}"
        )

        log_path = self._write_log(output_dir, log_lines)
        output = JobOutput(log_path=str(log_path))
        return self.store.update_job(
            job_id, status=JobStatus.COMPLETED, output=output,
        )
