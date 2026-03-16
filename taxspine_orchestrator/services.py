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

import json as _json
import logging
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

# LC-05: XRPL base-58 address pattern (r + 25-34 chars from the base-58 alphabet,
# excluding 0, O, I, and l).  Used to redact addresses from execution logs so that
# pseudonymous personal data is not retained in plaintext log files longer than needed.
_XRPL_ADDR_RE = re.compile(r"\br[1-9A-HJ-NP-Za-km-z]{24,34}\b")


def _redact_xrpl_addresses(text: str) -> str:
    """Replace every XRPL address in *text* with ``[XRPL-ADDRESS]``."""
    return _XRPL_ADDR_RE.sub("[XRPL-ADDRESS]", text)


# ── TL-01 / TL-02: provenance annotation ──────────────────────────────────────

# HTML warning banner injected into every HTML report produced with dummy
# valuation (TL-01).  Uses inline styles so it works without any external CSS.
_DRAFT_BANNER = (
    '<div style="background:#ff9800;color:#000;padding:14px 16px;'
    "font-family:monospace;font-size:15px;font-weight:bold;text-align:center;"
    'border-bottom:3px solid #e65100;position:sticky;top:0;z-index:9999">'
    "&#9888; DRAFT &mdash; Dummy valuation used: NOK values are placeholders "
    "and MUST NOT be filed with Skatteetaten. "
    "Re-run with valuation_mode=price_table before submitting."
    "</div>"
)


def _annotate_rf1159_with_provenance(
    path: Path,
    *,
    valuation_mode: str,
    price_source: str,
    price_table_path: str | None,
) -> None:
    """Add a ``_provenance`` block to an RF-1159 JSON file in-place.

    TL-01: Makes dummy-valuation output distinguishable from real output by
    setting ``draft=true`` when ``valuation_mode == "dummy"``.
    TL-02: Records the price source so a tax auditor can verify provenance.
    """
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    data["_provenance"] = {
        "valuation_mode": valuation_mode,
        "price_source": price_source,
        "price_table_path": price_table_path,
        "draft": valuation_mode == "dummy",
        "generated_by": "taxspine-orchestrator",
    }
    path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _inject_draft_banner(path: Path) -> None:
    """Insert a visible draft-warning banner at the top of an HTML report (TL-01).

    Inserts immediately after the opening ``<body`` tag if present, otherwise
    prepends the banner to the document.
    """
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return
    if "<body" in html:
        idx = html.find("<body")
        end = html.find(">", idx)
        if end != -1:
            html = html[: end + 1] + "\n" + _DRAFT_BANNER + html[end + 1 :]
        else:
            html = _DRAFT_BANNER + html
    else:
        html = _DRAFT_BANNER + html
    path.write_text(html, encoding="utf-8")

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

        # API-04: Accept PENDING or RUNNING.  RUNNING means the start_job
        # endpoint already performed the CAS transition — skip re-marking.
        # Any other terminal state (COMPLETED, FAILED, CANCELLED) returns early.
        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return job

        has_xrpl = bool(job.input.xrpl_accounts)
        has_csv = bool(job.input.csv_files)

        # ── Mark RUNNING (idempotent when endpoint already did the CAS) ──────
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

            # ── Guard: non-generic CSVs are incompatible with XRPL jobs ──
            # taxspine-xrpl-nor only accepts --generic-events-csv.  Firi,
            # Coinbase, and other native-format CSVs cannot be attached to an
            # XRPL job; the caller must submit a separate CSV-only job instead.
            if has_xrpl and has_csv:
                unsupported_specs = [
                    spec for spec in job.input.csv_files
                    if spec.source_type != CsvSourceType.GENERIC_EVENTS
                ]
                if unsupported_specs:
                    bad = ", ".join(
                        f"{spec.source_type.value}:{spec.path}"
                        for spec in unsupported_specs
                    )
                    return self._fail_job(
                        job_id,
                        error=(
                            "Mixed XRPL+CSV jobs only support generic-events CSV files. "
                            f"The following files use unsupported source types: {bad}. "
                            "Submit a separate CSV-only job for these files."
                        ),
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

            # ── Carry-forward lots (Norway jobs only) ─────────────────────
            # Load FIFO lots saved from tax_year-1 and inject them as
            # synthetic opening-position TRADE events.  The CSV is prepended
            # to XRPL and NOR_MULTI invocations so all sources share the same
            # opening inventory.  PER_FILE mode does not support carry-forward
            # (each invocation has an isolated lot pool).
            carry_forward_spec: CsvFileSpec | None = None
            if job.input.country == Country.NORWAY:
                carry_csv = self._maybe_write_carry_forward_csv(
                    output_dir, job.input.tax_year
                )
                if carry_csv is not None:
                    carry_forward_spec = CsvFileSpec(path=str(carry_csv))
                    log_lines.append(
                        f"[carry-forward] injecting {len(carry_csv.read_text(encoding='utf-8').splitlines()) - 1} "
                        f"lot(s) from {job.input.tax_year - 1}: {carry_csv}"
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
                    # RF-1159 export is only meaningful for Norway jobs.
                    rf1159_dest: Path | None = (
                        output_dir / f"rf1159{suffix}.json"
                        if job.input.country == Country.NORWAY
                        else None
                    )

                    # Attach CSV files only to the primary account (idx == 0)
                    # when this is a mixed workspace.  This keeps the FIFO lot
                    # pool unified and prevents formue double-counting.
                    csv_files_for_account: list[CsvFileSpec] = (
                        list(job.input.csv_files) if (has_csv and idx == 0) else []
                    )
                    # Prepend carry-forward lots to the primary account so
                    # the opening position is established before any events.
                    if carry_forward_spec is not None and idx == 0:
                        csv_files_for_account = [carry_forward_spec] + csv_files_for_account

                    xrpl_cmd = self._build_xrpl_command(
                        job.input,
                        account=account,
                        html_path=html_dest,
                        csv_files=csv_files_for_account,
                        rf1159_json_path=rf1159_dest,
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

                    if rf1159_dest is not None and rf1159_dest.exists():
                        all_rf1159_json_paths.append(str(rf1159_dest))
                        if rf1159_json_path is None:
                            rf1159_json_path = rf1159_dest

            # ── Step 2: CSVs → taxspine-nor-report / taxspine-nor-multi ───
            # Only run this step for CSV-only workspaces.  When XRPL accounts
            # are present, generic-events CSV files were already merged in Step 1.
            if has_csv and not has_xrpl:
                if (
                    job.input.pipeline_mode == PipelineMode.NOR_MULTI
                    and job.input.country == Country.NORWAY
                ):
                    # Single combined invocation — all CSV sources in one shot.
                    # Prepend carry-forward lots (if any) so the FIFO engine
                    # sees the prior-year opening inventory first.
                    html_dest = output_dir / "report_combined.html"
                    rf1159_dest_nm = output_dir / "rf1159.json"
                    review_dest_nm = output_dir / "review.json"
                    nor_multi_specs = (
                        [carry_forward_spec] + list(job.input.csv_files)
                        if carry_forward_spec is not None
                        else list(job.input.csv_files)
                    )
                    nor_multi_cmd = self._build_nor_multi_command(
                        job.input,
                        csv_specs=nor_multi_specs,
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

            # ── Step 2.5: annotate output files with provenance ───────────
            # TL-01 / TL-02: determine the effective price source string and
            # annotate all RF-1159 JSON files with a _provenance block.  If the
            # job used dummy valuation, also inject a visible draft banner into
            # every HTML report so the output cannot be mistaken for a real filing.
            _valuation_mode = job.input.valuation_mode.value
            _price_source = (
                "price_table_csv" if _valuation_mode == "price_table" else "dummy"
            )
            _price_table_path = job.input.csv_prices_path

            for _rf1159_path_str in all_rf1159_json_paths:
                _annotate_rf1159_with_provenance(
                    Path(_rf1159_path_str),
                    valuation_mode=_valuation_mode,
                    price_source=_price_source,
                    price_table_path=_price_table_path,
                )

            if _valuation_mode == "dummy":
                for _html_path_str in all_html_paths:
                    _inject_draft_banner(Path(_html_path_str))

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
                valuation_mode_used=_valuation_mode,
                price_source=_price_source,
                price_table_path=_price_table_path,
            )
            # API-07: guard against overwriting a user-initiated CANCELLED state
            # with COMPLETED.  If the job was cancelled mid-run the terminal
            # CANCELLED status must be preserved.
            current = self.store.get(job_id)
            if current and current.status == JobStatus.CANCELLED:
                return current
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
        rf1159_json_path: Path | None = None,
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
            --rf1159-json          PATH     (optional; RF-1159 JSON export)
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

        if rf1159_json_path is not None:
            cmd.extend(["--rf1159-json", str(rf1159_json_path)])

        if review_json_path is not None:
            cmd.extend(["--review-json", str(review_json_path)])

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
    def _maybe_write_carry_forward_csv(output_dir: Path, tax_year: int) -> Path | None:
        """Load prior-year FIFO lots and write them as synthetic TRADE events.

        Reads carry-forward lots from the lot persistence store for
        ``tax_year - 1`` and writes a generic-events CSV that the tax CLI
        will process as opening positions for ``tax_year``.  This bridges
        the orchestrator's lot store into the CLI's FIFO engine.

        Returns the path to the generated CSV, or ``None`` when:
        - the ``tax_spine`` package is not installed
        - the lot store database does not exist yet
        - no lots were saved for the prior year
        - all prior-year lots have a missing cost basis

        The synthetic events are dated ``{tax_year-1}-12-31T23:59:59Z`` so
        they sit chronologically before the current year's events in the
        FIFO engine, yet do not appear in the current year's tax report.
        """
        try:
            from tax_spine.pipeline.lot_store import LotPersistenceStore  # noqa: PLC0415
        except ImportError:
            _log.debug("LotPersistenceStore not available — skipping carry-forward")
            return None

        db_path = settings.LOT_STORE_DB
        if not db_path.is_file():
            _log.debug("Lot store not found at %s — no carry-forward", db_path)
            return None

        prior_year = tax_year - 1
        try:
            with LotPersistenceStore(str(db_path)) as store:
                if prior_year not in store.list_years():
                    _log.debug("No lots saved for %d — skipping carry-forward", prior_year)
                    return None
                lots = store.load_carry_forward(prior_year)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not read carry-forward lots for %d: %s", prior_year, exc)
            return None

        if not lots:
            return None

        # Build synthetic TRADE rows: one buy per lot with a resolved basis.
        # Lots without a resolved NOK basis are skipped; they cannot be
        # expressed as a cost-basis TRADE event.
        ts = f"{prior_year}-12-31T23:59:59Z"
        rows: list[str] = []
        for i, lot in enumerate(lots):
            if lot.remaining_cost_basis_nok is None:
                _log.debug(
                    "Skipping carry-forward lot %s for %s — missing basis",
                    lot.lot_id, lot.asset,
                )
                continue
            event_id = f"carry_{prior_year}_{i}"
            rows.append(
                f"{event_id},{ts},TRADE,carry_forward,orchestrator,"
                f"{lot.asset},{lot.remaining_quantity},"
                f"NOK,{lot.remaining_cost_basis_nok},"
                f",,,,,,"
            )

        if not rows:
            _log.debug("All carry-forward lots for %d have missing basis — skipping", prior_year)
            return None

        carry_csv = output_dir / f"carry_forward_{prior_year}.csv"
        header = (
            "event_id,timestamp,event_type,source,account,"
            "asset_in,amount_in,asset_out,amount_out,"
            "fee_asset,fee_amount,tx_hash,exchange_tx_id,label,"
            "complex_tax_treatment,note"
        )
        carry_csv.write_text(
            header + "\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )
        _log.info(
            "Wrote carry-forward CSV: %d lots from %d → %s",
            len(rows), prior_year, carry_csv,
        )
        return carry_csv

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
        # SEC-02: allowlist-only sanitisation — only [A-Za-z0-9_-] permitted.
        # This is stricter than the previous separator-only replacement, which
        # still admitted '..' sequences that could escape DEDUP_DIR.
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", source_slug)
        resolved = (settings.DEDUP_DIR / f"{safe}.db").resolve()
        try:
            resolved.relative_to(settings.DEDUP_DIR.resolve())
        except ValueError:
            raise ValueError(
                f"Resolved dedup path {resolved!r} escapes DEDUP_DIR — "
                f"source slug {source_slug!r} rejected"
            )
        return resolved

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
        """Write *lines* to ``execution.log`` inside *output_dir*.

        LC-05: XRPL account addresses are redacted before writing so that
        pseudonymous personal data is not retained verbatim in log files.
        """
        log_path = output_dir / "execution.log"
        content = _redact_xrpl_addresses("\n".join(lines) + "\n")
        log_path.write_text(content, encoding="utf-8")
        return log_path

    def _fail_job(
        self,
        job_id: str,
        *,
        error: str,
        log_lines: list[str],
        output_dir: Path,
    ) -> Job | None:
        """Mark a job as FAILED, persisting the error and log.

        API-07: does not overwrite a CANCELLED terminal state — if the user
        cancelled the job mid-run, CANCELLED takes precedence over FAILED.
        """
        log_path = self._write_log(output_dir, log_lines)
        output = JobOutput(
            error_message=error,
            log_path=str(log_path),
        )
        # Preserve CANCELLED: do not overwrite a user-initiated cancel with FAILED.
        current = self.store.get(job_id)
        if current and current.status == JobStatus.CANCELLED:
            return current
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

        # Note carry-forward availability without writing the actual CSV.
        dry_carry_forward_spec: CsvFileSpec | None = None
        if job.input.country == Country.NORWAY:
            prior_year = job.input.tax_year - 1
            carry_path = output_dir / f"carry_forward_{prior_year}.csv"
            if settings.LOT_STORE_DB.is_file():
                log_lines.append(
                    f"[carry-forward] lot store found at {settings.LOT_STORE_DB}; "
                    f"carry-forward CSV would be written to {carry_path}"
                )
                dry_carry_forward_spec = CsvFileSpec(path=str(carry_path))
            else:
                log_lines.append(
                    f"[carry-forward] lot store not found at {settings.LOT_STORE_DB} — "
                    "no carry-forward (first run or lot store not yet initialised)"
                )

        if has_xrpl:
            for idx, account in enumerate(job.input.xrpl_accounts):
                suffix = f"_{idx}" if len(job.input.xrpl_accounts) > 1 else ""
                html_path = output_dir / f"report{suffix}.html"
                review_path = output_dir / f"review{suffix}.json"
                rf1159_dry_path: Path | None = (
                    output_dir / f"rf1159{suffix}.json"
                    if job.input.country == Country.NORWAY
                    else None
                )
                # Primary account (idx == 0) gets all CSV files in mixed workspace.
                dry_csv_files: list[CsvFileSpec] = (
                    list(job.input.csv_files) if (has_csv and idx == 0) else []
                )
                if dry_carry_forward_spec is not None and idx == 0:
                    dry_csv_files = [dry_carry_forward_spec] + dry_csv_files
                cmd = self._build_xrpl_command(
                    job.input,
                    account=account,
                    html_path=html_path,
                    csv_files=dry_csv_files,
                    rf1159_json_path=rf1159_dry_path,
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
                dry_nm_specs = (
                    [dry_carry_forward_spec] + list(job.input.csv_files)
                    if dry_carry_forward_spec is not None
                    else list(job.input.csv_files)
                )
                cmd = self._build_nor_multi_command(
                    job.input,
                    csv_specs=dry_nm_specs,
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
        # API-07: preserve CANCELLED — do not overwrite with COMPLETED.
        current = self.store.get(job_id)
        if current and current.status == JobStatus.CANCELLED:
            return current
        return self.store.update_job(
            job_id, status=JobStatus.COMPLETED, output=output,
        )
