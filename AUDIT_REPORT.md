# taxspine-orchestrator — Full-Spectrum Audit Report

**Date:** 2026-03-16
**Scope:** Complete codebase review across 7 specialist domains
**Methodology:** 7 independent agents with non-overlapping audit scopes, each reading primary source files and producing severity-graded findings

---

## Summary Table

| Severity | Legal | Tax Law | Security | Backend | Frontend | UI/UX | Infra | **Total** |
|----------|------:|--------:|---------:|--------:|---------:|------:|------:|----------:|
| 🔴 CRITICAL | 0 | 0 | 0 | 1 | 1 | 1 | 0 | **3** |
| 🟠 HIGH | 5 | 5 | 3 | 3 | 4 | 2 | 4 | **26** |
| 🟡 MEDIUM | 6 | 9 | 8 | 8 | 6 | 8 | 11 | **56** |
| 🔵 LOW | 2 | 5 | 9 | 4 | 4 | 11 | 10 | **45** |
| ⚪ INFO | 0 | 0 | 0 | 1 | 2 | 1 | 0 | **4** |
| 🧪 MISSING TEST | 0 | 0 | 0 | 4 | 0 | 0 | 0 | **4** |
| **Total** | **13** | **19** | **20** | **21** | **17** | **24** | **25** | **139** |

---

## Agent 1 — Legal & Compliance

*Files reviewed: `main.py`, `config.py`, `models.py`, `prices.py`, `services.py`, `storage.py`, `README.md`*

---

### LC-01 · HIGH · No Data Retention Policy or Right-to-Erasure Mechanism
**Finding:** No documented data retention schedule, automatic expiration, or endpoint to permanently erase a user's job records, transaction CSVs, and account addresses from disk.
**Detail:** Under GDPR and the Norwegian Personopplysningsloven, operators processing personal data (XRPL pseudonymous addresses) must be able to respond to subject-access and erasure requests. There is no API endpoint, admin script, or scheduled job that removes data beyond the DB record.
**Recommendation:** Add a `DELETE /workspace` or admin purge endpoint and document a retention period (e.g., 7 years for tax records, then auto-delete).

---

### LC-02 · HIGH · XRPL Account Addresses Stored in Persistent Plaintext JSON
**Finding:** XRPL account addresses (pseudonymous personal data) are stored indefinitely in `workspace.json` on disk with no encryption or access controls.
**Detail:** `WorkspaceStore` serialises addresses to a plaintext JSON file (`storage.py:286-291`). No file-system ACL or encryption-at-rest is applied. The README does not document that this file contains personal data.
**Recommendation:** Encrypt `workspace.json` at rest or document that the data directory must be encrypted at the OS level, and add a retention/clear lifecycle.

---

### LC-03 · HIGH · CSV Files with Transaction Data Never Automatically Deleted
**Finding:** Uploaded CSV files persist indefinitely under `UPLOAD_DIR`; deleting a job record does not remove associated input files.
**Detail:** `DELETE /jobs/{id}` removes only the DB row (`main.py:255-271`). Input CSVs and output files remain on disk. Transaction histories are personal financial data.
**Recommendation:** Either auto-delete input and output files when a job record is deleted, or implement a time-based cleanup job and document the retention period.

---

### LC-04 · HIGH · Job Records Retain Account Addresses and Case Names Indefinitely
**Finding:** SQLite job records store full `xrpl_accounts` lists and `case_name` labels indefinitely with no field-level deletion on request.
**Detail:** `storage.py:105-112` persists the full job input (including account addresses) in `jobs.db`. No column is marked sensitive or subject to erasure. GDPR Article 17 requires erasure within 30 days of request.
**Recommendation:** Add a per-job data-minimisation endpoint (e.g., null out `xrpl_accounts` after job completion) and document erasure procedure.

---

### LC-05 · HIGH · Execution Logs Capture CLI Arguments Including Account Addresses
**Finding:** Execution logs written to `execution.log` capture subprocess stdout/stderr including CLI flags that contain XRPL account addresses and file paths.
**Detail:** `services.py:367` captures full subprocess output to a log file on disk. Log rotation is not configured. These logs are discoverable via `GET /jobs/{id}/files/log`.
**Recommendation:** Redact address arguments from log files or ensure logs are covered by the same retention and deletion policy as job records.

---

### LC-06 · MEDIUM · Output Files Remain on Disk After Job Deletion
**Finding:** HTML reports, RF-1159 JSON exports, and summary JSONs written to `OUTPUT_DIR` are not deleted when a job record is removed via `DELETE /jobs/{id}`.
**Detail:** Orphaned output files may contain personal financial data (gains, wealth, account addresses in reports) long after the user believes the job has been deleted.
**Recommendation:** Remove the job's output directory on record deletion, or document that output files must be separately purged.

---

### LC-07 · MEDIUM · Third-Party API Calls (Kraken, Norges Bank) Without Consent Mechanism
**Finding:** `POST /prices/fetch` automatically calls the Kraken public OHLC API and the Norges Bank SDMX API; there is no consent flow, opt-out, or documented legal basis.
**Detail:** Under GDPR, even outbound-only HTTP requests to third parties must be covered by a lawful basis. The Norges Bank call in particular may be subject to Norwegian data sovereignty considerations. `prices.py:226-316`.
**Recommendation:** Document the third-party services used in a privacy notice and provide operator-controlled feature flags to disable external calls.

---

### LC-08 · MEDIUM · No Privacy Notice or Data Processing Documentation
**Finding:** No GDPR/Personopplysningsloven privacy notice, data controller/processor agreements, or legal basis documentation exists anywhere in the repository.
**Detail:** The README describes the system as an "Internal API" but provides no data classification, legal basis, or contact details for the data controller. Operators deploying this tool are exposed.
**Recommendation:** Add a `PRIVACY.md` document describing data flows, retention policy, legal basis, and contact for erasure requests.

---

### LC-09 · MEDIUM · GET /jobs?query= Allows Free-Text Search on Potentially Identifying Case Names
**Finding:** `GET /jobs?query=...` accepts substring match on `case_name` which may contain user-identifiable labels without any access-control boundary.
**Detail:** `main.py:183-198`. If multiple users share an instance, any user with API access can search other users' case names. No per-user isolation exists.
**Recommendation:** Either enforce single-operator usage in the README or add per-user job isolation if multi-user deployment is intended.

---

### LC-10 · MEDIUM · RF-1159 Output Contains No "Draft — Do Not File" Disclaimer
**Finding:** RF-1159 JSON and HTML report outputs do not include any disclaimer that output is a draft for professional review and must not be filed directly with Skatteetaten.
**Detail:** Automated tax calculations carry regulatory risk if users file them without professional review. The README does not warn of this. Skatteetaten holds taxpayers (not software vendors) liable.
**Recommendation:** Add a prominent disclaimer in every RF-1159 JSON and HTML report output, and in the README.

---

### LC-11 · LOW · No Audit Log for Data Access or Deletion
**Finding:** No log of who accessed which job records, CSV files, or account addresses; `DELETE /jobs/{id}` leaves no deletion audit trail.
**Detail:** In a regulatory audit, the operator cannot demonstrate who accessed what data and when. This is required under GDPR Article 30 records of processing.
**Recommendation:** Add structured access logging (at minimum: endpoint, timestamp, job_id) to a separate audit log file distinct from the execution log.

---

### LC-12 · MEDIUM · Completed Job Records Are Mutable — No Append-Only Audit Trail
**Finding:** `SqliteJobStore._upsert()` issues an `UPDATE` on completed job records with no guard preventing post-completion overwrites, allowing a finalised tax computation record to be silently modified.
**Detail:** `storage.py` uses `UPDATE jobs SET status = ?, data = ?` with no check on current status before writing. The cancel endpoint also calls `update_status` without a completed-state guard. Tax records that may support a self-assessment filing should be immutable once marked COMPLETED; mutable records could be manipulated without any audit trace.
**Recommendation:** Add a `job_audit_log` table that records every status transition (job_id, from_status, to_status, timestamp) as an append-only log, and add a guard in `update_job`/`update_status` that rejects mutations to COMPLETED records except via an explicit, logged override path.

---

### LC-14 · LOW · No LICENSE File
**Finding:** There is no `LICENSE` file in the repository root.
**Detail:** Without an explicit license, the code's IP status is ambiguous. If distributed to third parties (e.g., shared with an accountant), the legal terms are undefined.
**Recommendation:** Add a `LICENSE` file (e.g., MIT, proprietary, or "All Rights Reserved") appropriate to the project's distribution intent.

---

## Agent 2 — Tax Law Correctness

*Files reviewed: `main.py`, `models.py`, `services.py`, `prices.py`, `lots.py`, tax-spine source*

---

### TL-01 · HIGH · Dummy Valuation Output Indistinguishable From Real Output
**Finding:** Output from `valuation_mode="dummy"` (the default) produces RF-1159 JSON and HTML reports that appear identical to `price_table`-based output with no provenance marker.
**Detail:** `serialize_rf1159_export()` includes `skjema`, `inntektsaar`, and currency lines but no `valuation_mode` or `price_source` field. A user could inadvertently file dummy-valuation output with Skatteetaten. The dummy engine README comment ("Phase 1 stand-in") is not surfaced to the operator.
**Recommendation:** Inject a `valuation_mode` field into the RF-1159 JSON export and add a `⚠ DRAFT: Dummy valuation — not for filing` banner to the HTML report.

---

### TL-02 · HIGH · NOK Price Source Not Communicated at Job Output Level
**Finding:** Norges Bank is used as the NOK/USD source and is documented in `prices.py`, but the job output (RF-1159 JSON, HTML report) contains no indication of which price source was applied or the date of the rates used.
**Detail:** A tax auditor reviewing the output cannot verify the price source without inspecting the execution log. Skatteetaten may require price provenance to validate a filing.
**Recommendation:** Include `price_source`, `price_table_path`, and `price_table_date_range` fields in the RF-1159 JSON and execution summary.

---

### TL-03 · MEDIUM · RF-1159 Export Omits Income Section (Staking/Airdrop Income)
**Finding:** `Rf1159VirtualCurrencyLine` carries only `gevinst`, `tap`, and `formue`; there is no income (`inntekt`) row for staking rewards and airdrops.
**Detail:** Norway's RF-1159 form requires income declaration for staking rewards and airdrops under the "formue og inntekt" section. The current export serialisation omits these rows entirely. Filing the current output would underreport income.
**Recommendation:** Add `inntekt_nok: int` to `Rf1159VirtualCurrencyLine` and populate it from the Norway pipeline's income events; include it in `serialize_rf1159_export()`.

---

### TL-04 · MEDIUM · NOR_MULTI vs PER_FILE Can Produce Materially Different Tax Results Without Warning
**Finding:** The orchestrator allows switching between `nor_multi` (unified FIFO pool) and `per_file` (separate pools) without any comparison output or user warning that the choice affects cost basis and therefore tax liability.
**Detail:** Merging lots across sources in `nor_multi` changes cost basis allocation for assets held on multiple exchanges. A user who runs both modes will see different gain/loss figures but no reconciliation. README mentions the difference but does not call out the tax consequence.
**Recommendation:** Add a warning in the job output when `nor_multi` is selected, noting that results may differ materially from `per_file` and that the user should confirm which mode matches their legal obligation.

---

### TL-05 · MEDIUM · UK Tax Year Boundary Silent Mismatch
**Finding:** The orchestrator accepts `tax_year` as a plain integer for both Norway (calendar year) and UK (6 Apr – 5 Apr) without communicating the different year boundary to the user.
**Detail:** `models.py` accepts any `tax_year: int`. For `country=uk`, the pipeline internally applies `uk_tax_year_bounds(tax_year)` which returns April–April bounds. A user passing `tax_year=2025` for a UK job may not realise it covers 2025-04-06 to 2026-04-05.
**Recommendation:** Add a `tax_period_start` and `tax_period_end` field to the job output for UK jobs, and display this in the HTML report header.

---

### TL-06 · MEDIUM · Staking and Airdrop Tax Treatment Not Explicitly Validated
**Finding:** REWARD and AIRDROP events are aggregated into income summaries but no explicit check ensures they are not mis-classified as capital gains in either the Norway or UK pipeline.
**Detail:** `uk/pipeline.py` handles these as income events, but there is no canonical test asserting that a REWARD event never appears in a `s104_run()` disposal. Norwegian staking treatment (income at acquisition) is implied by the pipeline but not covered by a dedicated integration test or spec reference.
**Recommendation:** Add dedicated tests asserting REWARD/AIRDROP events produce income entries (not capital gains entries) in both Norway and UK pipelines; reference the relevant Skatteetaten/HMRC guidance in comments.

---

### TL-07 · MEDIUM · RLUSD Pricing Gap Not Warned at Job Execution Time
**Finding:** RLUSD is flagged as unsupported in `FetchPricesResponse` at fetch time, but once a price table is stored and a job starts, there is no runtime warning that RLUSD values will be zero/unresolved.
**Detail:** `services.py:148` validates CSV file existence but not completeness. A user with RLUSD transactions who uses `valuation_mode=price_table` will silently produce zero-value RLUSD lots with no flag in the job output or execution log.
**Recommendation:** Add a startup validation step that cross-references the assets present in `csv_files` against the assets covered in `csv_prices_path`, and surface any missing-asset warnings in the job output.

---

### TL-08 · LOW · Lot Carry-Forward Year Sequence Not Validated
**Finding:** The orchestrator does not validate that `tax_year` is monotonically increasing before loading carry-forward lots, allowing a 2025 job to run after a 2026 job and load stale lots.
**Detail:** `LotPersistenceStore.load_carry_forward(tax_year)` is keyed by year but the orchestrator does not check that the requested year is the next expected year. Running a prior year's job after a later year's would load an incorrect lot basis.
**Recommendation:** Add a guard in the service layer that warns (or fails) if `tax_year` is less than or equal to the most recently persisted year in the lot store.

---

### TL-09 · LOW · Dummy Engine Applies NOK Amounts to GBP Pipeline Without Currency Label
**Finding:** `DummyValuationEngine` returns NOK-denominated values for UK jobs without applying GBP/NOK conversion; all values are labelled GBP but denominated NOK.
**Detail:** The engine docstring at `dummy_engine.py:9` acknowledges this but the UK HTML report and job output carry no warning. A user who accidentally runs a UK job with `valuation_mode=dummy` (the default) will see GBP figures that are actually NOK.
**Recommendation:** Add a `⚠ Currency mismatch: dummy engine returns NOK values` warning to UK job output when `valuation_mode=dummy`.

---

### TL-10 · LOW · RLUSD and Missing-Asset Detection Happens Only at Fetch Time
**Finding:** If a user manually adds RLUSD rows to a CSV and then runs a job, the price table will silently produce zero/unresolved valuations with no per-job check.
**Detail:** `prices.py:188-196` flags RLUSD in `FetchPricesResponse` at fetch time only. No validation in `services.py` re-verifies coverage at job execution time.
**Recommendation:** Add an asset-coverage check at job start, producing a `WARNING: No price data for [RLUSD, ...]` entry in the execution log.

---

### TL-11 · HIGH · Non-GENERIC_EVENTS CSV Files Silently Dropped in Mixed-Workspace Jobs
**Finding:** When a job contains both XRPL accounts and non-generic CSV files (Coinbase, Firi, etc.), the non-generic CSVs are silently skipped — the acquisitions in those files are entirely excluded from FIFO calculation.
**Detail:** `services.py:431-441` in `_build_xrpl_command` logs a Python warning and skips any `CsvFileSpec` whose `source_type` is not `GENERIC_EVENTS`. No job failure is raised. The per-file path (Step 2 in `start_job_execution`) is gated on `not has_xrpl`, so non-generic CSVs also never receive a separate `taxspine-nor-report` invocation. A taxpayer with Coinbase purchases and XRPL sales would have the Coinbase acquisitions completely absent from the FIFO pool, producing wildly incorrect gain calculations.
**Recommendation:** Reject mixed-workspace jobs containing non-GENERIC_EVENTS CSVs with a clear `FAILED` error message, or run separate `taxspine-nor-report` invocations for those files and document that their lot pools are disjoint from the XRPL pool.

---

### TL-12 · HIGH · Float Arithmetic Used for NOK Price Computation
**Finding:** `prices.py` uses Python `float` for all NOK price calculations, violating the project's Decimal-only policy for tax-critical amounts.
**Detail:** `_fetch_kraken_usd_prices()` returns `dict[str, float]`, `_fetch_norges_bank_usd_nok()` returns `dict[str, float]`, and the final NOK price is computed as `usd_price * nok_rate` (both floats). The result is rounded with `f"{usd_price * nok_rate:.4f}"` but IEEE 754 double arithmetic introduces rounding errors in the intermediate multiplication. For high-value assets (BTC ~1,000,000 NOK), the absolute error can be several øre per unit, accumulating across all transactions in a tax year.
**Recommendation:** Convert both USD prices and FX rates to `Decimal` via `Decimal(str(value))` before multiplying, and write the result using `Decimal.quantize(Decimal("0.0001"))`.

---

### TL-13 · HIGH · Lot Carry-Forward Not Wired Into Any CLI Invocation
**Finding:** None of the three command builders pass carry-forward lot data to the taxspine CLIs, meaning every job starts with a completely clean FIFO state and prior-year unrealised lots are silently ignored.
**Detail:** `_build_xrpl_command`, `_build_csv_command`, and `_build_nor_multi_command` in `services.py` do not pass a `--lot-store` flag (the CLIs do not support it). The `LotPersistenceStore` is exposed only via the read-only `GET /lots` API. Without carry-forward, Year 2 tax calculations treat all Year 1 acquisitions as if they never happened: cost basis is zero for assets first acquired in Year 1 and sold in Year 2.
**Recommendation:** Either wire lot carry-forward through a GENERIC_EVENTS CSV pre-processing step (exporting carry-forward lots as synthetic acquisition events before the CLI run), or add `--lot-store` support to the relevant CLIs and re-wire the orchestrator to pass the configured `LOT_STORE_DB` path.

---

### TL-14 · MEDIUM · `--review-json` Not Passed to `taxspine-xrpl-nor`
**Finding:** `_build_xrpl_command` accepts a `review_json_path` parameter but never appends `--review-json` to the subprocess command, so XRPL pipeline unlinked-transfer warnings are silently lost.
**Detail:** `services.py:421-455`. The `review_dest` path is defined in `start_job_execution` (line 209) and checked for existence after the subprocess (line 253), but the flag is never added to the command list. Unlike the `--rf1159-json` gap (API-01), this affects the review/warning output rather than the primary tax output, but unlinked transfer warnings are critical for compliance.
**Recommendation:** Add `if review_json_path: cmd += ["--review-json", str(review_json_path)]` to `_build_xrpl_command`, if `taxspine-xrpl-nor` supports this flag.

---

### TL-15 · MEDIUM · Norges Bank FX Carry-Forward Misses First Days of Year
**Finding:** The FX rate fill-forward logic starts from January 1st with `last_rate = None`; if January 1st falls on a weekend, the first 1–2 days of the year have no NOK price entry, producing UNRESOLVED valuations for any transactions on those days.
**Detail:** `prices.py:319-335` (`_fill_calendar_gaps`). The function iterates from day 1 of the year and only writes a rate when `last_rate is not None`. If the first Norges Bank publication date is January 3rd (e.g., when January 1st–2nd are Saturday–Sunday), January 1st and 2nd are absent from the output CSV entirely. The taxspine CLI will have no price for those dates.
**Recommendation:** Seed `last_rate` from the last business day of the prior year (fetch one additional data point), or document that transactions in the first calendar days of a new year may require manual price entry.

---

### TL-16 · MEDIUM · RF-1159 Output Not Validated for Sign Correctness After CLI Completion
**Finding:** The orchestrator streams RF-1159 JSON directly to callers without validating that `gevinst`, `tap`, and `formue` are non-negative, allowing a malformed CLI output to be returned as a valid filing document.
**Detail:** `services.py:356-359, 303-305` record the RF-1159 path from CLI output without parsing the JSON. `GET /jobs/{id}/files/rf1159` streams the raw file. If the CLI produced a malformed document (negative `tap`, missing `inntektsaar`), it would be returned to the caller without any warning.
**Recommendation:** After each CLI completes successfully, parse the RF-1159 JSON and assert `gevinst >= 0`, `tap >= 0`, `formue >= 0` for every `virtuellValuta` entry; fail the job with a descriptive error if the assertion is violated.

---

### TL-17 · MEDIUM · `dry_run=True` Returns `COMPLETED` Status Indistinguishable From Real Output
**Finding:** A `dry_run=True` job completes with `status=COMPLETED` and no output files, making it indistinguishable programmatically from a real completed job with empty output.
**Detail:** `services.py:631-705`. Only `log_path` is populated in `JobOutput`; all other fields (rf1159, gains, wealth, report, review) are `None`. Any caller polling for `status == COMPLETED` and then checking for output files will find `None` with no indication that computation was intentionally skipped.
**Recommendation:** Set `status` to a distinct `DRY_RUN_COMPLETED` value, or populate `error_message` with `"[DRY RUN] No computation performed"` to prevent callers from treating dry-run results as authoritative tax output.

---

### TL-18 · LOW · Missing-Basis Lots Not Surfaced as Job-Level Alerts
**Finding:** After a job completes, lots with `basis_status="missing"` are only visible via a manual `GET /lots/{year}/portfolio` query; no alert is raised in `GET /alerts` for a job whose FIFO output contains missing-basis lots.
**Detail:** The existing alerts system (MEMORY.md `NorwayReviewSummary.has_unlinked_transfers`) does raise alerts for unlinked transfers. Missing basis is equally significant for tax correctness but is not wired into the alert path.
**Recommendation:** After job completion, check the lot store for any new `basis_status="missing"` lots introduced by this job and emit a `warn`-severity alert, consistent with the unlinked-transfer alerting pattern.

---

### TL-19 · LOW · No GBP Price-Fetch Endpoint for UK Jobs
**Finding:** The `POST /prices/fetch` endpoint produces only NOK price tables; there is no equivalent endpoint for GBP prices, so UK jobs always use dummy GBP valuation.
**Detail:** `prices.py` fetches Kraken close prices × Norges Bank USD/NOK only. UK jobs require GBP valuations (HMRC guidance). `MEMORY.md` notes that `StaticGbpValuationService` must be caller-supplied and "no real GBP price data bundled". UK jobs submitted through the orchestrator with `valuation_mode=price_table` will silently fall back to dummy valuation for crypto-to-crypto disposals.
**Recommendation:** Add a `GET /prices/fetch?country=uk&tax_year=...` path that fetches Kraken USD prices × Bank of England USD/GBP rates and writes a GBP price table, analogous to the NOK flow.

---

## Agent 3 — Security / Red Hat

*Files reviewed: `main.py`, `storage.py`, `services.py`, `config.py`, `models.py`, `dedup.py`, `ui/index.html`, `Dockerfile`, test suite*

---

### SEC-01 · MEDIUM · LIKE Wildcard Injection in SqliteJobStore.list()
**Finding:** The `query` parameter from `GET /jobs` is wrapped with `%` wildcards and passed to a LIKE clause without escaping `%` and `_` metacharacters, allowing overly broad result sets.
**Detail:** `storage.py:246`: the pattern `f"%{query}%"` is appended to params but `%` and `_` in the query string are not escaped. An attacker can use `query=_` to match all case names or `query=%` to retrieve all jobs regardless of name.
**Recommendation:** Escape LIKE metacharacters before wrapping: `query.replace('%', r'\%').replace('_', r'\_')` and add `ESCAPE '\'` to the SQL clause.

---

### SEC-02 · MEDIUM · Insufficient Path Traversal Sanitization in Dedup and Services
**Finding:** `_dedup_store_path()` and `_db_path()` sanitise source slugs by replacing `/` and `\` with `_` only — insufficient to prevent symlink traversal or Windows UNC paths.
**Detail:** `services.py:587`; `dedup.py:45`. The replacement does not handle `..` (after replacement of separators), absolute paths, or null bytes. `Path(settings.DEDUP_DIR) / f"{safe}.db"` could resolve outside `DEDUP_DIR` if the slug contains sequences like `....` that pass the replacement.
**Recommendation:** Use `re.sub(r'[^A-Za-z0-9_-]', '_', slug)` to allowlist only safe characters, then additionally assert the resolved path is under the expected directory.

---

### SEC-03 · MEDIUM · No Rate Limiting on Mutating Endpoints
**Finding:** `POST /jobs`, `/uploads/csv`, `/workspace/csv`, `/workspace/accounts`, and `/jobs/{id}/start` accept unlimited requests when `ORCHESTRATOR_KEY` is empty (default), enabling resource exhaustion.
**Detail:** An unauthenticated attacker (default dev config) can create unlimited jobs and upload unlimited files, consuming disk space and CPU until the host is exhausted. No per-IP or per-session throttle exists.
**Recommendation:** Add rate limiting via `slowapi` or nginx upstream, and document minimum rate limit requirements in deployment guidance.

---

### SEC-04 · MEDIUM · Dockerfile Build Secrets Logging
**Finding:** `TAXNOR_SHA` and `TAXNOR_TAG` build arguments are logged to build output, exposing the exact deployed version of the tax-nor package in CI/CD logs.
**Detail:** `Dockerfile:73`. While the GitHub token is correctly mounted via BuildKit secret and not baked into the image, version information in build logs could help an attacker identify known vulnerabilities in a pinned dependency version.
**Recommendation:** Suppress or redact version echoes in Dockerfile build steps, or move to image-level labels that are accessible only post-build.

---

### SEC-05 · LOW · CSV Price Table Path Not Validated for Containment
**Finding:** `csv_prices_path` is user-controlled and can reference any readable file on the filesystem; if error messages or logs contain file content, arbitrary file disclosure is possible.
**Detail:** `services.py:148-155`. The code intentionally does not constrain `csv_prices_path` to `UPLOAD_DIR`. An attacker who can POST `/workspace/run` with a crafted path (e.g., `/etc/passwd`) could leak file contents via error messages in the execution log.
**Recommendation:** Either constrain `csv_prices_path` to a designated prices directory, or add a warning in documentation that this field must not be user-supplied in multi-tenant deployments.

---

### SEC-06 · LOW · Subprocess Argument Injection via CSV File Paths
**Finding:** File paths from `csv_files` are passed directly to `subprocess.run()` argument lists without path normalization; flag injection is possible if an attacker controls the path string.
**Detail:** `services.py:433, 488, 492`. While `shell=False` prevents shell interpretation, a path like `--some-flag value` passed as a positional argument could inject a CLI flag into the taxspine command. List-based invocation passes the full path string as a single argument, which mitigates most risk, but unusual path characters warrant sanitization.
**Recommendation:** Validate that CSV paths are absolute, exist under an allowed directory, and contain no leading `--` or whitespace sequences.

---

### SEC-07 · LOW · GET /alerts Endpoint Unauthenticated
**Finding:** `GET /alerts` returns aggregated warnings from recent completed/failed jobs without requiring `X-Orchestrator-Key`.
**Detail:** `main.py:744`. While the endpoint does not return raw tax data, it leaks job metadata (case name fragments, warning types, job IDs) without authentication. An unauthenticated attacker can enumerate job activity.
**Recommendation:** Apply the same auth dependency as other read endpoints, or document that this is intentionally public and acceptable.

---

### SEC-08 · LOW · Default `ORCHESTRATOR_KEY=""` Disables All Authentication
**Finding:** The default configuration disables authentication for the entire API; no production safeguard prevents accidental open deployment.
**Detail:** `config.py:49`. The README documents this as intentional for local development, but there is no startup warning when running without a key. A misconfigured production deployment is fully open.
**Recommendation:** Log a `WARNING: ORCHESTRATOR_KEY is not set — API is unauthenticated` message at startup, and consider refusing to start in non-debug mode without a key.

---

### SEC-09 · LOW · CSV Upload Content-Type Validation Insufficient
**Finding:** File upload validation rejects `image/*` MIME types but accepts `application/octet-stream` without verifying the actual file format.
**Detail:** `main.py:526-531`. A file with a misleading MIME type and a `.csv` extension bypasses content validation. If the downstream taxspine CLI parses the file without strict validation, a crafted binary could cause unexpected behavior.
**Recommendation:** Add magic byte validation (first bytes of the file) to confirm CSV format before accepting the upload.

---

### SEC-10 · LOW · CORS Policy Not Production-Hardened
**Finding:** `CORS_ORIGINS` defaults to localhost only (safe), but there is no deployment documentation mandating that operators override this for public-facing instances.
**Detail:** `config.py:52`. A developer who deploys to a VPS without overriding `CORS_ORIGINS` will have CORS restricted to localhost, blocking the UI — but the API itself remains fully accessible from any origin to CORS-exempt clients (curl, mobile, server-side).
**Recommendation:** Document in the README that `CORS_ORIGINS` must be set to the actual UI hostname in production.

---

### SEC-11 · LOW · Dedup Source Slug Not Validated for Null Bytes
**Finding:** The dedup source slug from URL path parameters is sanitized only by replacing separators; no explicit null-byte stripping is applied before `Path()` operations.
**Detail:** `dedup.py:45`. FastAPI URL-decodes path parameters before routing, so `%00` becomes a null byte. Python's `Path()` will raise on null bytes on most platforms, resulting in a 500 error rather than graceful rejection.
**Recommendation:** Add `if '\x00' in source: raise HTTPException(400, ...)` before the sanitization step.

---

### SEC-12 · HIGH · All Sensitive Read Endpoints Permanently Unauthenticated Even When Key Is Set
**Finding:** `GET /jobs`, `GET /jobs/{id}`, `GET /dedup/*`, `GET /lots/*`, `GET /workspace`, and `GET /alerts` are permanently unauthenticated — any network peer who can reach port 8000 can read job records, XRPL account addresses, FIFO lot data, cost-basis figures, and dedup skip logs, regardless of whether `ORCHESTRATOR_KEY` is configured.
**Detail:** The auth design intentionally exempts all GET endpoints from `_require_key`. The default Docker compose binds to `0.0.0.0:8000`. On a Synology NAS exposed via Tailscale or Cloudflare Tunnel, any network peer — not just the owner — can read the full financial picture including account addresses and tax computations. SEC-07 identified this only for `/alerts`; the scope is the entire read surface.
**Recommendation:** Provide a `READ_KEY` setting (or extend `ORCHESTRATOR_KEY` to cover GET endpoints for all sensitive resources). At minimum, gate `GET /jobs`, `GET /lots`, `GET /workspace`, and file download endpoints on the same key requirement as mutating endpoints.

---

### SEC-13 · HIGH · `POST /jobs/{id}/attach-csv` Does Not Validate UPLOAD_DIR Containment
**Finding:** `attach_csv_to_job` checks only that `Path(spec.path).is_file()` — it does not assert the path is within `UPLOAD_DIR`, so an authenticated caller can attach `/etc/passwd` (or any readable file) as a CSV input, which is then forwarded as `--generic-events-csv /etc/passwd` to the tax CLI.
**Detail:** `main.py:575-604`. The `/workspace/csv` endpoint correctly enforces `relative_to(settings.UPLOAD_DIR)` (line 651), but `attach-csv` bypasses this check. The discrepancy means the containment invariant is silently broken for the non-workspace job flow.
**Recommendation:** Add the same containment assertion to `attach_csv_to_job`: `Path(spec.path).resolve().relative_to(Path(settings.UPLOAD_DIR).resolve())` and raise HTTP 400 if it fails.

---

### SEC-14 · HIGH · `POST /prices/fetch` Is Unauthenticated — Unguarded Outbound HTTP Trigger
**Finding:** `POST /prices/fetch` has no `dependencies=[Depends(_require_key)]` decorator; any unauthenticated caller on the local network can trigger repeated outbound HTTPS requests to Kraken and Norges Bank APIs.
**Detail:** `main.py`. Unlike `POST /jobs` which requires the key, `/prices/fetch` can be called without authentication even when `ORCHESTRATOR_KEY` is set. While the URLs are hardcoded (no SSRF), this is an unguarded side-effecting endpoint that creates denial-of-service (via API quota exhaustion) and unintended data-fetch risk in multi-user or NAS-exposed deployments.
**Recommendation:** Apply `dependencies=[Depends(_require_key)]` to `POST /prices/fetch`, matching the protection applied to other mutating endpoints.

---

### SEC-15 · MEDIUM · Execution Logs Expose Full CLI Commands Including Account Addresses — Unauthenticated
**Finding:** Execution logs (accessible via `GET /jobs/{id}/files/log` without authentication) contain full subprocess command lines including all flag values: `--account rXXX...`, `--generic-events-csv /absolute/path`, `--csv-prices /absolute/path`.
**Detail:** `services.py:225` logs the full command before execution. The file is stored at `OUTPUT_DIR/{job_id}/execution.log` and served via the unauthenticated file download endpoint. An adversary who cannot submit jobs but can reach port 8000 can read all execution logs, learning every XRPL account address, every file path, and every price table used across all jobs.
**Recommendation:** Either require authentication for file download endpoints, or redact sensitive flag values (account addresses, file paths) from the logged command line before writing.

---

### SEC-16 · MEDIUM · `/health` and `/alerts` Expose Internal Infrastructure Details Without Authentication
**Finding:** `/health` includes DB exception text and absolute directory paths in its response body; `/alerts` can echo full DB exception tracebacks; both endpoints are unauthenticated.
**Detail:** `main.py:153`: `checks["output_dir"] = f"error: {exc}"` exposes raw exception text including filesystem paths. Alert endpoint exception handlers may also propagate raw SQLite error messages. These reveal filesystem layout, absolute paths, and DB internals to any unauthenticated caller who can reach port 8000.
**Recommendation:** Return only opaque status codes and boolean flags from `/health` (e.g. `{"db": "ok", "output_dir": "error"}` without exception detail). Log exception detail server-side only.

---

### SEC-17 · MEDIUM · CLI Binary Names Are Fully Operator-Configurable — Arbitrary Binary Execution Risk
**Finding:** CLI binary names (`TAXSPINE_NOR_REPORT_CLI`, `TAXSPINE_XRPL_NOR_CLI`, etc.) are plain string environment variables used directly as the first element of `subprocess.run()` command lists; setting one to an arbitrary path enables execution of any binary on the host.
**Detail:** `config.py` and `services.py`. While this requires operator-level access to set environment variables, there is no allowlist check or `shutil.which` validation at startup — the binary names are accepted as-is. An operator who misconfigures these (or a compromised compose file) can cause the server to execute arbitrary binaries under the app's UID.
**Recommendation:** Validate CLI binary names at startup using `shutil.which()` and assert that resolved paths are under expected system directories (e.g. within `/usr/local/bin`). Log an error and refuse to start if a CLI binary is not found.

---

### SEC-18 · MEDIUM · Tailwind CSS Loaded From CDN Without Subresource Integrity Hash
**Finding:** `<script src="https://cdn.tailwindcss.com"></script>` has no `integrity=` attribute; if the CDN is compromised or the resource is served from a misconfigured cache, arbitrary JavaScript executes in the UI context.
**Detail:** `ui/index.html:7`. The UI handles financial data, displays XRPL account addresses, and submits jobs with API-key credentials. A CDN compromise would give an attacker full access to session state and the ability to exfiltrate data or issue authenticated API requests on behalf of the user.
**Recommendation:** Either pin the Tailwind CDN resource with a `integrity="sha384-..."` SRI attribute and `crossorigin="anonymous"`, or self-host the compiled Tailwind CSS as a static file.

---

### SEC-19 · LOW · `/dedup/sources` Exposes Absolute On-Disk Paths Without Authentication
**Finding:** `GET /dedup/sources` includes a `db_path` field containing the full absolute filesystem path of every dedup SQLite database in its response, with no authentication required.
**Detail:** `dedup.py:59-86`: `"db_path": str(db_file)` is returned in every source list entry. Combined with the unauthenticated-by-design read surface, any network peer learns the full filesystem layout of the container including exact directory names and `DEDUP_DIR` configuration — information that aids in planning path traversal or exfiltration attacks.
**Recommendation:** Omit `db_path` from the public response and return only the source name and record counts. If the path is needed for debugging, make it available only on an authenticated admin endpoint.

---

### SEC-20 · LOW · `subprocess.run` Called Without Timeout — Hung Tax CLI Blocks Forever
**Finding:** All three subprocess invocations in `services.py` call `subprocess.run()` without a `timeout=` argument; a hung tax CLI (network failure, infinite loop in parser, malicious CSV) blocks the calling thread indefinitely.
**Detail:** `services.py:227, 279, 331`. No `timeout=` is passed. Since `POST /workspace/run` calls `start_job_execution` synchronously (not offloaded), a single hung process holds the HTTP connection and an asyncio thread from the pool indefinitely. Multiple simultaneous hung subprocesses will exhaust the thread pool.
**Recommendation:** Add a configurable `SUBPROCESS_TIMEOUT_SECONDS` setting (e.g. default 300) and pass it as `timeout=settings.subprocess_timeout_seconds` to all `subprocess.run()` calls. Catch `subprocess.TimeoutExpired` and mark the job as FAILED with a descriptive error.

---

## Agent 4 — Backend / API Quality

*Files reviewed: `main.py`, `storage.py`, `services.py`, `models.py`, `config.py`, full test suite*

---

### API-01 · CRITICAL · `_build_xrpl_command` Never Emits `--rf1159-json` Flag
**Finding:** The XRPL command builder accepts an `rf1159_json_path` parameter but never adds `--rf1159-json` to the subprocess command, so XRPL-only and mixed XRPL+CSV jobs never produce RF-1159 exports.
**Detail:** `services.py:393-456`. The path is threaded through the function signature and the output dict maps `rf1159` to it, but the flag is never appended to the command list. The `_build_csv_command` and `_build_nor_multi_command` correctly emit `--rf1159-json`; only the XRPL builder is missing it. All XRPL job RF-1159 downloads will be empty or 404.
**Recommendation:** Add `if rf1159_json_path: cmd += ["--rf1159-json", str(rf1159_json_path)]` to `_build_xrpl_command`, matching the pattern in `_build_csv_command`.

---

### API-02 · HIGH · `WorkspaceStore._save_locked()` Not Atomic
**Finding:** `Path.write_text()` is not atomic; a crash mid-write leaves `workspace.json` corrupted and unreadable, crashing the app on next startup.
**Detail:** `storage.py:301`. On restart, `load_locked()` will raise `ValueError` (invalid JSON) and the workspace endpoint will fail. No backup or recovery path exists. The fix is the standard write-to-temp-then-rename pattern.
**Recommendation:** Replace `Path.write_text()` with `tmp.write_text(); tmp.rename(self._path)` using a sibling temp file to ensure atomicity on both POSIX and Windows.

---

### API-03 · MEDIUM · `run_workspace_report` Blocks the HTTP Worker Thread
**Finding:** `POST /workspace/run` calls `start_job_execution()` synchronously, blocking the entire HTTP worker for the full duration of CLI execution (potentially many seconds).
**Detail:** `main.py:732`. Unlike `POST /jobs/{id}/start` which uses `asyncio.create_task(asyncio.to_thread(...))` for a 202 async response, `run_workspace_report` does not offload to a thread. Under uvicorn with default settings, this blocks all concurrent requests.
**Recommendation:** Wrap the execution call in `await asyncio.to_thread(...)` or refactor to return 202 immediately with polling (same pattern as `start_job`).

---

### API-04 · MEDIUM · Race Condition in `start_job` — Concurrent Requests Can Spawn Duplicate Execution
**Finding:** Two concurrent `POST /jobs/{id}/start` requests can both pass the `status == PENDING` check before either updates the store to RUNNING, spawning two parallel execution threads.
**Detail:** `main.py:211-230`. The check at line 223 and the `create_task` at line 229 are not atomic with respect to the store. The store update to RUNNING happens inside the async task after both requests have already returned 202.
**Recommendation:** Perform a compare-and-swap update (set RUNNING only if currently PENDING) as a single atomic DB operation before spawning the task, and reject the second request with 409 if the CAS fails.

---

### API-05 · MEDIUM · `cancel_job` Status Can Be Overwritten by Background Thread
**Finding:** `POST /jobs/{id}/cancel` sets status to FAILED immediately, but a concurrently running execution thread can overwrite it with COMPLETED if the subprocess finishes after the cancel.
**Detail:** `main.py:251`. The docstring at lines 237-241 acknowledges this limitation. There is no lock coordination between the cancel endpoint and the execution thread's final `update_status` call.
**Recommendation:** Add a `cancelled` terminal state distinct from `failed`, and have the execution thread check for this state before overwriting.

---

### API-06 · MEDIUM · `GET /jobs?limit=500` Returns 422 Instead of Capping Gracefully
**Finding:** FastAPI's `le=200` constraint on the `limit` query parameter returns a 422 validation error when the UI requests `limit=500`, rather than silently capping at 200.
**Detail:** `main.py:191`. The UI currently requests `limit=500` (`index.html:794`). In production this means every page load fails with 422 until the UI is corrected. Ideally the API caps and continues.
**Recommendation:** Either change the API to `min(limit, 200)` clamping, or fix the UI to request `limit=200`. The current state causes a silent page-load failure.

---

### API-07 · MEDIUM · `cancel_job` and `start_job` Both Use Stored Status Without Locking
**Finding:** The cancel and start endpoints read job status, make a decision, and then write a new status in three non-atomic steps.
**Detail:** Combined observation from `main.py:211-230` (start) and `main.py:237-253` (cancel). The threading lock in `SqliteJobStore` protects individual DB operations but not the read-check-write sequence as a whole.
**Recommendation:** Implement a `transition_status(job_id, from_status, to_status)` method in `SqliteJobStore` that performs the CAS in a single locked transaction.

---

### API-08 · LOW · `SqliteJobStore._connect()` Does Not Enable WAL Mode
**Finding:** SQLite connections are opened without `PRAGMA journal_mode=WAL`, risking `SQLITE_BUSY` errors under concurrent load.
**Detail:** `storage.py:128-129`. The store uses `threading.Lock()` for mutual exclusion, but WAL mode allows concurrent reads alongside writes, improving throughput and reducing lock contention in async contexts.
**Recommendation:** Add `conn.execute("PRAGMA journal_mode=WAL")` immediately after `sqlite3.connect()`.

---

### API-09 · LOW · `attach_csv_to_job` Validates File Existence at Attach Time, Not Execution Time
**Finding:** CSV files are checked for existence when attached (`main.py:589`), but the job may not execute for hours; the file could be deleted before execution starts.
**Detail:** The check provides early user feedback (good) but the gap between attachment and execution means the check is non-authoritative. The service layer already checks at execution time (`services.py:178`), making the attachment-time check redundant for safety but the error messaging confusing.
**Recommendation:** Keep the attachment-time check for UX feedback, but clarify in the error message at execution time that the file was present at submission but is now missing.

---

### API-10 · LOW · `get_job_review` Silently Swallows Unreadable File Errors
**Finding:** `OSError` and `ValueError` on review file reads are silently caught and skipped; the caller sees 404 "not found" instead of a meaningful error when files exist but are corrupt.
**Detail:** `main.py:455-463`. If a review JSON was written but is corrupted (partial write, disk error), the endpoint behaves identically to "never generated", hiding the underlying problem.
**Recommendation:** Distinguish between "no review output path recorded" (true 404) and "file exists but is unreadable" (return 500 with a descriptive message).

---

### API-11 · INFO · `delete_job` Does Not Remove Output Files — Unbounded Disk Growth
**Finding:** `DELETE /jobs/{id}` removes only the DB record; the output directory `OUTPUT_DIR/{job_id}` persists indefinitely.
**Detail:** `main.py:255-271`. The README documents this explicitly. Over time, especially with test jobs and re-runs, orphaned directories accumulate on disk.
**Recommendation:** Either auto-delete output on record deletion (with a `keep_files` option) or provide an admin cleanup script and document the disk growth risk.

---

### API-12 · MISSING TEST · No Concurrent Double-Start Test
**Finding:** There is no test covering two simultaneous `POST /jobs/{id}/start` requests to verify that only one execution thread is spawned.
**Recommendation:** Add a test using `concurrent.futures.ThreadPoolExecutor` to fire two start requests simultaneously and assert `mock_run.call_count == 1`.

---

### API-13 · MISSING TEST · No Cancel-Then-Complete Race Test
**Finding:** There is no test simulating: (1) start job, (2) immediately cancel, (3) background thread completes, (4) verify final status is the cancelled state (not COMPLETED).
**Recommendation:** Add a test that starts a job, posts cancel, then confirms the background thread's COMPLETED write does not overwrite the cancelled state.

---

### API-14 · MISSING TEST · No `limit > 200` Pagination Boundary Test
**Finding:** No test verifies the behaviour of `GET /jobs?limit=500` — whether it returns 422 or caps at 200.
**Recommendation:** Add a parameterised test asserting `limit=201` returns either 422 (current behaviour) or a capped 200-item response (desired behaviour).

---

### API-15 · HIGH · `POST /jobs` Returns HTTP 200 Instead of 201
**Finding:** `POST /jobs` (resource creation) returns HTTP 200; RFC 7231 mandates 201 Created for successful resource creation. The incorrect code is also baked into the test assertions.
**Detail:** The `@app.post("/jobs", ...)` decorator has no explicit `status_code`, so FastAPI defaults to 200. `test_jobs_api.py` asserts `resp.status_code == 200`, cementing the wrong code in CI. Generated clients and API consumers that rely on status-code semantics will misinterpret a successful creation as a no-op response.
**Recommendation:** Add `status_code=201` to the `@app.post("/jobs", ...)` decorator and update the test assertion to `assert resp.status_code == 201`.

---

### API-16 · HIGH · `update_status`/`update_job` Lock Not Held Across Full Read-Modify-Write Cycle
**Finding:** `SqliteJobStore.update_status` and `update_job` acquire the lock separately for the `get()` call and again inside `_upsert()`, leaving a window where another thread can write between the two acquisitions.
**Detail:** `storage.py`. The pattern is: `get(job_id)` [acquires + releases lock] → mutate in Python → `_upsert(job)` [acquires + releases lock]. A concurrent cancel request and a background worker completion can interleave in this gap, causing a lost update (e.g. FAILED status from cancel is overwritten by COMPLETED from the worker). This is a distinct race from API-04 (concurrent start requests) — this one affects any two concurrent writes to the same job.
**Recommendation:** Refactor both methods to acquire the lock once around the full get-mutate-upsert sequence as a single critical section.

---

### API-17 · MEDIUM · `asyncio.create_task` Result Not Retained — Task Can Be Garbage-Collected
**Finding:** The background task created by `asyncio.create_task(asyncio.to_thread(...))` in `start_job` is not stored anywhere; CPython's GC can collect it before completion, silently killing the job execution.
**Detail:** `main.py:229`. The Python docs explicitly warn: "Tasks can be garbage collected if there's no reference kept to them." Under memory pressure or a busy event loop, the task reference can be dropped, terminating the background execution without any error, leaving the job stuck in RUNNING.
**Recommendation:** Store created tasks in a module-level `set`: `_background_tasks: set[asyncio.Task] = set()`. Add the task to the set on creation and remove it in a `task.add_done_callback(lambda t: _background_tasks.discard(t))` callback.

---

### API-18 · MEDIUM · `tax_year` Has No Range Validation
**Finding:** `tax_year` in `JobInput` and `WorkspaceRunRequest` accepts any integer — values like `0`, `-1`, or `9999` are silently forwarded to CLI subprocesses.
**Detail:** `models.py`. No `Field(ge=..., le=...)` constraint is applied. A CLI receiving `--year 0` will either fail with a cryptic subprocess error or silently produce incorrect output. The first XRPL-era year (2012) and the current year + 1 are reasonable bounds.
**Recommendation:** Add `Field(ge=2009, le=datetime.date.today().year + 1)` to `tax_year` in both `JobInput` and `WorkspaceRunRequest`.

---

### API-19 · MEDIUM · `GET /jobs` Returns No Total Count — Pagination Is Incomplete
**Finding:** `GET /jobs` returns a plain `list[Job]` with no wrapping envelope; callers cannot determine how many total pages exist without issuing another request.
**Detail:** `main.py:183-198`. Callers receiving N items cannot determine whether more exist beyond the current page without sending `offset + N` and observing a shorter list. This is a standard omission in paginated APIs.
**Recommendation:** Return a response envelope: `{"items": [...], "total": int, "limit": int, "offset": int}` and declare a corresponding Pydantic response model.

---

### API-20 · MEDIUM · Blocking File I/O in Async Route Handlers (`GET /alerts`, `GET /jobs/{id}/review`)
**Finding:** `GET /alerts` and `GET /jobs/{id}/review` perform synchronous `Path.read_text()` calls inside `async def` route handlers, blocking the event loop.
**Detail:** `main.py`. Both endpoints iterate over review JSON files and call `Path.read_text()` synchronously. This blocks the uvicorn event loop for the duration of each file read, starving other concurrent requests. Under normal load the files are small, but a large number of jobs or slow filesystem can cause measurable latency.
**Recommendation:** Wrap all file reads in `await asyncio.to_thread(path.read_text, encoding="utf-8")` to keep the event loop non-blocking.

---

### API-21 · LOW · `JobOutput` Dual Singular/List Path Fields Can Diverge
**Finding:** `JobOutput` maintains both singular path fields (`report_html_path`, `rf1159_json_path`) and corresponding list fields (`report_html_paths`, `rf1159_json_paths`) as independent fields; inconsistent assignment can cause callers to see different values from the two representations.
**Detail:** `models.py`. The singular fields are documented as backward-compatible aliases for the first list element, but they are independently settable. If the service layer sets the list but not the singular (or vice versa), callers relying on either field will get incorrect results.
**Recommendation:** Make the singular fields `@property` computed aliases: `@property def report_html_path(self) -> Optional[str]: return self.report_html_paths[0] if self.report_html_paths else None`.

---

### API-22 · LOW · Several Routes Missing `response_model` Declarations
**Finding:** `DELETE /jobs/{id}`, `POST /jobs/{id}/cancel`, `GET /jobs/{id}/files`, and `GET /jobs/{id}/review` all return untyped dicts with no `response_model`, making their response shapes invisible in the OpenAPI schema.
**Detail:** `main.py`. FastAPI generates no schema for these endpoints' responses, meaning API clients cannot generate correct typed code, and the shapes are not validated before sending.
**Recommendation:** Create dedicated Pydantic models for each response shape (`DeletedJobResponse`, `CancelledJobResponse`, etc.) and declare `response_model` on each route.

---

### API-23 · MISSING TEST · No Dedicated Tests for `/jobs/{id}/reports` Endpoints
**Finding:** `GET /jobs/{id}/reports` (list) and `GET /jobs/{id}/reports/{index}` (download) have no test file covering empty lists, out-of-bounds index, ordering, or correct filename in response.
**Recommendation:** Add `test_reports_endpoints.py` covering: empty report list, correct list order, index 0 download, out-of-range index 404, and path-containment rejection.

---

## Agent 5 — Frontend / JavaScript

*Files reviewed: `ui/index.html` (entire file, ~1300+ lines)*

---

### FE-01 · CRITICAL · Overlay Stuck Visible on Secondary Async Failures in `runReport()`
**Finding:** In `runReport()`, the loading overlay is removed only in the success branch of the `try` block; if `loadJobs()`, `openResults()`, or `loadAlerts()` throw after the job succeeds, the overlay remains visible permanently.
**Detail:** `index.html:765` removes the overlay inside the `try` block before awaiting the three follow-up calls. If any of those calls throw an uncaught exception, execution falls to `catch` at line 776 — but only if the `/workspace/run` call itself threw. The three subsequent failures are not caught.
**Recommendation:** Wrap the post-success calls in their own `try/finally` block that guarantees overlay removal and button re-enablement regardless of secondary failures.

---

### FE-02 · HIGH · `fetchPrices()` Injects Server-Side Path Into User-Editable Input
**Finding:** `fetchPrices()` places a server-side absolute filesystem path (`data.path`) directly into the `run-prices-path` input field without validation; this path is later submitted to `runReport()` and passed to CLI commands.
**Detail:** `index.html:708`. If the server returns a path with shell-like characters (e.g. spaces, quotes), these are placed verbatim into the form and submitted to the backend. Depending on backend handling, this could enable CLI flag injection. The path is also meaningless to remote users who cannot access the server filesystem.
**Recommendation:** Validate that `data.path` matches an expected pattern before populating the input, and add explanatory UI text indicating this is a server-side path.

---

### FE-03 · MEDIUM · Drop Zone Inside `<label>` Causes Double File Dialog
**Finding:** The CSV drop zone is nested inside a `<label>` that wraps the file input; the label's default behaviour plus the explicit `fileInput.click()` in the drop zone click handler triggers two file picker dialogs in sequence.
**Detail:** `index.html:154-159` (label wrapping input) and `index.html:624` (drop zone click handler). On click, the label fires the input natively and then the handler also calls `fileInput.click()`.
**Recommendation:** Either remove the explicit `click()` call from the handler (relying on the label) or move the drop zone outside the label element.

---

### FE-04 · MEDIUM · Inconsistent Escaping in Job Action Handlers
**Finding:** `cancelJob()` and `deleteJob()` URL-encode job IDs via `encodeURIComponent()` for API calls, but the same IDs are embedded in onclick attributes using `escHtml()` — different escaping contexts applied inconsistently.
**Detail:** `index.html:813, 817, 821-822` use `escHtml(j.id)` in attribute strings; `index.html:1129, 1157, 1178` use `encodeURIComponent(jobId)` in fetch calls. The approach is correct for each context but the dual-escaping in the onclick attribute string (`escHtml` for HTML context, then `encodeURIComponent` needed inside the JS string) is not applied.
**Recommendation:** Replace inline `onclick` attribute strings with `data-job-id` attributes and attach event listeners in JS, eliminating the dual-escaping problem entirely.

---

### FE-05 · LOW · `loadJobs()` Requests `limit=500` But API Caps at 200
**Finding:** `loadJobs()` sends `limit=500` but the API enforces `le=200`, returning a 422 validation error on every page load.
**Detail:** `index.html:794`. This means the job list silently fails to load in production (the API returns 422, the catch block ignores it, the list appears empty).
**Recommendation:** Change the request to `limit=200` to match the API's documented maximum.

---

### FE-06 · INFO · `badgeHtml()` Calls `String(status).toUpperCase()` on Server Data
**Finding:** `badgeHtml()` uses `String(status).toUpperCase()` where `status` comes from the server; if the server ever returns an unexpected status string, it is rendered unsanitized as badge label text.
**Detail:** `index.html:917`. The risk is minimal if the server only ever returns known enum values, but the function has no allowlist guard.
**Recommendation:** Add a fallback: `const label = ['pending','running','completed','failed'].includes(status) ? status.toUpperCase() : 'UNKNOWN'`.

---

### FE-07 · HIGH · Background `setInterval` Handles Are Discarded — Cannot Be Cleared
**Finding:** The three background refresh timers (`loadJobs` every 30s, `checkHealth` every 15s, `loadAlerts` every 60s) are started in the init IIFE with no reference retained, so they can never be cleared.
**Detail:** `index.html:1479-1481`. `setInterval(loadJobs, 30_000)` etc. are called but the return handles are not stored. There is no lifecycle API to stop these timers. If the page is ever mounted into a shell or test harness that re-runs the init code, duplicate timers accumulate indefinitely.
**Recommendation:** Store all three handles alongside `_pollTimer`: `const _jobsTimer = setInterval(loadJobs, 30_000)` etc., so they can be stopped symmetrically and tested.

---

### FE-08 · HIGH · `a.severity` Interpolated Into CSS Class Without Sanitization — XSS Vector
**Finding:** The alert severity value from the server is interpolated directly into a CSS `class` attribute inside `innerHTML` without `escHtml`, creating an XSS injection path.
**Detail:** `index.html:1233`. `` const sevClass = `sev-${a.severity}`; `` then `` `<div class="alert-item ${sevClass}">` `` at line 1241. If the backend returns a severity containing `"` or a space followed by `onclick=`, the attribute boundary is broken and arbitrary HTML is injected. While currently backend-controlled, it is server-supplied data flowing into `innerHTML` unsanitized.
**Recommendation:** Either apply `escHtml(a.severity)` when building `sevClass`, or validate against an allowlist: `const sevClass = ['error','warn','info'].includes(a.severity) ? \`sev-${a.severity}\` : 'sev-info'`.

---

### FE-09 · HIGH · Timestamp Strings Inserted Into `innerHTML` Without `escHtml`
**Finding:** Job creation/update timestamps, formatted via `toLocaleString()`, are inserted into `innerHTML` without escaping — some locale-specific date formats can contain `<`, `>`, or `"` characters.
**Detail:** `index.html:831`. `<span>${created}</span>` where `created = new Date(j.created_at).toLocaleString()`. Certain locale implementations (e.g. `ja-JP` or locales using angle-bracket quotations) may produce strings containing `<` characters. These would be interpreted as HTML tags when inserted raw into `innerHTML`.
**Recommendation:** Wrap all `toLocaleString()` results in `escHtml()` before innerHTML insertion: `escHtml(new Date(j.created_at).toLocaleString())`.

---

### FE-10 · MEDIUM · `loadAlerts()` Skips `r.ok` Check — Server Errors Shown as "All Clear"
**Finding:** `loadAlerts()` calls `.json()` on the fetch response without checking `r.ok`; a server 500 error returns a JSON error body, causing the alert list to silently display "All clear — no active alerts" when the backend is actually broken.
**Detail:** `index.html:1219`. The pattern `const alerts = await (await fetch(...)).json()` does not check `r.ok`. If the backend returns 500 with a JSON body, `Array.isArray(alerts)` will be false and the "no active alerts" empty state is displayed — masking a real server failure. The `catch` block only fires on network-level failures.
**Recommendation:** Check `r.ok` before `.json()`: `const r = await fetch(...); if (!r.ok) throw new Error(r.status); const alerts = await r.json();`.

---

### FE-11 · MEDIUM · `openResultsById` and `loadReviewBadge` Swallow All Errors Silently
**Finding:** Both functions have empty `catch {}` blocks; if a job detail fetch fails, the user gets no feedback — the results panel stays empty or stale with no error message.
**Detail:** `index.html:922, 1258`. `openResultsById` silently does nothing on 404 or network error, leaving the user clicking a job row with no response. `loadReviewBadge` silently hides the review section. For primary user actions, silent failure is unacceptable.
**Recommendation:** In `openResultsById`, display a brief error in the results panel on failure. In `loadReviewBadge`, distinguish an expected 404 (non-Norway job) from a genuine network error and surface the latter.

---

### FE-12 · MEDIUM · Form Fields Not Reset After Successful `runReport()` Submission
**Finding:** After a successful run, the case name input, dry-run checkbox, debug checkbox, and price path input retain their values — the user may inadvertently submit the same options on the next run.
**Detail:** `index.html:773-774`. No field reset occurs after `await loadJobs()` and `openResults(job)`. The dry-run checkbox is particularly risky: a user who ran a test dry-run and then clicks Run again will submit another dry-run without noticing, because the checkbox state persists.
**Recommendation:** After a successful submission, uncheck the dry-run checkbox and optionally clear the case name field. If case name retention is preferred, explicitly uncheck dry-run only.

---

### FE-13 · MEDIUM · `API` Constant Assumes Same-Origin Root — Breaks Behind Reverse Proxy
**Finding:** `const API = (location.protocol === 'file:') ? 'http://localhost:8000' : ''` assumes the backend is always at the same origin and root path; any reverse-proxy deployment with a path prefix (e.g. `/taxspine/`) breaks all API calls silently.
**Detail:** `index.html:439`. If the orchestrator is served behind nginx at `/taxspine/api`, all fetch calls resolve to `/jobs` instead of `/taxspine/api/jobs` and silently 404. There is no runtime mechanism to inject the base URL.
**Recommendation:** Add a `<meta name="api-base" content="">` tag that the server renders with the correct base URL, and read it in JS: `const API = document.querySelector('meta[name="api-base"]')?.content || ''`.

---

### FE-14 · LOW · Blob URLs in `loadReportInIframe` Never Revoked — Memory Leak
**Finding:** Each call to `loadReportInIframe` creates a new `URL.createObjectURL(blob)` and assigns it to `iframe.src` without revoking the previous URL, leaking the full report HTML in memory.
**Detail:** `index.html:1082`. `URL.createObjectURL` holds a reference to the blob data until `URL.revokeObjectURL` is called or the page is unloaded. Opening multiple reports in a long session accumulates unreleased memory proportional to the total size of all reports viewed.
**Recommendation:** Store the previous URL before overwriting: `const old = iframe.src; iframe.src = newUrl; if (old.startsWith('blob:')) URL.revokeObjectURL(old);`.

---

### FE-15 · LOW · Advisory Divs Prepended Without Removing Prior — Duplicate Banners Accumulate
**Finding:** Each call to `loadReportInIframe` on a provisional report inserts a new advisory `<div>` above the iframe without checking for or removing an existing one, causing banners to stack on repeated opens.
**Detail:** `index.html:1084-1089`. If a user clicks the same provisional job multiple times (or the poll callback reopens it), a new advisory div is prepended each time. The results panel accumulates multiple identical "DRAFT" warning banners.
**Recommendation:** Assign the advisory a stable id (`advisory-banner`) and check `document.getElementById('advisory-banner')?.remove()` before inserting a new one.

---

### FE-16 · LOW · `_filteredJobs()` Doesn't Search the Fallback Display Label
**Finding:** The search filter only matches `j.input?.case_name` but the job row shows `j.input.country + ' ' + j.input.tax_year` when `case_name` is absent — a user searching "norway 2025" will find no results for unnamed jobs.
**Detail:** `index.html`. `_filteredJobs` checks `j.input?.case_name?.toLowerCase()?.includes(q)`. An unnamed Norway 2025 job displays "norway 2025" in the row but has an empty `case_name`, so the search silently fails to match its visible label.
**Recommendation:** In `_filteredJobs`, also search the fallback label: `const label = (j.input?.case_name || \`${j.input?.country} ${j.input?.tax_year}\`).toLowerCase(); return label.includes(q);`.

---

## Agent 6 — UI/UX

*Files reviewed: `ui/index.html` (entire file)*

---

### UX-01 · CRITICAL · Embedded Report Iframe Can Navigate Parent Window (Same-Origin Blob)
**Finding:** HTML report content is loaded via a same-origin blob URL into an `<iframe sandbox="allow-scripts">`; a malicious or compromised report can call `window.parent.location = 'http://attacker.com'` to redirect the dashboard.
**Detail:** `index.html:1090` creates a blob URL (same-origin by design). `sandbox="allow-scripts"` enables script execution. A report generated from attacker-controlled CSV data could embed a redirect. `allow-top-navigation` is not explicitly blocked by the sandbox string.
**Recommendation:** Add `sandbox="allow-scripts allow-same-origin"` and explicitly add `Content-Security-Policy` headers on the blob, or use `allow-popups-to-escape-sandbox` blocking; specifically test that `window.parent.location` assignment is blocked from within the iframe.

---

### UX-02 · MEDIUM · Alert Severity Emoji Icons Lack Accessible Labels
**Finding:** Alert severity icons (🔴 ⚠️ ℹ️) are rendered as raw emoji without `aria-label` attributes; screen readers will announce the emoji name or skip it, losing severity semantics.
**Detail:** `index.html:1213, 1242`. The severity level is communicated visually via emoji and background colour only, with no text alternative for non-visual users.
**Recommendation:** Wrap each emoji in `<span aria-label="critical" role="img">🔴</span>` or replace with SVG icons that include a title.

---

### UX-03 · MEDIUM · Dummy Valuation Warning Not Announced to Screen Readers
**Finding:** The "⚠ Not for filing — dummy valuation" warning (`role="alert"`) lacks `aria-live="polite"` and `aria-atomic="true"`, so screen readers do not announce it when valuation mode is changed dynamically.
**Detail:** `index.html:199`. When users toggle the valuation mode dropdown, the warning appears/disappears but is not announced to assistive technology users who rely on live region updates.
**Recommendation:** Add `aria-live="polite" aria-atomic="true"` to the warning element.

---

### UX-04 · MEDIUM · Price Table Path Field Shows Server-Side Absolute Path Without Context
**Finding:** After `fetchPrices()` succeeds, the run form auto-fills with an absolute server-side path (e.g. `/data/prices_2025.csv`) that is meaningless and non-editable for remote users.
**Detail:** `index.html:708`. A user running the UI from a different machine cannot browse to the server path; the field appears to show a local path but it is server-side. No explanatory label clarifies this.
**Recommendation:** Add helper text below the field: "Server-side path — auto-filled by Fetch Prices. Do not modify unless running locally."

---

### UX-05 · MEDIUM · Status Badges Use Decorative Symbols Without Screen-Reader Alternatives
**Finding:** Job status badges render ●, ▶, ✓, ✗ symbols inline; these are announced verbatim by screen readers ("bullet", "play", "check mark", "cross mark") alongside the status text, creating redundant or confusing output.
**Detail:** `index.html:910-919`. The symbols add visual polish but are purely decorative for accessibility purposes.
**Recommendation:** Add `aria-hidden="true"` to the symbol `<span>` so only the status text label is announced.

---

### UX-06 · LOW · Review Badge Emoji Lack Accessible Labels
**Finding:** Review badge status indicators (✅ 🔗 ⚠️) lack `aria-label` attributes; screen reader users cannot distinguish "review clean" from "unlinked transfers".
**Detail:** `index.html:1270-1277`.
**Recommendation:** Add `aria-label` to each review badge emoji span (e.g., `aria-label="Review clean"`, `aria-label="Unlinked transfers detected"`).

---

### UX-07 · LOW · Job Row Uses `div[role="button"]` Instead of Semantic `<button>`
**Finding:** Job rows are `<div>` elements with `role="button" tabindex="0"`, limiting native button semantics (disabled state, pressed state) and requiring manual keydown handling.
**Detail:** `index.html:820`. Native `<button>` elements provide keyboard activation (`Enter`/`Space`) natively without an explicit `keydown` listener.
**Recommendation:** Refactor job rows to use `<button>` or `<a>` elements to get built-in keyboard interaction and screen-reader semantics.

---

### UX-08 · LOW · Empty State Guide Disappears After First Job Is Created
**Finding:** The getting-started guide (step-by-step instructions) is shown only when no jobs exist; once any job is created — even if all are later deleted — the guide never reappears.
**Detail:** `index.html:290-298`. Returning users who have deleted all jobs see a blank job list with no guidance.
**Recommendation:** Show the empty state guide whenever the job list is genuinely empty, not just on first load.

---

### UX-09 · LOW · Cancel Button Has Insufficient Touch Target Size
**Finding:** The cancel button on running jobs uses `padding:3px 8px; font-size:11px`, producing an approximately 20px tall touch target — below the 44px minimum recommended by WCAG 2.5.5.
**Detail:** `index.html:812-813`. Mobile users and users with motor impairments will struggle to tap this button accurately.
**Recommendation:** Increase button padding to at least `padding:8px 12px` and font-size to `13px` minimum.

---

### UX-10 · LOW · Colour Alone Differentiates Alert Severity
**Finding:** Alert boxes use only background colour (red, yellow, blue) to communicate severity; users with colour-blindness cannot distinguish error from warning states.
**Detail:** `index.html:49-51`. No additional pattern, border style, or icon differentiates severity for colour-blind users.
**Recommendation:** Add a severity label ("ERROR", "WARNING", "INFO") as visible text in addition to colour coding.

---

### UX-11 · LOW · Tax Center Table Headers Missing `scope="col"` Attribute
**Finding:** `<th>` elements in the Tax Center result tables lack `scope="col"` attributes, reducing the ability of screen readers to associate headers with data cells.
**Detail:** `index.html:370-378, 392-399, 411-415`.
**Recommendation:** Add `scope="col"` to all `<th>` elements in result tables.

---

### UX-12 · LOW · CSV Source-Type Dropdown Shows Technical Labels
**Finding:** The CSV source type dropdown displays raw internal identifiers (`generic_events`, `coinbase_csv`, `firi_csv`) rather than user-friendly names.
**Detail:** `index.html:148-151`. Non-technical users (accountants, end users) will not recognise these as exchange names.
**Recommendation:** Use display labels in the dropdown options: "Generic Taxspine Events", "Coinbase CSV", "Firi CSV", etc.

---

### UX-13 · LOW · No Animated Loading Indicator During Async Operations
**Finding:** CSV upload and Tax Center refresh show status text changes but no spinner; users on slow connections may not perceive that an action is in progress.
**Detail:** `index.html:632, 1310-1318`.
**Recommendation:** Add a CSS `animate-spin` spinner or pulsing indicator alongside status text during upload and refresh operations.

---

### UX-14 · LOW · Destructive Confirmations Use Native `confirm()` Dialog
**Finding:** Delete job and cancel job confirmations use the browser's native `confirm()` popup, which is visually inconsistent with the dashboard's dark theme and cannot be styled.
**Detail:** `index.html:1154-1155`. The native dialog is jarring and cannot communicate the consequence of the action with rich formatting.
**Recommendation:** Replace `confirm()` with a styled in-page modal dialog that clearly states the destructive action and its consequences.

---

### UX-15 · LOW · Pipeline Mode Selector Not Explained in Context
**Finding:** The `per_file` vs `nor_multi` pipeline mode selector appears in the Run Report form without an explanation of the tax consequence of choosing each mode.
**Detail:** `index.html`. The dropdown labels "Per file — one report per source" and "NOR MULTI — unified FIFO pool, single report" are technically accurate but do not communicate that the choice affects cost basis calculation and therefore tax liability.
**Recommendation:** Add a tooltip or help text block: "NOR MULTI merges all CSV sources into one FIFO lot pool. This can change your cost basis and gain/loss figures vs. per-file mode. Use the same mode every year for consistency."

---

### UX-16 · HIGH · CSV and Account Removal Fire Immediately Without Confirmation
**Finding:** `removeCsv()` and `removeAccount()` send DELETE requests immediately on click with no confirmation dialog, inconsistent with `deleteJob()` which already uses `confirm()`.
**Detail:** `index.html`. The "×" remove button on each CSV entry and XRPL account tag fires instantly. There is no undo. The `deleteJob()` function already establishes the confirm-before-delete pattern — the workspace removal handlers simply don't follow it.
**Recommendation:** Add `if (!confirm('Remove this item from the workspace?')) return;` to both `removeAccount()` and `removeCsv()`, matching the existing job-deletion pattern.

---

### UX-17 · HIGH · XRPL Address Input Has No Inline Format Validation
**Finding:** The XRPL address input provides no client-side format check; a user who pastes an Ethereum address receives no feedback until a browser `alert()` fires after the server rejects it.
**Detail:** `index.html`. XRPL addresses always start with "r" and are 25–34 alphanumeric characters. The placeholder "r… (XRPL address)" is the only guidance. The late `alert()` error is jarring and gives no inline correction hint.
**Recommendation:** Add blur-triggered inline validation: check the "r" prefix and 25–34 character length before sending the request, and display a red helper message beneath the input field.

---

### UX-18 · MEDIUM · Run Overlay Has No Escape Path — UI Locks on Hung Request
**Finding:** The full-screen run overlay (`#run-overlay`) has no cancel button and no timeout; if the server becomes unresponsive, the UI is completely locked until the user force-refreshes the browser tab.
**Detail:** `index.html`. The overlay blocks all interaction and is removed only inside the `try/catch` of `runReport()`. A hung network request leaves the overlay open indefinitely with no progress indicator or elapsed timer.
**Recommendation:** Add an `AbortController` signal with a timeout (e.g. 120 seconds) to the `fetch()` call, and add a visible "Dismiss" button on the overlay that aborts the request and re-enables the form.

---

### UX-19 · MEDIUM · Download Buttons Share Identical Styling — RF-1159 Indistinguishable From Debug Log
**Finding:** All output file download buttons use identical `btn-secondary` styling; the tax-filing document (RF-1159) is visually indistinguishable from the execution log or raw gains CSV.
**Detail:** `index.html`. The `results-downloads` container renders all output kinds as identical secondary buttons differentiated only by an emoji prefix. A user wanting the primary filing document cannot prioritise it visually.
**Recommendation:** Style the primary artifacts (HTML report, RF-1159 JSON) with `btn-primary` and display the file format (HTML, JSON, CSV) as a badge next to each button label.

---

### UX-20 · MEDIUM · Tax Center Tab Bar Has No ARIA Tab Roles
**Finding:** The three Tax Center tabs use custom `<button>` elements with a toggled `active` CSS class but have no `role="tablist"`, `role="tab"`, or `aria-selected` — screen readers cannot identify the tabbed relationship.
**Detail:** `index.html`. Without proper ARIA roles, assistive technology will announce three unrelated buttons with no indication that activating one panel hides the others.
**Recommendation:** Apply `role="tablist"` to the container, `role="tab"` + `aria-selected` to each button, and `role="tabpanel"` + `aria-labelledby` to each content panel.

---

### UX-21 · MEDIUM · "Ingestion Sources" Tab Uses Internal Pipeline Vocabulary
**Finding:** The "🔁 Ingestion Sources" tab displays raw dedup database metadata with column headers and empty-state text that use the internal term "ingestion" — opaque to non-developer tax users.
**Detail:** `index.html`. The empty-state message reads "Sources appear here after the first ingestion run." Column headers and the raw UTC timestamps add no user-meaningful context.
**Recommendation:** Rename to "Data Sources" or "Import Log", add a one-sentence description, and replace UTC timestamps with relative times (e.g. "2 days ago").

---

### UX-22 · LOW · Debug Valuation Checkbox Has No Explainer Text
**Finding:** The "Debug valuation output" checkbox has no description, unlike the "Dry run" checkbox which has an inline `<p>` explanation.
**Detail:** `index.html`. A user cannot determine whether this affects output files, the HTML report, or only the execution log.
**Recommendation:** Add: "Prints a valuation diagnostics block to the execution log. Does not affect report content." — matching the dry-run explainer pattern.

---

### UX-23 · LOW · Report Iframe Fixed at 600px With No Overflow Affordance
**Finding:** Tall HTML reports scroll inside a fixed 600px iframe with no visual indication that more content exists below the visible fold.
**Detail:** `index.html`. There is no fade gradient, scroll indicator, or hint directing users to the "Open ↗" button for full-page viewing.
**Recommendation:** Add a fade gradient at the iframe bottom and a hint: "Scroll for full report or click Open ↗ for full-page view."

---

### UX-24 · INFO · Report Advisory Text References Skatteetaten for UK Jobs
**Finding:** The provisional-report advisory reads "Do not file with Skatteetaten until all warnings are resolved" — but Skatteetaten is the Norwegian authority; UK users see jurisdictionally incorrect guidance.
**Detail:** `index.html`. The advisory text is hardcoded and not conditioned on `job.input.country`.
**Recommendation:** Use a jurisdiction-neutral phrase: "Do not file this report until all warnings are resolved and you have obtained professional tax advice."

---

## Agent 7 — Infrastructure & Operations

*Files reviewed: `Dockerfile`, `Dockerfile.local`, `docker-compose.synology.yml`, `docker.yml`, `config.py`, `storage.py`, `main.py`, `pyproject.toml`, `requirements.lock`*

---

### INFRA-01 · HIGH · `blockchain-reader` Installed From Unpinned `main` Branch
**Finding:** The Dockerfile installs `blockchain-reader` from the GitHub `main` branch without a version pin or commit hash, creating a supply-chain risk.
**Detail:** `Dockerfile:92, 94`. If the upstream `main` branch is compromised or changes API compatibility, the next production build silently picks up the change. This is a high-severity supply chain risk for a financial application.
**Recommendation:** Pin to a specific commit hash: `git+https://github.com/...@<COMMIT_SHA>#egg=blockchain-reader` and document the upgrade process.

---

### INFRA-02 · HIGH · SQLite Without WAL Mode — Concurrent Access Risk
**Finding:** `SqliteJobStore._connect()` does not set `PRAGMA journal_mode=WAL`, risking `SQLITE_BUSY` errors and potential data corruption under concurrent access.
**Detail:** `storage.py:128-129`. Even with `threading.Lock()` at the application level, SQLite's default rollback journal mode can cause issues with multiple async tasks accessing the same connection. WAL mode enables concurrent reads with writes.
**Recommendation:** Add `conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA synchronous=NORMAL")` immediately after every `sqlite3.connect()` call.

---

### INFRA-03 · MEDIUM · Docker Base Image Not Pinned to Digest
**Finding:** `FROM python:3.11.9-slim` uses a mutable tag; rebuilds can pull a different binary for the same tag if the upstream image is updated.
**Detail:** `Dockerfile:24`. The comment at lines 19-23 acknowledges this but only recommends manual steps. For a financial application, reproducible builds are critical.
**Recommendation:** Pin to `FROM python:3.11.9-slim@sha256:<digest>` and update the digest deliberately as part of a security review process.

---

### INFRA-04 · MEDIUM · `requirements.lock` Bypassed in Docker Build
**Finding:** The Dockerfile uses `pip install .` (from `pyproject.toml`) instead of `pip install -r requirements.lock`, allowing dependency drift between the lock file and the production image.
**Detail:** `Dockerfile:45`. `requirements.lock` explicitly states "Do NOT install this file directly in Docker" but provides no alternative pinning mechanism, leaving transitive dependencies unpinned in the built image.
**Recommendation:** Adopt `pip-tools`, `uv`, or Poetry to generate a lock file that is used both locally and in Docker, ensuring reproducible dependency resolution.

---

### INFRA-05 · MEDIUM · `workspace.json` Written Non-Atomically
**Finding:** `storage.py:301` uses `Path.write_text()` which is not atomic; a crash mid-write corrupts `workspace.json` and prevents server startup.
**Detail:** This duplicates API-02 from a deployment perspective: on a Synology NAS with limited power protection, an unclean shutdown mid-write is a realistic failure scenario.
**Recommendation:** Use atomic write pattern: write to `workspace.json.tmp` then `rename()` to final path.

---

### INFRA-06 · MEDIUM · No Disk Quota or Automatic Cleanup
**Finding:** All data directories (`PRICES_DIR`, `OUTPUT_DIR`, `UPLOAD_DIR`, `TEMP_DIR`, `DEDUP_DIR`) accumulate files indefinitely with no cleanup mechanism or disk quota enforcement.
**Detail:** `config.py`. On a Synology NAS with limited storage, this will eventually fill the disk. The only removal mechanism is `DELETE /jobs/{id}`, which does not remove files.
**Recommendation:** Add a configurable TTL-based cleanup job (e.g., `DELETE /admin/cleanup?older_than_days=90`) and document storage growth expectations.

---

### INFRA-07 · MEDIUM · Container Runs as Root
**Finding:** There is no `USER` directive in the Dockerfile; the process runs as root (UID 0) in production.
**Detail:** `Dockerfile`. Files created under `/data` will be root-owned, creating access control issues on a shared Synology NAS. A container escape would give an attacker root on the host.
**Recommendation:** Add `RUN useradd -r -u 1000 app && chown -R app:app /data /tmp/taxspine` and `USER app` before the `CMD` directive.

---

### INFRA-08 · MEDIUM · Health Check Returns HTTP 200 Even When Service Is Degraded
**Finding:** `/health` always returns HTTP 200 regardless of CLI availability or writable output directory; container orchestrators will not detect a degraded service.
**Detail:** `main.py:164-170`. Kubernetes/Docker readiness probes check only the HTTP status code. A pod with missing CLIs or a full disk will be considered "ready" and receive traffic.
**Recommendation:** Return HTTP 503 when `db != "ok"` or `output_dir != "ok"`, or add a separate `/ready` endpoint that returns 503 on degraded state while `/health` returns 200 for liveness.

---

### INFRA-09 · MEDIUM · `Dockerfile.local` Depends on `build-local.ps1` Without Validation
**Finding:** `Dockerfile.local` requires a `vendor/` directory populated by a PowerShell prerequisite script; there is no build-time check that this directory exists.
**Detail:** `Dockerfile.local:8-13`. Linux/macOS developers and CI pipelines that skip the PS1 script will get a confusing build failure without a clear error message.
**Recommendation:** Add a `RUN test -d vendor || (echo "Run build-local.ps1 first" && exit 1)` guard in the Dockerfile, or document this as a hard requirement in the README.

---

### INFRA-10 · LOW · `git` and `build-essential` Not Removed From Production Image
**Finding:** `git` and `build-essential` (including `gcc`, `make`) are installed for the pip GitHub install step but not removed, increasing image size and attack surface.
**Detail:** `Dockerfile:36-37`. These packages are not needed at runtime and their presence in a financial application container is unnecessary risk.
**Recommendation:** Use a multi-stage build: install dependencies in a builder stage, then copy only the installed packages to a clean `python:3.11.9-slim` runtime stage.

---

### INFRA-11 · LOW · Synology Watchtower Auto-Restart With 5-Minute Poll
**Finding:** `docker-compose.synology.yml` configures Watchtower to auto-update the production container every 5 minutes and auto-restart it if a new image is available.
**Detail:** `docker-compose.synology.yml:126-142`. If a broken image is pushed, production auto-updates to the broken version within 5 minutes with no rollback mechanism.
**Recommendation:** Increase poll interval to at least 1 hour, disable auto-restart for production (require manual approval), and add a post-update health check before Watchtower completes the update.

---

### INFRA-12 · LOW · `TAXNOR_SHA` Defaults to `"unknown"` on Token Failure
**Finding:** The Dockerfile build fetches the `tax-nor` HEAD SHA for cache-busting, but defaults to `"unknown"` if the GitHub token is absent; subsequent builds reuse the cache even when `tax-nor` has new commits.
**Detail:** `Dockerfile:71`. Local builds will always use a cached `tax-nor` unless the default is overridden, silently running stale tax code.
**Recommendation:** Log a `WARNING: TAXNOR_SHA=unknown — build may use cached tax-nor` message and consider failing the build in CI when the SHA cannot be resolved.

---

### INFRA-13 · LOW · CORS Origins Default Not Documented as Production Override
**Finding:** `CORS_ORIGINS` defaults to localhost; deployment documentation does not explicitly require operators to override this for production.
**Detail:** `config.py:52`. Operators who deploy to a Synology with a custom domain will find the UI blocked by CORS without a clear configuration error.
**Recommendation:** Add `CORS_ORIGINS=https://your-dashboard-url` as a required production configuration item in the README deployment section.

---

### INFRA-14 · LOW · JSON Log Driver Not Centralized — Logs Lost on Restart
**Finding:** Docker logging uses the `json-file` driver with `max-size=10m, max-file=3` (total ~30MB per container); logs are lost on container removal and there is no centralized logging.
**Detail:** `docker-compose.synology.yml`. A production financial application should maintain logs for audit purposes beyond container lifetime.
**Recommendation:** Export logs to a persistent volume mount or use a syslog/Loki driver to capture and retain logs independently of container lifecycle.

---

### INFRA-15 · LOW · CI Pipeline Runs Tests Only — No Container Build or Smoke Test
**Finding:** A `docker.yml` GitHub Actions workflow exists and runs `pytest`, but it never builds the Docker image and has no container-level smoke test (e.g., `docker run --rm` health probe).
**Detail:** A broken `Dockerfile`, bad environment variable wiring, or missing runtime dependency would pass all Python unit tests while breaking the production container. There is no automated signal that the built image actually starts.
**Recommendation:** Add a CI step: `docker build -t taxspine-test .` followed by `docker run --rm --env-file .env.ci taxspine-test python -c "from taxspine_orchestrator.main import app"` (or a `/health` curl).

---

### INFRA-16 · HIGH · Production Compose Uses Floating `:latest` Image Tag
**Finding:** `docker-compose.synology.yml` references the orchestrator image as `:latest`; combined with Watchtower's 5-minute auto-deploy, any push to the registry deploys immediately to production with no gating.
**Detail:** If the registry receives a broken or malicious `:latest` image (typo tag, supply-chain push), Watchtower will auto-deploy it within 5 minutes. There is no staging gate, canary, or rollback step.
**Recommendation:** Pin `docker-compose.synology.yml` to a specific image digest (e.g., `taxspine-orchestrator@sha256:abc123`) and require an explicit update step in the deployment runbook.

---

### INFRA-17 · HIGH · `ORCHESTRATOR_KEY` Defaults to Empty — No Startup Guard or `.env.example`
**Finding:** If `ORCHESTRATOR_KEY` is not set, the orchestrator starts silently with no authentication — all API endpoints are public. There is no `.env.example` file and no startup-time warning.
**Detail:** `config.py`. A misconfigured deployment (missing environment variable) would silently expose the full API, including job submission and file upload, to the local network or internet depending on Synology port-forwarding.
**Recommendation:** Add a startup check: `if not settings.orchestrator_key: raise RuntimeError("ORCHESTRATOR_KEY must be set in production")` (or at minimum log a `WARNING: running without authentication`). Add a `.env.example` file documenting all required variables.

---

### INFRA-18 · MEDIUM · No CPU/Memory Resource Limits in Compose Files
**Finding:** Neither `docker-compose.yml` nor `docker-compose.synology.yml` sets `deploy.resources.limits` for CPU or memory; a runaway job can consume all NAS resources.
**Detail:** On a Synology NAS with constrained RAM, a large XRPL sync or multi-source pipeline run with no memory limit can OOM-kill DSM services or cause the NAS to become unresponsive.
**Recommendation:** Add resource constraints, e.g. `mem_limit: 1g`, `cpus: "1.5"` in both compose files, tuned to available NAS hardware.

---

### INFRA-19 · MEDIUM · No Documented Backup Strategy for SQLite Databases
**Finding:** The system relies on three SQLite databases (`jobs.db`, `skip_log.db`, `dedup.db`) bound-mounted to `/data` with no documented backup procedure, retention policy, or point-in-time recovery.
**Detail:** For a tax spine handling financial data, loss of `jobs.db` means loss of all job history and provenance records. There is no `VACUUM`, `BACKUP` command, or bind-mount snapshot cadence documented.
**Recommendation:** Document a daily backup schedule (e.g., `sqlite3 /data/jobs.db ".backup /backup/jobs-$(date +%Y%m%d).db"` via cron), a retention window, and test a restore at least once. Consider Synology Hyper Backup for the `/data` volume.

---

### INFRA-20 · MEDIUM · CI Workflow Has No Lint or Type-Check Step
**Finding:** The `docker.yml` CI workflow runs `pytest` but does not run `ruff`, `mypy`, or `flake8`; type errors and style regressions are not caught in CI.
**Detail:** The tax spine codebase uses type annotations throughout; a `mypy --strict` or `pyright` pass would catch API surface drift and missing nullability guards before merge.
**Recommendation:** Add `ruff check .` and `mypy src/` (or `pyright`) as CI steps before `pytest` so they gate the test run.

---

### INFRA-21 · MEDIUM · Application Logging Is Unstructured Plain Text
**Finding:** All application logging uses Python's default `logging` formatter, emitting unstructured plain-text lines with no JSON formatter, correlation IDs, or job-ID context fields.
**Detail:** For a financial system, log lines must be queryable by job ID and timestamp for audit and incident response. Plain-text logs cannot be efficiently filtered by Synology's Log Center or a SIEM tool.
**Recommendation:** Add a `structlog` or `python-json-logger` formatter that includes `job_id`, `source`, and `level` as structured fields. Route logs through Docker's `json-file` driver (already configured) so DSM Log Center can ingest them.

---

### INFRA-22 · LOW · `Dockerfile.local` Uses Floating `python:3.11-slim` Tag
**Finding:** `Dockerfile.local` uses `FROM python:3.11-slim` (a floating minor-version tag) rather than the pinned `python:3.11.9-slim` used in the production `Dockerfile`.
**Detail:** `Dockerfile.local:1`. If `3.11-slim` resolves to a different patch release than the production image, local builds silently diverge from production in stdlib behaviour and security patches.
**Recommendation:** Change `Dockerfile.local` to use `FROM python:3.11.9-slim` to match the production base image.

---

### INFRA-23 · LOW · Watchtower Mounts Docker Socket — Root-Equivalent Container Control
**Finding:** `docker-compose.synology.yml` mounts `/var/run/docker.sock` into the Watchtower container, giving it full control over all containers on the host.
**Detail:** If the Watchtower container is compromised (e.g., via a malicious image it pulls), the attacker gains root-equivalent host access via the Docker socket. This is a known Docker privilege-escalation vector.
**Recommendation:** Restrict the Docker socket with a proxy (e.g., `tecnativa/docker-socket-proxy`) that allows only the `GET /containers/*/json` and `POST /containers/*/restart` methods needed by Watchtower.

---

### INFRA-24 · LOW · `start.ps1` Uses `--reload` and Binds to `0.0.0.0` Without Dev-Only Warning
**Finding:** `start.ps1` launches the server with `uvicorn --reload --host 0.0.0.0`, making it network-accessible on all interfaces in development without any warning or guard.
**Detail:** If `start.ps1` is accidentally run on a machine connected to a corporate or home network, the development server with live reload is exposed without authentication enforcement (if `ORCHESTRATOR_KEY` is also unset).
**Recommendation:** Add a prominent comment and an env-var guard: `if ($env:TAXSPINE_ENV -ne "development") { Write-Error "start.ps1 is for local development only"; exit 1 }`.

---

### INFRA-25 · LOW · `requires-python = ">=3.11"` Not Validated Against 3.12/3.13 in CI
**Finding:** `pyproject.toml` declares `requires-python = ">=3.11"`, but the CI matrix only tests Python 3.11; behaviour on 3.12 and 3.13 (which ship with deprecation removals) is untested.
**Detail:** Python 3.12 removed `distutils`, `imp`, and several asyncio internals. Python 3.13 removes additional deprecated APIs. If the broad `>=3.11` bound is advertised, CI should verify it.
**Recommendation:** Add a CI matrix entry for Python 3.12 (and 3.13 when stable). If the intent is to support only 3.11, narrow the bound to `>=3.11,<3.12`.

---

## Total Finding Count

| Severity | Count |
|----------|------:|
| 🔴 CRITICAL | 3 |
| 🟠 HIGH | 26 |
| 🟡 MEDIUM | 56 |
| 🔵 LOW | 45 |
| ⚪ INFO | 4 |
| 🧪 MISSING TEST | 4 |
| **GRAND TOTAL** | **139** |

### Top Priority Actions (CRITICAL + HIGH)

| ID | Severity | Agent | One-Line Fix |
|----|----------|-------|-------------|
| API-01 | 🔴 CRITICAL | Backend | Add `--rf1159-json` flag to `_build_xrpl_command` |
| FE-01 | 🔴 CRITICAL | Frontend | Wrap post-success calls in `runReport()` in `try/finally` |
| UX-01 | 🔴 CRITICAL | UI/UX | Add `allow-same-origin` + CSP to iframe sandbox |
| LC-01 | 🟠 HIGH | Legal | Implement data retention policy and erasure endpoint |
| LC-02 | 🟠 HIGH | Legal | Encrypt workspace.json or document OS-level encryption requirement |
| LC-03 | 🟠 HIGH | Legal | Auto-delete files on job record deletion |
| LC-04 | 🟠 HIGH | Legal | Add field-level erasure for account addresses in job records |
| LC-05 | 🟠 HIGH | Legal | Redact addresses from execution logs |
| TL-01 | 🟠 HIGH | Tax Law | Inject provenance marker into RF-1159 and HTML output |
| TL-02 | 🟠 HIGH | Tax Law | Add price source metadata to job output |
| TL-11 | 🟠 HIGH | Tax Law | Reject or re-route non-GENERIC_EVENTS CSVs in mixed XRPL+CSV jobs |
| TL-12 | 🟠 HIGH | Tax Law | Replace float arithmetic with Decimal in `prices.py` NOK computation |
| TL-13 | 🟠 HIGH | Tax Law | Wire lot carry-forward into CLI invocations (synthetic events or --lot-store flag) |
| API-02 | 🟠 HIGH | Backend | Atomic write for workspace.json (tmp + rename) |
| API-15 | 🟠 HIGH | Backend | Change `POST /jobs` status_code to 201 and update test assertions |
| API-16 | 🟠 HIGH | Backend | Hold lock across full get-mutate-upsert in update_status/update_job |
| API-17 | 🟠 HIGH | Backend | Store asyncio.create_task result in a set to prevent GC |
| FE-02 | 🟠 HIGH | Frontend | Validate server path before injecting into form input |
| FE-07 | 🟠 HIGH | Frontend | Store all setInterval handles in variables so they can be cleared |
| FE-08 | 🟠 HIGH | Frontend | Allowlist `a.severity` before using in CSS class attribute in innerHTML |
| FE-09 | 🟠 HIGH | Frontend | Wrap `toLocaleString()` timestamp results in `escHtml()` |
| UX-16 | 🟠 HIGH | UI/UX | Add confirm() to removeCsv() and removeAccount() matching deleteJob() pattern |
| UX-17 | 🟠 HIGH | UI/UX | Add inline XRPL address format validation on blur |
| SEC-12 | 🟠 HIGH | Security | Gate all read endpoints (jobs, lots, workspace, dedup) on `_require_key` |
| SEC-13 | 🟠 HIGH | Security | Add `UPLOAD_DIR` containment check to `attach_csv_to_job` |
| SEC-14 | 🟠 HIGH | Security | Apply `_require_key` dependency to `POST /prices/fetch` |
| INFRA-01 | 🟠 HIGH | Infra | Pin blockchain-reader to a commit hash |
| INFRA-02 | 🟠 HIGH | Infra | Enable WAL mode in all SQLite connections |
| INFRA-16 | 🟠 HIGH | Infra | Pin Synology compose to image digest; remove floating `:latest` + Watchtower auto-deploy |
| INFRA-17 | 🟠 HIGH | Infra | Add startup guard for empty `ORCHESTRATOR_KEY`; add `.env.example` |
