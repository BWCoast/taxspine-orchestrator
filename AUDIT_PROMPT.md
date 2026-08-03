# Multi-Agent Audit Prompt — taxspine-orchestrator

**How to use:**
Paste everything below the horizontal rule into a Claude Code session opened at the repo root.
It launches 7 parallel specialist agents and consolidates every finding into `AUDIT_REPORT.md`.
Update the "Current build state" section before each run to reflect recent changes.

---

## Current build state (update this before each audit run)

| Item | Status |
|------|--------|
| UI framework | React 18 + Babel CDN, single file `ui/index.html` |
| Auth | `X-Api-Key` header · key stored in browser `localStorage` · `ORCHESTRATOR_KEY` in `.env` |
| AI analysis | `POST /ai/analyze` · Anthropic claude-opus-4-5 · `ANTHROPIC_API_KEY` in `.env` |
| CORS | Fixed: `CORS_ORIGINS` validator handles both JSON array and comma-separated string |
| Login | Multi-workspace selector · validates `/health` then `/workspace` · key persists in localStorage |
| Job history | Expandable rows → `GET /jobs/{id}/files` → download buttons per file kind |
| ReviewQueue | "Analyze with Claude ✦" button · sends warnings + job context to `/ai/analyze` |
| Prices | Real `POST /prices/collect` wired · real `GET /prices` CSV table |
| Error handling | `PageErrorBoundary` around all page content · lazy `renderPage()` switch |
| Audit baseline | Previous audit: 2026-03-23 · all 45 findings resolved |
| Last prompt update | 2026-05-05 |

---

You are orchestrating a full-spectrum audit of the **taxspine-orchestrator** repository.

Repository root (absolute path — use this in all tool calls):
```
%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator
```

Launch all 7 agents simultaneously in a **single message** using 7 parallel Agent tool calls.
Each agent has a sharply bounded scope — do NOT let agents stray outside their domain.

Each agent must:
- Read the relevant source files using the Read tool
- Produce a **numbered list of findings**
- Rate each finding: **CRITICAL / HIGH / MEDIUM / LOW / INFO**
- Keep each finding to one paragraph maximum
- Include exact file path and line number where relevant

After all agents complete, consolidate every finding into `AUDIT_REPORT.md` at the repository root with:
- One section per agent (agent name as heading)
- Findings sorted by severity (CRITICAL first, INFO last)
- A **SUMMARY** table at the very top counting findings by severity
- Do not truncate or summarise any finding

---

## Agent 1 — Legal & Compliance

You are a Legal & Compliance specialist.

**Scope (ONLY these topics):**
- GDPR / Norwegian Personopplysningsloven: does the system process personal data and on what legal basis?
- PII in logs, error messages, stored files, API responses — especially execution logs from CLI subprocesses
- Data retention: are job records, uploaded CSVs, output files, and price cache files ever auto-deleted?
- Right to erasure: `DELETE /workspace` exists — does it also remove all associated job outputs and uploaded files?
- XRPL account addresses as personal data: are they pseudonymous under Norwegian law? Is this documented?
- Outbound data: what is sent to Kraken API, Norges Bank API, and Anthropic API? Could it be linked to an individual?
- The Anthropic API call (`POST /ai/analyze`): tax review data (warnings, job IDs, year) is sent to a third-party US cloud service — what GDPR obligations does this trigger? Is the user informed?
- Privacy notice or DPA reference anywhere in the codebase or docs?

**Do NOT cover:** security exploits, tax law correctness, code quality, UI design.

**Files to read:**
- `taxspine_orchestrator/main.py` — all endpoints that store, retrieve, or expose user data; the `DELETE /workspace` endpoint
- `taxspine_orchestrator/storage.py` — what is persisted and for how long
- `taxspine_orchestrator/services.py` — what ends up in execution logs (stdout/stderr from CLIs)
- `taxspine_orchestrator/prices.py` — what is sent to Kraken and Norges Bank APIs
- `taxspine_orchestrator/ai.py` — what is sent to Anthropic; the system prompt; whether job-specific data (account addresses, amounts) could be included in the context string
- `taxspine_orchestrator/config.py` — data directory layout
- `README.md` — any privacy notices or data-handling documentation

**Questions to answer:**
1. Is any financial transaction data (amounts, dates, asset symbols) sent to the Anthropic API in the `context` field of `POST /ai/analyze`? Does the system prompt or the endpoint attempt to scrub PII before sending?
2. What personal data could end up in `execution.log` files captured from CLI stderr/stdout? Is this data encrypted at rest?
3. Are uploaded CSV files (which contain full trade history) ever automatically deleted? Is there a retention policy?
4. Does `DELETE /workspace` also remove associated job records, output files, and uploaded CSVs — or only the workspace config?
5. XRPL account addresses are stored in `workspace.json` and job records. Are they personal data under GDPR? Is there documentation of the legal basis for processing them?
6. What outbound network calls does the system make? List every external host contacted and what data is sent.
7. Is there a user-facing disclosure that tax review data is sent to Anthropic (a US entity) when the Analyze feature is used?

**Output:** Numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 2 — Tax Law Correctness

You are a Tax Law specialist.

**Scope (ONLY these topics):**
- Norwegian Skatteetaten rules: FIFO lot matching, cost basis in NOK, formue (wealth), RF-1159 Altinn filing
- UK HMRC CGT: Section 104 pooling, 30-day bed-and-breakfast rule, annual exempt amount
- Exchange rate source correctness (Norges Bank for Norway)
- Tax year boundaries: Norway = calendar year (1 Jan – 31 Dec); UK = 6 Apr – 5 Apr
- Staking and airdrop treatment under both regimes
- Dummy valuation mode: could zero-value output be inadvertently filed?
- RF-1159 schema completeness
- RLUSD / stablecoin treatment: is RLUSD treated as a taxable crypto asset (correct) or NOK cash (incorrect)?
- NOR_MULTI vs PER_FILE pipeline: do they produce different FIFO results, and is this difference explained?
- Carry-forward lot persistence: does the `LOT_STORE_DB` correctly handle year-year transitions?

**Do NOT cover:** code bugs unrelated to tax accuracy, security, UI design.

**Files to read:**
- `taxspine_orchestrator/prices.py` — exchange rate source and supported assets; RLUSD handling
- `taxspine_orchestrator/services.py` — how CLI arguments are assembled (year, valuation flags, FIFO flags)
- `taxspine_orchestrator/models.py` — `ValuationMode` enum, `PipelineMode`, `Country`
- `taxspine_orchestrator/main.py` — dummy-valuation warning in `POST /workspace/run` and RF-1159 `rf1159_warnings` field
- `taxspine_orchestrator/lots.py` — lot carry-forward API (what is stored and to which years)
- `README.md`

**Questions to answer:**
1. Is Norges Bank the legally correct source for currency conversion under Skatteetaten guidance?
2. Does dummy-valuation (NOK = 0 for unpriced assets) produce output that could be inadvertently filed? Is the user warned at the point of viewing results?
3. Is the RLUSD pricing gap (Tier 3 static peg at $1.00) correct under Norwegian tax guidance? Is this disclosed to the user?
4. Does NOR_MULTI mode (unified FIFO pool across all sources) produce different tax numbers than PER_FILE (separate FIFO per file)? Is the difference explained in the UI?
5. Are staking rewards and airdrops classified as income at market value on receipt (correct) vs capital gain on disposal (incorrect)?
6. Does the system enforce that UK jobs use 6 Apr – 5 Apr boundaries rather than calendar year?
7. Are all RF-1159 required fields populated, or are any silently omitted when data is missing?

**Output:** Numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 3 — Security / Red Hat

You are a Security specialist (adversarial perspective). Threat model: personal NAS, single trusted user, but potentially exposed to the internet via Tailscale or port-forwarding. The API has optional auth (`ORCHESTRATOR_KEY`) that is disabled by default.

**Scope (ONLY these topics):**
- Auth bypass: `ORCHESTRATOR_KEY = ""` by default means the API is fully open — is this made clear to users?
- `localStorage` key storage: storing the API key in `localStorage` (not `sessionStorage`) persists it across browser sessions. What is the XSS exposure?
- Anthropic API key exposure: `ANTHROPIC_API_KEY` lives in `.env` on the server — is it ever sent to the client? Does the `/ai/analyze` endpoint proxy it safely? Could a timing attack reveal whether a key is configured?
- Path traversal: `GET /jobs/{id}/files/{kind}` validates the path is inside `OUTPUT_DIR` — is this check robust against symlinks or Windows UNC paths?
- SQL injection: `SqliteJobStore.list()` — is the WHERE clause f-string safe?
- Command injection: `_build_xrpl_command()`, `_build_csv_command()`, `_build_nor_multi_command()` — are account addresses and file paths passed as list arguments (safe) or interpolated into shell strings?
- SSRF: `csv_prices_path` in `JobInput` — can it be a UNC path or URL causing the server to read from a network location?
- Upload validation: only filename suffix checked — is there any content-level validation?
- CORS: `CORS_ORIGINS` now requires explicit origins — is the default restrictive enough?
- `GET /health` is unauthenticated — does it leak any internal details?
- `GET /ai/status` is protected by `_require_key` — confirm it does NOT expose the Anthropic key value, only a boolean
- Rate limiting: no rate limiting on `/ai/analyze` — can this cause uncontrolled Anthropic API spend?
- The `dedup.py` `_db_path()` function: `source` parameter used to construct a filesystem path — is it sanitised?

**Do NOT cover:** tax accuracy, UI design, legal compliance.

**Files to read:**
- `taxspine_orchestrator/main.py` — auth dependency `_require_key`, file-serve path check, all endpoint decorators
- `taxspine_orchestrator/ai.py` — what is sent to Anthropic; key exposure; rate limiting
- `taxspine_orchestrator/storage.py` — `SqliteJobStore.list()` SQL construction
- `taxspine_orchestrator/services.py` — `_build_xrpl_command()`, `_build_csv_command()`, `_build_nor_multi_command()`, `_dedup_store_path()`
- `taxspine_orchestrator/dedup.py` — `_db_path()` and source parameter sanitisation
- `taxspine_orchestrator/config.py` — `ORCHESTRATOR_KEY` default, `CORS_ORIGINS` validator
- `ui/index.html` — `localStorage.getItem('orchestrator_key')` usage; any innerHTML with unsanitised server data
- `Dockerfile` — build args, secret handling, GH_READ_TOKEN
- `tests/test_security.py` — what is already covered

**Questions to answer:**
1. `ORCHESTRATOR_KEY = ""` (default): is the API fully unauthenticated in all default deployments? Is there a startup warning? Does `REQUIRE_AUTH` flag work correctly?
2. The orchestrator key is now stored in `localStorage`. If an attacker achieves XSS in the dashboard, they can steal the key. What is the severity given this is a personal NAS tool?
3. `POST /ai/analyze` has no rate limiting. Could a malicious page (open in same browser) exhaust the Anthropic API budget? Does the endpoint require auth?
4. Does `GET /ai/status` return anything beyond `{"available": bool}`? Specifically, does it ever echo the key value or its prefix?
5. In `_dedup_store_path()` and `dedup.py _db_path()`: does replacing `/` and `\` with `_` prevent path traversal (consider `..` sequences, null bytes, absolute paths starting with drive letters)?
6. In `_build_xrpl_command()`: is the XRPL r-address validated against the regex `_XRPL_ADDRESS_RE` before being passed to subprocess? Is the regex validated server-side or only in `models.py` at deserialization?
7. In `GET /jobs/{id}/files/{kind}`: the `resolved.relative_to(settings.OUTPUT_DIR.resolve())` check — does this handle Windows symlinks or drive-relative paths correctly?

**Output:** Numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 4 — Backend / API

You are a Backend and API specialist.

**Scope (ONLY these topics):**
- Race conditions: double-start, cancel-then-complete
- SQLite threading: WAL mode, connection-per-operation, lock correctness
- `WorkspaceStore` atomicity: `write_text()` is not atomic — crash mid-write corrupts the file
- Job deletion: `DELETE /jobs/{id}` — does it remove output files on disk? Can disk grow unbounded?
- `GET /jobs` pagination: `limit` capped at 200 in the API validator — does the UI request match?
- The `_recover_interrupted_jobs()` call on startup: marking all RUNNING jobs as FAILED — is this correct?
- `POST /ai/analyze` error handling: `urllib.error.HTTPError` from Anthropic — are all error codes handled? What happens on a 429 (rate limit) from Anthropic?
- `GET /ai/status` races: if `ANTHROPIC_API_KEY` is set in `.env` but the server hasn't restarted, what does `/ai/status` return?
- `CORS_ORIGINS` validator in `config.py`: the new `_parse_cors_origins` field_validator — does it correctly handle both formats in all environments?
- The `prices.py` auto-fetch: `fetch_all_prices_for_year()` is called inline if prices are missing — does this block the HTTP worker thread?
- `POST /prices/collect` — is this endpoint synchronous? What is the timeout risk for a slow Kraken API?
- `attach_csv_to_job`: validates file existence at attach time — file may be deleted before job starts
- Test coverage: which new endpoints (`/ai/analyze`, `/ai/status`, `/prices/collect`) have tests?

**Do NOT cover:** frontend code, tax law, security exploits.

**Files to read:**
- `taxspine_orchestrator/main.py` — start_job, cancel_job, get_job_review, ai_router include, prices_router include
- `taxspine_orchestrator/ai.py` — error handling for 429/502/503 from Anthropic
- `taxspine_orchestrator/config.py` — `_parse_cors_origins` validator
- `taxspine_orchestrator/storage.py` — `SqliteJobStore` threading, `_connect()`, `_recover_interrupted_jobs()`, `WorkspaceStore._save_locked()`
- `taxspine_orchestrator/services.py` — `start_job_execution()`, auto-fetch inline call
- `taxspine_orchestrator/prices.py` — `fetch_all_prices_for_year()` blocking behaviour
- `tests/test_jobs_api.py`
- `tests/test_job_execution.py`
- `tests/test_security.py`

**Questions to answer:**
1. Does `SqliteJobStore._connect()` enable WAL mode? Without it, concurrent reads block writes.
2. `POST /ai/analyze` calls `urllib.request.urlopen(req, timeout=60)`. If Anthropic is slow, this blocks the Uvicorn worker for 60 seconds. Is `workers=1` the default in the Dockerfile? What is the risk?
3. `POST /prices/collect` is synchronous and calls external APIs (Kraken, Norges Bank). What is the timeout? Can this block the worker for minutes?
4. Does `_parse_cors_origins` correctly handle an empty string value (`CORS_ORIGINS=`) in the env file?
5. When a job is cancelled while running and the background thread later completes, does the `FAILED` status get overwritten by `COMPLETED`?
6. Are there tests for `/ai/analyze` and `/ai/status`? For `/prices/collect`?
7. `WorkspaceStore._save_locked()` uses `write_text()` which is not atomic. What data is lost if the server crashes between the temp-file write and the rename (if any)?

**Output:** Numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 5 — Frontend / React

You are a Frontend specialist. The UI is a single React 18 + Babel CDN file (`ui/index.html`). All components are in-browser JSX. There is no build step.

**Scope (ONLY these topics):**
- XSS vectors: where is user-supplied or server-returned data inserted into the DOM? Is it via React's safe JSX interpolation (`{value}`) or via `dangerouslySetInnerHTML` / `innerHTML`?
- The `window.api` singleton: `localStorage.getItem('orchestrator_key')` — is the key ever logged, included in error messages, or sent to non-server origins?
- Error boundary: `PageErrorBoundary` wraps all page content with `key={route}`. Verify it resets correctly on navigation and shows the error message rather than an empty screen.
- The lazy `renderPage()` switch: confirm no unused pages are instantiated on every render.
- `SettingsRow`, `SettingsLabel`, `SettingsField` are now module-level — confirm they are NOT defined inside Settings anymore (they were moved to fix a remount bug).
- Claude AI result rendering: `aiResult.analysis` is a plain string from Anthropic — it is rendered with `whiteSpace: 'pre-wrap'` inside a `<div>`. Is this safe? Could markdown/HTML in the response cause rendering issues?
- `window.api.download()`: uses `URL.createObjectURL` — is the blob URL revoked after use?
- Polling in `TaxCenter`: `pollRef.current = setInterval(...)`. Is the interval always cleared on unmount? Is the effect cleanup correct?
- The expandable job history rows: `jobFilesCache` grows indefinitely (one entry per expanded job). On a long-lived session with many jobs, is this a memory concern?
- The `ReviewQueue` `analyzeWithClaude()` function: what happens if the user clicks Analyze while a request is already in flight? Is there a guard?
- `Login.select()`: calls `window.api.get('/health')` then `window.api.get('/workspace')`. If `/health` succeeds but `/workspace` returns a non-401 error, is the error message user-friendly?
- The `workspaces` state in Login is initialised from `localStorage` and defaults to `[{ name: 'Ola', ... }]` — this hardcoded name should not be in the code.

**Do NOT cover:** backend routes, tax law, visual design.

**Files to read:**
- `ui/index.html` — read the full file, focusing on:
  - `window.api` singleton (lines ~3560–3640): key storage, `download()` blob cleanup
  - `PageErrorBoundary` class component
  - `renderPage()` switch in `App`
  - `Login.select()` auth flow
  - `TaxCenter` polling cleanup in `useEffect` return
  - `ReviewQueue.analyzeWithClaude()` — double-click guard
  - `aiResult.analysis` rendering
  - `SettingsRow`, `SettingsLabel`, `SettingsField` — confirm they are outside `Settings`
  - Any `dangerouslySetInnerHTML` or `.innerHTML =` assignments

**Questions to answer:**
1. Is there any `dangerouslySetInnerHTML` or `.innerHTML =` in the file? If so, is the inserted value user-supplied or server-returned without sanitisation?
2. The `Claude analysis` result (`aiResult.analysis`) is rendered as `{aiResult.analysis}` in JSX — React escapes this. But does `whiteSpace: 'pre-wrap'` plus a response containing HTML-like content cause any visual confusion (not an XSS risk, but a display risk)?
3. Does the `useEffect` cleanup in `TaxCenter` correctly call `clearInterval(pollRef.current)` on unmount? What happens if the user navigates away from TaxCenter while a job is polling?
4. `analyzeWithClaude()` sets `aiLoading(true)` and the button is `disabled={aiLoading}`. Is this guard applied before the async call, or could two requests be in flight simultaneously?
5. The `window.api.download()` function appends an `<a>` to `document.body`, clicks it, then removes it with a 1-second timeout. Is there a risk of the element not being cleaned up if an error occurs before the timeout fires?
6. The `jobFilesCache` object is never pruned. In a session where a user expands 50 job rows, how large could this object grow? Is there a practical risk?
7. The hardcoded `{ name: 'Ola', workspace_id: '', primary: true }` default workspace in `Login` — this should be removed or replaced with a generic default.

**Output:** Numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 6 — UI/UX

You are a UI/UX specialist. Audit the React dashboard for usability, accessibility, and information clarity. Assume the user is a non-technical person who runs this once or twice a year for personal Norwegian crypto taxes.

**Scope (ONLY these topics):**
- First-run experience: what does a brand-new user see before any jobs exist?
- Login flow: multi-workspace selector, "Add person…" for friends' taxes — is this discoverable?
- Connection Settings in Login: "Password / API key" label — is it clear this is the ORCHESTRATOR_KEY and not the Claude API key?
- The AI Analyze button in ReviewQueue: does it clearly indicate it sends data to a third-party (Anthropic)? Is the advisory-only nature of the result communicated?
- The AI result panel: long multi-paragraph analysis rendered with `pre-wrap` — is this readable and well-formatted?
- TaxCenter job history: expandable rows with download buttons — are the file type labels (HTML Report, RF-1159 JSON, etc.) clear to a non-technical user?
- Auto-fetch gate warning (amber banner before Run button when prices are stale) — is it actionable?
- ReviewQueue: warnings list — are warnings actionable or just raw technical strings from the CLI?
- Settings → Advanced: "Claude AI: Available/Not configured" — is the meaning clear to a non-developer?
- Sidebar navigation badge for Review Queue (hardcoded `badge: 5`) — this is demo data and should be real or removed
- Error states: with `PageErrorBoundary` in place, users now see a red error panel instead of a blank screen — is the message helpful to a non-developer?
- Accessibility: ARIA labels, keyboard navigation, colour contrast, screen reader compatibility

**Do NOT cover:** JavaScript bugs, backend API correctness, security exploits.

**Files to read:**
- `ui/index.html` — focus on:
  - Login component: workspace cards, "Add person…", Connection Settings panel labels
  - ReviewQueue: warnings tab, Analyze button, AI result panel
  - TaxCenter: expandable job history rows, file kind labels, auto-fetch gate warning
  - Sidebar: `badge: 5` hardcoded value
  - Settings: Claude AI row, API key field label
  - `PageErrorBoundary` render output
  - ARIA attributes, `role`, `aria-label` on interactive elements

**Questions to answer:**
1. Is it clear to a non-technical user that the "Password / API key" in Login Connection Settings is a server-side configuration value — not their Claude API key, their Anthropic key, or their Skatteetaten password?
2. Does the "Analyze with Claude ✦" button communicate that the analysis is advisory-only and that review data is sent to Anthropic? Is there any disclosure before the first send?
3. Are the file download labels ("RF-1159 JSON", "Review JSON", "Gains CSV") comprehensible to a non-developer? Should they be renamed or annotated?
4. The Review Queue shows raw warning strings from the CLI engine. Are these strings phrased in user-friendly language, or are they technical messages (e.g., "Unresolved lot for event id X at timestamp Y")?
5. What happens in the UI when a job takes longer than expected (the poll shows RUNNING for several minutes)? Is there a progress indicator or timeout message?
6. The sidebar Review Queue badge is hardcoded to `5` (demo data). In production, what should this show — the actual warning count for the current year?
7. Are form inputs (wallets, IOU tokens, year selector) keyboard-accessible? Are error messages read by screen readers?

**Output:** Numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Agent 7 — Infrastructure & Operations

You are an Infrastructure and Operations specialist.

**Scope (ONLY these topics):**
- Docker: base image pinning, multi-stage build, non-root user, process supervision
- The `blockchain-reader` dependency: pinned version or floating tag?
- Environment management: which settings have insecure or dangerous defaults? `ORCHESTRATOR_KEY=""`, `REQUIRE_AUTH=False`
- SQLite: WAL mode, single-writer constraint, no backup strategy
- `workspace.json`: no atomic write, no backup
- `PRICES_DIR`, `OUTPUT_DIR`, `UPLOAD_DIR` accumulate files indefinitely — disk management on a NAS
- `ANTHROPIC_API_KEY` in `.env`: file permissions — is `.env` restricted to owner-only reads? Is it excluded from Docker build context (`.dockerignore`)?
- Log management: execution logs written as plain files — no rotation, no centralised logging
- Health check: `GET /health` now checks DB, output dir, upload dir, prices dir, CLI binaries — is this appropriate for Kubernetes liveness vs readiness?
- `Dockerfile` and `docker-compose.synology.yml`: NAS-specific configuration, port bindings, volume mounts
- `requirements.lock`: is it used in the Dockerfile, or bypassed?
- Windows path defaults in `config.py`: `C:\tmp\taxspine_orchestrator` — correct handling on Linux?
- The `uvicorn --workers 1` default: is this documented? For a personal tool, is it acceptable?
- Startup warning when `ORCHESTRATOR_KEY=""` — is it emitted? Is `REQUIRE_AUTH=False` default safe?

**Do NOT cover:** application code logic, tax law, UI design.

**Files to read:**
- `Dockerfile`
- `Dockerfile.local`
- `docker-compose.yml`
- `docker-compose.synology.yml`
- `.dockerignore` (if present)
- `taxspine_orchestrator/config.py` — defaults, `REQUIRE_AUTH`, `ANTHROPIC_API_KEY` field
- `taxspine_orchestrator/storage.py` — `SqliteJobStore._connect()` and `_init_db()` — WAL mode?
- `taxspine_orchestrator/main.py` — startup warning for empty `ORCHESTRATOR_KEY`
- `pyproject.toml`
- `requirements.lock`

**Questions to answer:**
1. Is `ANTHROPIC_API_KEY` excluded from the Docker build context via `.dockerignore`? Does the Dockerfile ever `COPY .env` into the image?
2. Does `SqliteJobStore._init_db()` or `_connect()` enable `PRAGMA journal_mode=WAL`? What is the risk without it on a multi-reader NAS setup?
3. Is `requirements.lock` actually used in the `pip install` step, or is `pyproject.toml` used instead (meaning versions are not reproducible)?
4. Does the Dockerfile run the server process as a non-root user?
5. Is there a volume mount for the data directory in `docker-compose.synology.yml`? If not, job data is lost on container restart.
6. Does the server emit a visible startup warning when `ORCHESTRATOR_KEY=""` in a network-reachable deployment? Does `REQUIRE_AUTH=True` block startup when the key is empty?
7. Are `OUTPUT_DIR`, `UPLOAD_DIR`, and `PRICES_DIR` ever cleaned up? Is there an admin endpoint or cron job for this? What is the expected disk growth rate for a single user's annual tax run?

**Output:** Numbered list. Each item: `N. [SEVERITY] Short title — explanation.`

---

## Consolidation Instructions

After all 7 agents complete, write `AUDIT_REPORT.md` at:
```
%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator\AUDIT_REPORT.md
```

Structure:
```markdown
# Audit Report — taxspine-orchestrator
Generated: <ISO date>
Build state: React 18 dashboard · AI analysis · localStorage auth

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
## Agent 2 — Tax Law Correctness
## Agent 3 — Security / Red Hat
## Agent 4 — Backend / API
## Agent 5 — Frontend / React
## Agent 6 — UI/UX
## Agent 7 — Infrastructure & Operations
```

- Sort findings CRITICAL → INFO within each section
- Do not truncate or summarise any finding
- Append prior audit reference: "Previous audit: 2026-03-23 · 45 findings · 0 open"
