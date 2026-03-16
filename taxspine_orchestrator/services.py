"""Job orchestration service layer.

This module owns all job-lifecycle logic.  It calls blockchain-reader and
taxspine-* CLIs via ``subprocess.run`` to produce tax-report artefacts.

Supported job types
-------------------
- **XRPL-only** — blockchain-reader exports events, tax CLI processes them.
- **CSV-only** — generic-events CSVs are passed straight to the tax CLI.
- **Combined** — XRPL + CSV files are merged into a SINGLE ``taxspine-xrpl-nor``
  invocation per account.  The primary account gets all CSV files attached via
  ``--generic-events-csv``; additional accounts each get their own invocation
  (CSV files already included with the primary account).  This ensures a unified
  FIFO lot pool, correct transfer linking, and no double-counting of formue.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .models import Country, CsvFileSpec, CsvSourceType, Job, JobInput, JobOutput, JobStatus, PipelineMode, ValuationMode
from .storage import InMemoryJobStore, SqliteJobStore

_log = logging.getLogger(__name__)


class JobService:
    """Create, query, and execute tax jobs."""

    def __init__(self, store: InMemoryJobStore | SqliteJobStore) -> None:
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
        - Combined   — runs a SINGLE ``taxspine-xrpl-nor`` invocation for the
                        primary account with ALL CSV files attached via
                        ``--generic-events-csv``.  Additional accounts (if any)
                        each get their own ``taxspine-xrpl-nor`` invocation
                        without CSV files (CSV events were included with the
                        primary account).  This keeps a unified FIFO lot pool.
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

        output_dir = self._job_output_dir(job_id)
        log_lines: list[str] = []

        try:
            # ── Guard: valuation_mode consistency ────────────────────────
            if job.input.valuation_mode == ValuationMode.PRICE_TABLE:
                if job.input.csv_prices_path is None:
                    # Auto-resolve: look for the cached combined NOK price CSV
                    # written by POST /prices/fetch (combined_nok_{year}.csv).
                    auto_path = settings.PRICES_DIR / f"combined_nok_{job.input.tax_year}.csv"
                    if auto_path.is_file():
                        log_lines.append(
                            f"[prices] auto-resolved price table: {auto_path}"
                        )
                        job = job.model_copy(
                            update={"input": job.input.model_copy(
                                update={"csv_prices_path": str(auto_path)}
                            )}
                        )
                    else:
                        return self._fail_job(
                            job_id,
                            error=(
                                f"valuation_mode=price_table requires csv_prices_path "
                                f"(or a cached price file at {auto_path}; "
                                f"call POST /prices/fetch to download prices automatically)"
                            ),
                            log_lines=log_lines,
                            output_dir=output_dir,
                        )
                # F-11: resolve path and verify existence.
                # Note: the price table CSV is an operator-supplied reference file
                # and is NOT constrained to UPLOAD_DIR (it may live in /data or
                # anywhere the operator has staged it).  Path-traversal protection
                # for untrusted user uploads is handled at the /workspace/csv
                # endpoint (see TestPathContainment in test_security.py).
                prices_path = Path(job.input.csv_prices_path).resolve()
                if not prices_path.is_file():
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
                    output_dir=output_dir,
                    log_lines=log_lines,
                )

            # ── Guard: verify CSV files exist ─────────────────────────────
            for spec in job.input.csv_files:
                if not Path(spec.path).is_file():
                    return self._fail_job(
                        job_id,
                        error=f"CSV file not found: {spec.path}",
                        log_lines=log_lines,
                        output_dir=output_dir,
                    )

            # ── Step 1: XRPL accounts → taxspine-xrpl-nor ────────────────
            # taxspine-xrpl-nor handles the full XRPL → Norway pipeline
            # internally (no separate blockchain-reader step required).
            #
            # Mixed workspace (XRPL + CSV):
            #   The primary account gets ALL CSV files attached via
            #   --generic-events-csv so that XRPL and CSV events share a
            #   single FIFO lot pool.  Additional accounts run separately
            #   without CSV files (already merged with primary).
            #
            # XRPL-only workspace:
            #   One invocation per account, no CSV files attached.
            report_html_path: Path | None = None
            all_html_paths: list[str] = []
            rf1159_json_path: Path | None = None
            all_rf1159_json_paths: list[str] = []
            review_json_path: Path | None = None
            all_review_json_paths: list[str] = []

            if has_xrpl:
                for idx, account in enumerate(job.input.xrpl_accounts):
                    suffix = f"_{idx}" if len(job.input.xrpl_accounts) > 1 else ""
                    html_dest = output_dir / f"report{suffix}.html"
                    review_dest = output_dir / f"review{suffix}.json"

                    # Attach CSV files only to the primary account (idx == 0)
                    # when this is a mixed workspace.  This keeps the FIFO lot
                    # pool unified and prevents formue double-counting.
                    csv_files_for_account = (
                        job.input.csv_files if (has_csv and idx == 0) else []
                    )

                    xrpl_cmd = self._build_xrpl_command(
                        job.input,
                        account=account,
                        html_path=html_dest,
                        csv_files=csv_files_for_account,
                        review_json_path=review_dest,
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

                    # Track all generated HTML reports.
                    if html_dest.exists():
                        all_html_paths.append(str(html_dest))
                        if report_html_path is None:
                            report_html_path = html_dest

                    if review_dest.exists():
                        all_review_json_paths.append(str(review_dest))
                        if review_json_path is None:
                            review_json_path = review_dest

            # ── Step 2: CSVs → taxspine-nor-report / taxspine-nor-multi ───
            # Only run this step for CSV-only workspaces.  When XRPL accounts
            # are present, generic-events CSV files were already merged in Step 1.
            if has_csv and not has_xrpl:
                if (
                    job.input.pipeline_mode == PipelineMode.NOR_MULTI
                    and job.input.country == Country.NORWAY
                ):
                    # Single combined invocation — all CSV sources in one shot.
                    html_dest = output_dir / "report_combined.html"
                    rf1159_dest_nm = output_dir / "rf1159.json"
                    review_dest_nm = output_dir / "review.json"
                    nor_multi_cmd = self._build_nor_multi_command(
                        job.input,
                        csv_specs=job.input.csv_files,
                        html_path=html_dest,
                        rf1159_json_path=rf1159_dest_nm,
                        review_json_path=review_dest_nm,
                    )
                    log_lines.append(f"$ {' '.join(str(c) for c in nor_multi_cmd)}")

                    nor_multi_result = subprocess.run(
                        nor_multi_cmd, capture_output=True, text=True, check=False,
                    )
                    log_lines.append(f"  rc={nor_multi_result.returncode}")
                    if nor_multi_result.stdout:
                        log_lines.append(f"  stdout:\n{nor_multi_result.stdout.rstrip()}")
                    if nor_multi_result.stderr:
                        log_lines.append(f"  stderr:\n{nor_multi_result.stderr.rstrip()}")

                    if nor_multi_result.returncode != 0:
                        return self._fail_job(
                            job_id,
                            error=(
                                f"taxspine-nor-multi failed "
                                f"(rc={nor_multi_result.returncode})"
                            ),
                            log_lines=log_lines,
                            output_dir=output_dir,
                        )

                    if html_dest.exists():
                        all_html_paths.append(str(html_dest))
                        report_html_path = html_dest

                    if rf1159_dest_nm.exists():
                        all_rf1159_json_paths.append(str(rf1159_dest_nm))
                        rf1159_json_path = rf1159_dest_nm

                    if review_dest_nm.exists():
                        all_review_json_paths.append(str(review_dest_nm))
                        review_json_path = review_dest_nm

                else:
                    # Per-file mode (default): one taxspine-nor-report per CSV.
                    for spec in job.input.csv_files:
                        csv_stem = Path(spec.path).stem
                        html_dest = output_dir / f"report_{csv_stem}.html"
                        rf1159_dest_pf: Path | None = None
                        review_dest_pf: Path | None = None
                        if job.input.country == Country.NORWAY:
                            rf1159_dest_pf = output_dir / f"rf1159_{csv_stem}.json"
                            review_dest_pf = output_dir / f"review_{csv_stem}.json"

                        csv_cmd = self._build_csv_command(
                            job.input,
                            csv_spec=spec,
                            html_path=html_dest,
                            rf1159_json_path=rf1159_dest_pf,
                            review_json_path=review_dest_pf,
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
                                    f"taxspine-nor-report failed for {spec.path} "
                                    f"(rc={csv_result.returncode})"
                                ),
                                log_lines=log_lines,
                                output_dir=output_dir,
                            )

                        if html_dest.exists():
                            all_html_paths.append(str(html_dest))
                            if report_html_path is None:
                                report_html_path = html_dest

                        if rf1159_dest_pf is not None and rf1159_dest_pf.exists():
                            all_rf1159_json_paths.append(str(rf1159_dest_pf))
                            if rf1159_json_path is None:
                                rf1159_json_path = rf1159_dest_pf

                        if review_dest_pf is not None and review_dest_pf.exists():
                            all_review_json_paths.append(str(review_dest_pf))
                            if review_json_path is None:
                                review_json_path = review_dest_pf

            # ── Step 3: write log + build output record ───────────────────
            log_path = self._write_log(output_dir, log_lines)

            output = JobOutput(
                report_html_path=str(report_html_path) if report_html_path else None,
                report_html_paths=all_html_paths,
                rf1159_json_path=str(rf1159_json_path) if rf1159_json_path else None,
                rf1159_json_paths=all_rf1159_json_paths,
                review_json_path=str(review_json_path) if review_json_path else None,
                review_json_paths=all_review_json_paths,
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
        csv_files: list[CsvFileSpec] | None = None,
        review_json_path: Path | None = None,
    ) -> list[str]:
        """Build a ``taxspine-xrpl-nor`` command for a single XRPL account.

        taxspine-xrpl-nor handles the full pipeline internally:
        it fetches transactions from the ledger and runs the Norway
        tax pipeline.  No blockchain-reader step is needed.

        Real CLI flags (as of Phase 2):
            --account              ADDRESS  (required)
            --year                 YEAR     (required)
            --generic-events-csv   PATH     (optional; repeatable — one per CSV)
            --csv-prices           PATH     (optional; CSV format price table)
            --debug-valuation               (optional)
            --html-output          PATH     (optional; self-contained HTML report)

        ``csv_files`` is an optional list of CSV file specs to attach.
        Only GENERIC_EVENTS files are supported by taxspine-xrpl-nor.
        Non-generic files are skipped with a warning (they must be run via
        taxspine-nor-report instead).
        """
        cmd: list[str] = [
            settings.TAXSPINE_XRPL_NOR_CLI,
            "--account", account,
            "--year", str(job_input.tax_year),
            "--html-output", str(html_path),
        ]

        # Attach generic-events CSV files (mixed workspace: primary account only).
        # taxspine-xrpl-nor only supports --generic-events-csv; non-generic formats
        # (Coinbase, Firi) must be processed by taxspine-nor-report separately.
        for spec in (csv_files or []):
            if spec.source_type == CsvSourceType.GENERIC_EVENTS:
                cmd.extend(["--generic-events-csv", spec.path])
            else:
                _log.warning(
                    "Skipping %s file %r in XRPL job — "
                    "taxspine-xrpl-nor only supports generic-events CSVs. "
                    "Run a CSV-only job with this file to process it.",
                    spec.source_type.value,
                    spec.path,
                )

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
        csv_spec: CsvFileSpec,
        html_path: Path,
        rf1159_json_path: Path | None = None,
        review_json_path: Path | None = None,
    ) -> list[str]:
        """Build a ``taxspine-nor-report`` command for a CSV file.

        Routes to the correct CLI flag based on ``csv_spec.source_type``:

        - GENERIC_EVENTS → ``--generic-events-csv PATH``
        - COINBASE_CSV   → ``--coinbase-csv PATH``
        - FIRI_CSV       → ``--input PATH --source-type firi_csv``

        Common flags:
            --year                YEAR  (required)
            --csv-prices          PATH  (optional; CSV format price table)
            --debug-valuation           (optional)
            --html-output         PATH  (optional; self-contained HTML report)
        """
        if job_input.country == Country.NORWAY:
            cmd: list[str] = [settings.TAXSPINE_NOR_REPORT_CLI]
        elif job_input.country == Country.UK:
            cmd = [settings.TAXSPINE_UK_REPORT_CLI]
        else:
            raise ValueError(f"Unsupported country: {job_input.country}")

        if csv_spec.source_type == CsvSourceType.GENERIC_EVENTS:
            cmd.extend(["--generic-events-csv", csv_spec.path])
        elif csv_spec.source_type == CsvSourceType.COINBASE_CSV:
            cmd.extend(["--coinbase-csv", csv_spec.path])
        elif csv_spec.source_type == CsvSourceType.FIRI_CSV:
            cmd.extend(["--input", csv_spec.path, "--source-type", "firi_csv"])
        else:
            raise ValueError(f"Unsupported source_type: {csv_spec.source_type}")

        cmd.extend(["--year", str(job_input.tax_year), "--html-output", str(html_path)])

        if (
            job_input.valuation_mode == ValuationMode.PRICE_TABLE
            and job_input.csv_prices_path is not None
        ):
            cmd.extend(["--csv-prices", job_input.csv_prices_path])

        if job_input.debug_valuation:
            cmd.append("--debug-valuation")

        if job_input.country == Country.NORWAY:
            if rf1159_json_path is not None:
                cmd.extend(["--rf1159-json", str(rf1159_json_path)])

        return cmd

    # Mapping from CsvSourceType to the --source TYPE name used by taxspine-nor-multi.
    _NOR_MULTI_SOURCE_TYPE: dict[CsvSourceType, str] = {
        CsvSourceType.GENERIC_EVENTS: "generic_events",
        CsvSourceType.COINBASE_CSV: "coinbase",
        CsvSourceType.FIRI_CSV: "firi",
    }

    @staticmethod
    def _build_nor_multi_command(
        job_input: JobInput,
        *,
        csv_specs: list[CsvFileSpec],
        html_path: Path,
        rf1159_json_path: Path | None = None,
        review_json_path: Path | None = None,
    ) -> list[str]:
        """Build a ``taxspine-nor-multi`` command for all CSV files at once.

        taxspine-nor-multi accepts repeated ``--source TYPE:PATH`` arguments,
        builds a unified FIFO lot pool across all sources, and emits a single
        combined HTML report.

        Real CLI flags:
            --source    TYPE:PATH  (repeatable; one per CSV file)
            --year      YEAR       (required)
            --html-output PATH     (optional; self-contained HTML report)
            --csv-prices  PATH     (optional; CSV format price table)
            --debug-valuation      (optional)

        Source TYPE values:
            ``generic_events`` — spine's own generic-events CSV schema
            ``coinbase``        — Coinbase RAWTX export
            ``firi``            — Firi CSV export
        """
        cmd: list[str] = [
            settings.TAXSPINE_NOR_MULTI_CLI,
            "--year", str(job_input.tax_year),
            "--html-output", str(html_path),
        ]

        source_type_map = JobService._NOR_MULTI_SOURCE_TYPE
        for spec in csv_specs:
            type_name = source_type_map.get(spec.source_type, spec.source_type.value)
            cmd.extend(["--source", f"{type_name}:{spec.path}"])

        if (
            job_input.valuation_mode == ValuationMode.PRICE_TABLE
            and job_input.csv_prices_path is not None
        ):
            cmd.extend(["--csv-prices", job_input.csv_prices_path])

        if job_input.debug_valuation:
            cmd.append("--debug-valuation")

        if rf1159_json_path is not None:
            cmd.extend(["--rf1159-json", str(rf1159_json_path)])

        return cmd

    @staticmethod
    def _dedup_store_path(source_slug: str) -> Path:
        """Return the per-source dedup store path inside DEDUP_DIR.

        Each source slug maps to a dedicated SQLite file so different exchange
        formats never share key namespaces:
          - ``xrpl_{account}``  → one file per XRPL account address
          - ``generic_events``  → one file for all generic-events CSVs
          - ``coinbase_csv``    → one file for all Coinbase CSV uploads
          - ``firi_csv``        → one file for all Firi CSV uploads
          - ``nor_multi``       → one file for multi-source nor_multi runs

        The directory is created lazily by ``ensure_dirs()`` at startup.
        """
        # Sanitise the slug to a filesystem-safe name (replace path separators).
        safe = source_slug.replace("/", "_").replace("\\", "_")
        return settings.DEDUP_DIR / f"{safe}.db"

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
        output_dir: Path,
        log_lines: list[str],
    ) -> Job | None:
        """Complete a dry-run job without calling any subprocesses.

        Writes an execution log listing the commands that *would* have
        been run, then marks the job as COMPLETED.  No output file paths
        are set — dry_run is intended for testing and previewing only.
        """
        log_lines.append("[DRY RUN] — no subprocesses will be executed.")

        has_csv = bool(job.input.csv_files)

        if has_xrpl:
            for idx, account in enumerate(job.input.xrpl_accounts):
                suffix = f"_{idx}" if len(job.input.xrpl_accounts) > 1 else ""
                html_path = output_dir / f"report{suffix}.html"
                review_path = output_dir / f"review{suffix}.json"
                # Primary account (idx == 0) gets all CSV files in mixed workspace.
                csv_files_for_account = (
                    job.input.csv_files if (has_csv and idx == 0) else []
                )
                cmd = self._build_xrpl_command(
                    job.input,
                    account=account,
                    html_path=html_path,
                    csv_files=csv_files_for_account,
                    review_json_path=review_path,
                )
                log_lines.append(f"[would run] $ {' '.join(str(c) for c in cmd)}")

        # CSV-only: nor_multi = single combined call; per_file = one per CSV.
        if has_csv and not has_xrpl:
            if (
                job.input.pipeline_mode == PipelineMode.NOR_MULTI
                and job.input.country == Country.NORWAY
            ):
                html_path = output_dir / "report_combined.html"
                cmd = self._build_nor_multi_command(
                    job.input,
                    csv_specs=job.input.csv_files,
                    html_path=html_path,
                    rf1159_json_path=output_dir / "rf1159.json",
                    review_json_path=output_dir / "review.json",
                )
                log_lines.append(f"[would run] $ {' '.join(str(c) for c in cmd)}")
            else:
                for spec in job.input.csv_files:
                    csv_stem = Path(spec.path).stem
                    html_path = output_dir / f"report_{csv_stem}.html"
                    rf1159_dest: Path | None = None
                    review_dest: Path | None = None
                    if job.input.country == Country.NORWAY:
                        rf1159_dest = output_dir / f"rf1159_{csv_stem}.json"
                        review_dest = output_dir / f"review_{csv_stem}.json"
                    cmd = self._build_csv_command(
                        job.input,
                        csv_spec=spec,
                        html_path=html_path,
                        rf1159_json_path=rf1159_dest,
                        review_json_path=review_dest,
                    )
                    log_lines.append(f"[would run] $ {' '.join(str(c) for c in cmd)}")

        log_path = self._write_log(output_dir, log_lines)
        output = JobOutput(log_path=str(log_path))
        return self.store.update_job(
            job_id, status=JobStatus.COMPLETED, output=output,
        )
