"""Job orchestration service layer.

This module owns all job-lifecycle logic.  It calls blockchain-reader and
taxspine-* CLIs via ``subprocess.run`` to produce tax-report artefacts.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
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
        job = Job(id=job_id, status=JobStatus.PENDING, input=job_input)
        return self.store.add(job)

    def get_job(self, job_id: str) -> Job | None:
        return self.store.get(job_id)

    def list_jobs(self) -> list[Job]:
        return self.store.list()

    # ── Execution pipeline ───────────────────────────────────────────────

    def start_job_execution(self, job_id: str) -> Job | None:
        """Run the full XRPL → tax-report pipeline synchronously.

        Lifecycle: PENDING → RUNNING → COMPLETED | FAILED.

        Only PENDING jobs can be started.  If the job is already RUNNING,
        COMPLETED, or FAILED the call returns the job unchanged (idempotent).
        """
        job = self.store.get(job_id)
        if job is None:
            return None

        # Only start from PENDING — return current state otherwise.
        if job.status != JobStatus.PENDING:
            return job

        # ── Mark RUNNING ──────────────────────────────────────────────────
        self.store.update_status(job_id, JobStatus.RUNNING)

        work_dir = self._job_work_dir(job_id)
        output_dir = self._job_output_dir(job_id)
        log_lines: list[str] = []

        try:
            # ── Step 1: blockchain-reader → events.json ───────────────────
            events_path = work_dir / "events.json"
            reader_cmd = self._build_reader_command(job.input, events_path)
            log_lines.append(f"$ {' '.join(reader_cmd)}")

            reader_result = subprocess.run(
                reader_cmd, capture_output=True, text=True, check=False,
            )
            log_lines.append(f"  rc={reader_result.returncode}")
            if reader_result.stdout:
                log_lines.append(f"  stdout: {reader_result.stdout.rstrip()}")
            if reader_result.stderr:
                log_lines.append(f"  stderr: {reader_result.stderr.rstrip()}")

            if reader_result.returncode != 0:
                return self._fail_job(
                    job_id,
                    error=f"blockchain-reader failed (rc={reader_result.returncode})",
                    log_lines=log_lines,
                    output_dir=output_dir,
                )

            # ── Step 2: taxspine report CLI → CSV / JSON ──────────────────
            gains_path = work_dir / "gains.csv"
            wealth_path = work_dir / "wealth.csv"
            summary_path = work_dir / "summary.json"

            report_cmd = self._build_report_command(
                job.input, events_path, gains_path, wealth_path, summary_path,
            )
            log_lines.append(f"$ {' '.join(str(c) for c in report_cmd)}")

            report_result = subprocess.run(
                report_cmd, capture_output=True, text=True, check=False,
            )
            log_lines.append(f"  rc={report_result.returncode}")
            if report_result.stdout:
                log_lines.append(f"  stdout: {report_result.stdout.rstrip()}")
            if report_result.stderr:
                log_lines.append(f"  stderr: {report_result.stderr.rstrip()}")

            if report_result.returncode != 0:
                return self._fail_job(
                    job_id,
                    error=(
                        f"tax report CLI failed (rc={report_result.returncode})"
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
        events_path: Path,
        gains_path: Path,
        wealth_path: Path,
        summary_path: Path,
    ) -> list[str]:
        """Assemble the country-specific tax-report CLI command.

        TODO: Flag names are aspirational — adjust once the taxspine CLIs
              are finalised.
        """
        if job_input.country == Country.NORWAY:
            return [
                settings.TAXSPINE_NOR_REPORT_CLI,
                "--xrpl-scenario", str(events_path),
                "--tax-year", str(job_input.tax_year),
                "--gains-csv", str(gains_path),
                "--wealth-csv", str(wealth_path),
                "--summary-json", str(summary_path),
            ]

        if job_input.country == Country.UK:
            return [
                settings.TAXSPINE_UK_REPORT_CLI,
                "--xrpl-scenario", str(events_path),
                "--tax-year", str(job_input.tax_year),
                "--uk-gains-csv", str(gains_path),
                "--uk-wealth-csv", str(wealth_path),
                "--uk-summary-json", str(summary_path),
            ]

        # Should be unreachable thanks to the Country enum, but be safe.
        raise ValueError(f"Unsupported country: {job_input.country}")

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
