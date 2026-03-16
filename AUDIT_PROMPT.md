# Multi-Agent Audit Prompt

Paste the entire content below this heading into Claude Code (starting from the horizontal rule).
It will launch 7 parallel specialist agents and consolidate their findings into `AUDIT_REPORT.md`.

---

You are orchestrating a full-spectrum audit of the **taxspine-orchestrator** repository.

Repository root (absolute path — use this in all tool calls):
```
C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator
```

Launch all 7 agents simultaneously in a single message using 7 parallel Agent tool calls.
Each agent has a sharply bounded scope — do NOT let agents stray outside their domain.

Each agent must:
- Read the relevant source files for their domain using the Read tool
- Produce a **numbered list of findings**
- Rate each finding: **CRITICAL / HIGH / MEDIUM / LOW / INFO**
- Keep each finding to one paragraph maximum
- Include the exact file path and line number (where relevant)

After all agents complete, consolidate every finding into `AUDIT_REPORT.md` (create or overwrite)
at the repository root with:
- One section per agent (use the agent name as the heading)
- Findings sorted by severity within each section (CRITICAL first, INFO last)
- A **SUMMARY** section at the very top counting findings by severity across all agents
- Total finding count at the end of the SUMMARY

---

## Agent 1 — Legal & Compliance

You are a Legal & Compliance specialist. Audit this repository for data-protection and regulatory compliance issues.

**Scope (ONLY these topics):**
- GDPR / CCPA / Norwegian Personopplysningsloven compliance
- Whether the system processes personal data and on what legal basis
- PII exposure in logs, error messages, stored files, or API responses
- Data retention policies — are job records, uploaded CSVs, and output files ever deleted?
- Right to erasure — is there any mechanism to remove a user's data?
- Third-party data sharing — data sent to Kraken API and Norges Bank API: what is sent, is it PII?
- Privacy notice / data processing agreement — is one referenced anywhere?
- Whether XRPL account addresses constitute personal data under Norwegian law

**Do NOT cover:** security exploits, tax law correctness, code quality, UI design.

**Files to read:**
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\main.py` — all endpoints that store, retrieve, or expose user data
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\storage.py` — WorkspaceStore, SqliteJobStore (what is persisted and for how long)
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\services.py` — what ends up in execution logs (stdout/stderr from CLI subprocesses)
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\prices.py` — what is sent to Kraken and Norges Bank APIs
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\config.py` — data directory layout
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\README.md` — any privacy notices or data-handling documentation

**Questions to answer:**
1. Does the system collect, store, or process personal data? If so, what legal basis is documented?
2. Are XRPL account addresses (public keys) personal data under GDPR/Personopplysningsloven?
3. What PII could end up in execution logs (`execution.log`) captured from CLI stderr/stdout?
4. Are uploaded CSV files (which may contain trade history with timestamps and amounts) ever automatically deleted?
5. Is there any DELETE endpoint for job records that also removes associated files on disk?
6. What data is sent outbound to Kraken and Norges Bank — could it be linked to an individual?
7. Is there a data retention schedule, privacy notice, or DPA reference anywhere in the codebase or docs?

**Output format:**
Produce a numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 2 — Tax Law Correctness

You are a Tax Law specialist. Audit this repository for correctness against Norwegian Skatteetaten rules and UK HMRC rules.

**Scope (ONLY these topics):**
- Norwegian crypto tax rules: FIFO lot matching, cost basis in NOK, formue (wealth tax), RF-1159 Altinn filing
- UK HMRC CGT rules: Section 104 pooling, 30-day bed-and-breakfast rule, annual exempt amount
- Whether the exchange rate source (Norges Bank official rates) is legally correct for Norway
- Tax year boundary handling: does the Norway pipeline use calendar year (Jan–Dec) vs UK (Apr–Apr)?
- Staking and airdrop treatment under Norwegian and UK rules
- Whether "dummy valuation" output could be mistaken for a real filing
- RF-1159 schema completeness: are all required fields produced?
- Whether carry-forward lot persistence correctly handles year boundaries
- Whether the RLUSD gap (acknowledged in `prices.py`) creates a compliance risk

**Do NOT cover:** code bugs unrelated to tax accuracy, security, UI design.

**Files to read:**
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\prices.py` — exchange rate source and supported assets
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\services.py` — how CLI arguments are assembled (FIFO flags, year, valuation flags)
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\models.py` — ValuationMode enum, PipelineMode, Country
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\main.py` — the dummy-valuation warning at POST /workspace/run and RF-1159 file kind
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\lots.py` — lot carry-forward API (what is exposed and to which years)
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\README.md`

**Questions to answer:**
1. Is Norges Bank the legally correct source for USD/NOK rates per Skatteetaten guidance?
2. Does the "dummy valuation" mode produce output that could be inadvertently filed?
3. Is the RLUSD pricing gap (Kraken cannot supply it) documented to users at the point of running a report?
4. Does the NOR_MULTI pipeline mode (unified FIFO pool) produce a different tax result than PER_FILE mode, and is this difference explained to users?
5. Are staking rewards and airdrops classified and taxed correctly (income at receipt vs capital gain on disposal)?
6. Does the system enforce that UK jobs use the correct tax year boundary (6 Apr – 5 Apr)?
7. Are all RF-1159 required fields populated in the JSON output, or are any silently omitted?

**Output format:**
Produce a numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 3 — Security / Red Hat

You are a Security specialist (Red Hat perspective). Audit this repository for exploitable vulnerabilities.

**Scope (ONLY these topics):**
- Authentication bypass: when is `ORCHESTRATOR_KEY` enforced vs skipped?
- Path traversal in file serving (GET /jobs/{id}/files/{kind} and GET /jobs/{id}/reports/{index})
- SQL injection in `SqliteJobStore.list()` — the `case_name LIKE ?` query and f-string SQL construction
- Command injection: are CSV file paths or XRPL account addresses interpolated unsanitised into subprocess calls?
- SSRF via `csv_prices_path`: can an attacker supply a network path or UNC path to cause the server to read arbitrary files?
- Insecure deserialization: `Job.model_validate_json()` deserialises untrusted DB content
- The `source` URL parameter in `/dedup/{source}/summary` and `/dedup/{source}/entries` — is it sanitised before constructing a filesystem path?
- Secrets in code: hardcoded tokens, default credentials, or secrets in Dockerfile build args
- CORS policy in `config.py` — is the default too permissive for a production deployment?
- Upload validation: only filename suffix and content-type checked — what about file content?
- Rate limiting: no rate limiting on any endpoint
- The `GET /alerts` endpoint is unauthenticated and returns internal job details

**Do NOT cover:** tax accuracy, UI design, legal compliance.

**Files to read:**
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\main.py` — auth dependency, file-serve path containment check, all endpoint decorators
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\storage.py` — SqliteJobStore.list() SQL construction (lines 236–258)
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\services.py` — _build_xrpl_command(), _build_csv_command(), _build_nor_multi_command(), _dedup_store_path()
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\dedup.py` — _db_path() function, source parameter handling
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\config.py` — ORCHESTRATOR_KEY default, CORS_ORIGINS default
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\Dockerfile` — build args, secret handling, GH_READ_TOKEN
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\tests\test_security.py` — what is already tested

**Questions to answer:**
1. In `SqliteJobStore.list()`, is the WHERE clause constructed safely? Look at lines 236–258 of `storage.py` — the f-string `f"SELECT data FROM jobs {where} ..."` — is `where` safe?
2. In `_dedup_store_path()` in `services.py` and `_db_path()` in `dedup.py`, does replacing `/` and `\` with `_` fully prevent path traversal (e.g. `..` sequences, null bytes, absolute paths)?
3. In `_build_xrpl_command()` and `_build_csv_command()`, are the XRPL account address and CSV file path passed to `subprocess.run` as a list (safe) or interpolated into a shell string (unsafe)?
4. Can `csv_prices_path` in `JobInput` be a UNC path (`\\server\share\file`) or a URL that causes the server to read from a network location?
5. Does the `GET /alerts` endpoint leak job details (case names, warning strings from review JSON) to unauthenticated callers?
6. Does `ORCHESTRATOR_KEY = ""` (the documented default) mean the entire API is unauthenticated in all default deployments?
7. Is the Dockerfile BuildKit secret handling (`--mount=type=secret,id=gh_token`) sufficient to prevent token leakage into image layers or `docker history`?

**Output format:**
Produce a numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 4 — Backend / API

You are a Backend and API specialist. Audit this repository for correctness, robustness, and completeness of the server-side code.

**Scope (ONLY these topics):**
- FastAPI route correctness: HTTP status codes, response models, missing validation
- Race conditions: `start_job` uses `asyncio.create_task(asyncio.to_thread(...))` — can the same job be started twice before the status transitions to RUNNING?
- The `cancel_job` endpoint documents that a RUNNING job's background thread can still overwrite the FAILED status — is this a correctness problem?
- `SqliteJobStore` threading: `threading.Lock()` used, but a new connection is opened per operation — is WAL mode enabled?
- Job deletion does not remove output files on disk — can this cause unbounded disk growth?
- `WorkspaceStore` uses a JSON file with a threading lock — what happens if the server crashes mid-write?
- The `/workspace/run` endpoint runs `_job_service.start_job_execution()` synchronously (blocking the HTTP worker) — is this documented and acceptable?
- Pagination in `GET /jobs`: `limit` capped at 200, but the UI fetches `limit=500` — what happens?
- The `_recover_interrupted_jobs()` function in `SqliteJobStore.__init__` — is it correct to mark all RUNNING jobs as FAILED on every startup?
- Missing `review_json_path` for XRPL jobs: `_build_xrpl_command()` does not pass `--rf1159-json` to the CLI
- `attach_csv_to_job`: validates that the CSV file exists at attach time, but the file may be deleted before the job starts
- Error handling in `get_job_review`: silently skips unreadable review files — is this the right behaviour?
- Test coverage: which happy paths and edge cases are tested, and what is missing?

**Do NOT cover:** frontend code, tax law, security exploits.

**Files to read:**
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\main.py` — all route handlers, especially start_job (lines 210–230), cancel_job (lines 233–252), and get_job_review (lines 420–477)
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\storage.py` — SqliteJobStore threading, _connect(), _recover_interrupted_jobs(), WorkspaceStore._save_locked()
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\services.py` — start_job_execution(), _build_xrpl_command() (no --rf1159-json flag), _build_csv_command()
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\tests\test_jobs_api.py`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\tests\test_job_execution.py`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\tests\test_background_worker.py`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\tests\test_security.py`

**Questions to answer:**
1. In `start_job` (main.py ~line 229): `asyncio.create_task(asyncio.to_thread(...))` is called without awaiting. The status is checked before the task is created. Can two concurrent POST /jobs/{id}/start requests both pass the `status == PENDING` check before either transitions to RUNNING?
2. Does `SqliteJobStore._connect()` enable WAL mode? Without it, concurrent readers may block writers.
3. `GET /jobs` is capped at `limit=200` by the API validator, but the UI JavaScript calls `?limit=500`. What response does the server actually send?
4. Does `_build_xrpl_command()` ever emit a `--rf1159-json` flag? If not, XRPL jobs never produce RF-1159 exports.
5. `WorkspaceStore._save_locked()` calls `self._path.write_text(...)` — is this atomic? What happens if the process is killed mid-write?
6. What happens when a job is cancelled while running, then the background thread completes successfully and calls `store.update_job` with `status=COMPLETED` — does the FAILED status get silently overwritten?
7. Are there tests for the concurrent double-start scenario, the cancel-then-complete race, or the limit=500 pagination edge case?

**Output format:**
Produce a numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 5 — Frontend / JavaScript

You are a Frontend JavaScript specialist. Audit the single-page UI for correctness and robustness.

**Scope (ONLY these topics):**
- JavaScript correctness: async/await error paths, unhandled promise rejections
- XSS vectors: where is `escHtml()` used and where is it missing? Look for any innerHTML assignment that includes user-controlled data without escaping
- The `escHtml` function itself — is it implemented correctly?
- Polling logic: `_startPolling()` and `_stopPolling()` — are timers always cleaned up? Can multiple polling intervals accumulate?
- The `openResults()` function: it calls `_startPolling()` on every invocation for running jobs — can this create duplicate intervals?
- Memory leaks: event listeners added to elements that are later replaced via `innerHTML`
- Filter logic in `_filteredJobs()`: case-sensitivity, edge cases with empty strings
- The `loadJobs()` function fetches `limit=500` but the API caps at 200 — is the UI aware of this limit?
- The `dropZone.addEventListener('click')` handler: clicking the drop zone calls `fileInput.click()`, but the drop zone is inside a `<label>` that wraps the file input — does this trigger a double file dialog?
- The overlay spinner (`run-overlay`) — is it always removed even when `runReport()` throws synchronously?
- Browser tab title: never updated to reflect the current state
- The `fetchPrices()` function uses `data.path` (a server-side absolute path) and puts it in a text input for the user — on a remote server this path is meaningless to a local client
- `badgeHtml()` constructs HTML with template literals and concatenates `String(status).toUpperCase()` — if `status` contains HTML characters, is this safe?

**Do NOT cover:** backend routes, visual design, tax law.

**Files to read:**
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\ui\index.html` — read the entire file (it contains all JS inline); pay particular attention to:
  - The `escHtml` function definition
  - `_jobRowHtml()` — template literal building HTML with `escHtml(j.id)` and `escHtml(label)`
  - `renderWorkspace()` — DOM manipulation building tags
  - `openResults()` and `_startPolling()` / `_stopPolling()`
  - `loadReviewBadge()` — does it escape review warning strings?
  - `runReport()` error handling and overlay removal

**Questions to answer:**
1. Is `escHtml()` defined in the file? If so, does it escape all five dangerous characters (`&`, `<`, `>`, `"`, `'`)?
2. In `_jobRowHtml()`: the cancel/delete buttons embed `escHtml(j.id)` inside an `onclick` attribute string. Is this safe against a crafted job ID?
3. In `renderWorkspace()`: account tags are built with `document.createElement` and `.textContent` — is this safe? What about the CSV file path used in `span.title`?
4. In `openResults()`: if the function is called multiple times on the same running job, does `_startPolling()` clear the previous interval before setting a new one?
5. In `loadReviewBadge()`: review warning strings from the server are rendered into the DOM — are they escaped?
6. Does the drop zone click handler inside a `<label>` wrapping the file input cause a double file dialog in Chrome?
7. After `runReport()` succeeds, the overlay is removed. Is it also removed in all catch/error paths?

**Output format:**
Produce a numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 6 — UI/UX

You are a UI/UX specialist. Audit the dashboard for usability, accessibility, and information clarity.

**Scope (ONLY these topics):**
- Information architecture: is the layout intuitive for a first-time user?
- Empty states: what does a new user see before any jobs exist?
- Loading states: are there spinners/indicators during all async operations?
- Error messaging: are error messages specific and actionable?
- Accessibility: ARIA labels, keyboard navigation, colour contrast (dark theme), screen reader compatibility
- Form usability: tax year input, country selector, valuation mode selector
- The "dummy valuation" warning — is it prominent enough? Could a user miss it?
- The price table path input: it shows a server-side absolute path — is this comprehensible to a user running against a remote server?
- The "Run Report" button: it is disabled during the run overlay, but can a user still submit via keyboard?
- Mobile/responsive behaviour: the grid is `xl:col-span-3` — what does it look like on a narrow screen?
- The Tax Center section (Holdings, Lots, Dedup tabs): is it clear what "carry-forward lots" means to a non-technical user?
- The inline iframe preview: `sandbox="allow-scripts"` — does this allow the report to interact with the parent page?
- Missing affordance: there is no confirmation dialog before deleting a job
- The alerts panel: severity icons are emoji — are these accessible to screen readers?

**Do NOT cover:** JavaScript bugs, backend API correctness, security exploits.

**Files to read:**
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\ui\index.html` — the entire HTML and CSS sections (up to the `<script>` tag); focus on:
  - ARIA roles and labels on interactive elements
  - Empty state copy in `#jobs-empty-state`
  - The dummy-valuation warning div (look for `dummy-valuation-warning`)
  - The `#results-iframe` sandbox attribute
  - The `run-overlay` modal — is there a focus trap?
  - Colour contrast for `.badge-*` and `.alert-item` classes
  - The `<label>` wrapping the file drop zone (accessibility pattern)
  - The delete button (`btn-danger`) — confirm dialog?
  - The Tax Center tab buttons — ARIA selected state?

**Questions to answer:**
1. Are interactive elements (buttons, the drop zone, job rows) reachable and activatable via keyboard alone?
2. Does the delete button (`btn-danger`) have a confirmation step, or does it delete immediately?
3. Are the alert severity icons (emoji: 🚨, ✕, ⚠) announced correctly to screen readers, or do they need `aria-label`?
4. The price table path field shows a container-internal absolute path after "Fetch prices" — how does a remote user know what to do with `/data/prices/combined_nok_2025.csv`?
5. Is the "Not for filing" dummy-valuation warning visually prominent enough, and is it in an `aria-live` region so screen readers announce it when it appears?
6. What does a brand-new user see when they open the dashboard with no workspace configured and no jobs?
7. Does the results panel iframe with `sandbox="allow-scripts"` allow the embedded tax report HTML to navigate the parent page?

**Output format:**
Produce a numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 7 — Infrastructure & Operations

You are an Infrastructure and Operations specialist. Audit the deployment configuration, environment management, and operational readiness.

**Scope (ONLY these topics):**
- Docker configuration: base image pinning, layer caching, multi-stage build absence
- The `blockchain-reader` dependency is installed without a pinned tag — what are the supply-chain implications?
- Environment variable management: which settings have insecure defaults?
- SQLite as the production database: single-writer constraint, no WAL mode configuration in connection strings, no backup strategy
- The `workspace.json` file — no atomic write, no backup
- The `PRICES_DIR` stores downloaded CSVs indefinitely — no cleanup
- The `OUTPUT_DIR` and `UPLOAD_DIR` accumulate files indefinitely — disk management
- Process supervision: `CMD uvicorn ... --workers 1` — no process supervisor (supervisord/s6/tini)
- Log management: execution logs are written to `OUTPUT_DIR/{job_id}/execution.log` as plain files — no log rotation, no centralised logging
- Health check: returns HTTP 200 even when degraded — is this correct for Kubernetes readiness probes?
- CI/CD: the `.github/workflows/` directory (if present) — are secrets handled correctly?
- The `Dockerfile.local` variant — what is its purpose, and does it introduce any risks?
- `requirements.lock` — is it used in the Dockerfile, or is the lockfile bypassed?
- Windows path defaults in `config.py` (`C:\tmp\taxspine_orchestrator`) — what happens if deployed on Linux with the default?
- The `docker-compose.synology.yml` — NAS-specific configuration, port bindings, volume mounts

**Do NOT cover:** application code logic, tax law, UI design.

**Files to read:**
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\Dockerfile`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\Dockerfile.local`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\docker-compose.yml`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\docker-compose.synology.yml`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\config.py`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\taxspine_orchestrator\storage.py` — SqliteJobStore._connect() and _init_db() — is WAL mode set?
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\pyproject.toml`
- `C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\requirements.lock`

**Questions to answer:**
1. Is the `blockchain-reader` pip install in the Dockerfile pinned to a specific commit hash or tag? If not, what is the supply-chain risk?
2. Does `SqliteJobStore._init_db()` or `_connect()` enable WAL mode (`PRAGMA journal_mode=WAL`)? Without it, concurrent reads block writes.
3. Is `requirements.lock` used in the Dockerfile `pip install` step, or is it bypassed in favour of `pyproject.toml`?
4. What happens if the server is deployed on Linux with default settings? The `_DEFAULT_BASE` path is `C:\tmp\...` on Windows — does this fall back correctly?
5. Is there a volume mount for `/data` in `docker-compose.yml`? If not, all job data and uploaded CSVs are lost on container restart.
6. Does the production Dockerfile run the process as a non-root user?
7. Does the health check correctly distinguish "liveness" (process alive) from "readiness" (can serve requests), and is this distinction appropriate for Kubernetes deployments?

**Output format:**
Produce a numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Consolidation Instructions

After all 7 agents have returned their findings, write `AUDIT_REPORT.md` at:
```
C:\Users\mrkro\Documents\Repo cloning\taxspine-orchestrator\AUDIT_REPORT.md
```

Structure:

```
# Audit Report — taxspine-orchestrator
Generated: <date>

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL |   N   |
| HIGH     |   N   |
| MEDIUM   |   N   |
| LOW      |   N   |
| INFO     |   N   |
| **Total**| **N** |

## Agent 1 — Legal & Compliance
[findings sorted CRITICAL → INFO]

## Agent 2 — Tax Law Correctness
[findings sorted CRITICAL → INFO]

## Agent 3 — Security / Red Hat
[findings sorted CRITICAL → INFO]

## Agent 4 — Backend / API
[findings sorted CRITICAL → INFO]

## Agent 5 — Frontend / JavaScript
[findings sorted CRITICAL → INFO]

## Agent 6 — UI/UX
[findings sorted CRITICAL → INFO]

## Agent 7 — Infrastructure & Operations
[findings sorted CRITICAL → INFO]
```

Do not truncate or summarise any finding. Write every finding in full.
