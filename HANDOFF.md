# Session Handoff — Audit Remediation

**Last updated:** 2026-03-17 (end of Batch 12)
**Completed through:** Batch 12 of 18
**Test count:** 633 passing, 0 failing
**All HIGH findings:** ✅ Confirmed remediated

---

## What this project is

`taxspine-orchestrator` is a FastAPI/Python application that coordinates
`blockchain-reader` and `taxspine-*` CLI pipelines to produce Norwegian and UK
crypto tax reports.  It exposes a REST API and a single-file HTML dashboard UI.

The codebase is under an 18-batch audit remediation programme.  Each batch
addresses a group of related findings from `AUDIT_REPORT.md` (139 findings total).

---

## How to pick up the next batch

1. Read `AUDIT_REPORT.md` — the canonical list of all 139 findings
2. Read `REMEDIATION_PROGRESS.md` — what has been fixed and which tests cover it
3. Identify the next unfixed findings (MEDIUM first, then LOW, INFO, MISSING TEST)
4. The user will say "Batch N. Execute." — implement all findings in that batch,
   write tests, verify `pytest --tb=short -q` → all pass, then stop

---

## Status at end of Batch 12

### All HIGH findings confirmed remediated
The Batch 12 scan checked every HIGH finding in the audit report.  All 26 are fixed.
No HIGH findings remain open.

### Remaining open findings (MEDIUM/LOW/INFO/MISSING TEST only)
See `REMEDIATION_PROGRESS.md` and `AUDIT_REPORT.md` for the full list.
Top candidates for Batch 13:

**MEDIUM — Frontend**
- FE-03: drop zone inside `<label>` causes double file dialog
- FE-04: inconsistent escaping in job action handlers (onclick vs fetch)
- FE-13: API constant assumes same-origin root — breaks behind reverse proxy

**MEDIUM — UI/UX**
- UX-02: alert severity emoji lack `aria-label`
- UX-03: dummy-valuation warning not announced to screen readers
- UX-04: price table path field shows server-side absolute path
- UX-05: status badge symbols not `aria-hidden`
- UX-18: run overlay has no escape path / AbortController timeout
- UX-19: download buttons identical styling — RF-1159 indistinguishable from log

**MEDIUM — Security**
- SEC-03: no rate limiting on mutating endpoints
- SEC-17: CLI binary names fully configurable — arbitrary binary execution risk
- SEC-20: `subprocess.run` called without timeout

**MEDIUM — Backend**
- API-19: `GET /jobs` returns no total count — pagination is incomplete
- API-21: `JobOutput` singular/list path fields can diverge
- API-22: several routes missing `response_model` declarations

**MEDIUM — Tax Law**
- TL-03: RF-1159 omits income section (staking/airdrop)
- TL-04: NOR_MULTI vs PER_FILE no warning about different tax results
- TL-05: UK tax year boundary not communicated at job output level

**MEDIUM — Infrastructure**
- INFRA-03: Docker base image not pinned to digest
- INFRA-07: container runs as root (no USER directive)
- INFRA-08: /health returns 200 even when degraded

---

## Key architectural changes made during remediation (cumulative through Batch 12)

**Storage layer (`taxspine_orchestrator/storage.py`)**
- `SqliteJobStore` and `InMemoryJobStore` both have `transition_status(job_id, from_status, to_status)` — atomic CAS
- SQLite WAL mode in both `_init_db()` and `_connect()`
- LIKE wildcard injection defence (`%` and `_` escaped)
- `WorkspaceStore._save_locked()`: atomic write-to-tmp-then-rename
- `update_status()` and `update_job()`: single lock held across full read-modify-write

**Models (`taxspine_orchestrator/models.py`)**
- `JobStatus.CANCELLED = "cancelled"` — distinct terminal state
- `JobInput.tax_year: Field(ge=2009, le=2100)` — rejects out-of-range values

**Services (`taxspine_orchestrator/services.py`)**
- CANCELLED guard in `start_job_execution`, `_fail_job`, `_execute_dry_run`
- Source slug allowlist + containment assertion
- TL-01/TL-02: `_annotate_rf1159_with_provenance()` and `_inject_draft_banner()` for dummy valuation
- TL-11: Non-GENERIC_EVENTS CSVs in mixed XRPL jobs raise FAILED (not silent skip)
- TL-13: `_maybe_write_carry_forward_csv()` wires lot carry-forward via synthetic events

**API (`taxspine_orchestrator/main.py`)**
- `Field` import added (needed by `WorkspaceRunRequest.tax_year`)
- `start_job`: CAS via `transition_status(PENDING, RUNNING)` before spawning thread
- `cancel_job`: sets `JobStatus.CANCELLED`
- `run_workspace_report`: `async def` with `await asyncio.to_thread(...)`
- `get_alerts()`: `Path.read_text()` wrapped in `await asyncio.to_thread(...)`
- `/health` and `/alerts`: opaque `"error"` string; full exception logged server-side
- All GET endpoints authenticated (`dependencies=[Depends(_require_key)]`)
- `POST /jobs`: `status_code=201`
- `WorkspaceRunRequest.tax_year: Field(ge=2009, le=2100)`
- `_background_tasks: set[asyncio.Task]` retains task references to prevent GC

**Dedup (`taxspine_orchestrator/dedup.py`)**
- `_db_path()`: allowlist regex + containment assertion (SEC-02)
- `list_dedup_sources()` and `get_dedup_summary()`: `db_path` removed from response (SEC-19)

**UI (`ui/index.html`)**
- Tailwind: `<link href="tailwind.min.css">` + onerror CDN fallback (SEC-18)
- `loadAlerts()`: `r.ok` check before `.json()` (FE-10)
- `openResultsById()`: injects error HTML into `#results-panel` on failure (FE-11)
- `runReport()`: unchecks `#run-dry` after `successJob` confirmed (FE-12)
- `limit=500` → `limit=200` in job-fetch calls (API-06)
- `run-overlay` hide in `finally` block (FE-01)
- iframe sandbox: no `allow-top-navigation`, no `allow-same-origin`; CSP in blob URL (UX-01)
- `${created}` / `${updated}` wrapped in `escHtml()` (FE-09)
- `fetchPrices()`: server path validated against allowlist pattern (FE-02)
- `a.severity`: validated against `['error','warn','info']` allowlist before CSS class (FE-08)
- `window._jobsTimer`, `window._healthTimer`, `window._alertsTimer` retained (FE-07)
- `removeAccount()` and `removeCsv()`: `confirm()` before DELETE (UX-16)
- `validateXrplAddressInput()` inline validation on blur (UX-17)

**Infrastructure**
- `Dockerfile`: `ARG BLOCKCHAIN_READER_SHA=main` + SHA pin (INFRA-01); `ARG TAILWIND_VERSION=3.4.17` + CSS download (SEC-18)
- `.github/workflows/docker.yml`: full 40-char SHA passed as build-arg
- `docker-compose.synology.yml`: `sha-` image tag; `WATCHTOWER_RUN_ONCE: "true"` (INFRA-16)

---

## Test files added during remediation

| File | Batch | Test count | Findings |
|------|-------|-----------|---------|
| `tests/test_ui_fixes.py` | 7, 9 | ~14 | FE-01, UX-01, FE-09, FE-02, path validation |
| `tests/test_infra_hardening.py` | 8 | 18 | INFRA-01, INFRA-02, INFRA-16 |
| `tests/test_medium_security.py` | 10 | 19 | SEC-01, SEC-02, SEC-16 |
| `tests/test_api_hardening.py` | 11 | 22 | API-03, API-04, API-05, API-06, API-07 |
| `tests/test_batch12.py` | 12 | 24 | API-18, API-20, SEC-18, SEC-19, FE-10, FE-11, FE-12 |

---

## Running tests

```bash
cd taxspine-orchestrator
pip install -e ".[dev]"
pytest --tb=short -q                     # full suite — must be 0 failures
pytest tests/test_batch12.py -v          # batch 12 tests only
```

---

## Key files to read before starting Batch 13

```
taxspine_orchestrator/main.py       # FastAPI app, all HTTP endpoints
taxspine_orchestrator/services.py   # JobService — pipeline execution
taxspine_orchestrator/storage.py    # InMemoryJobStore, SqliteJobStore, WorkspaceStore
taxspine_orchestrator/models.py     # Pydantic models, JobStatus enum
taxspine_orchestrator/config.py     # Settings (env vars, directory paths)
taxspine_orchestrator/dedup.py      # Dedup store router + _db_path
taxspine_orchestrator/prices.py     # Price fetch router
ui/index.html                       # Single-file dashboard SPA (~1600 lines)
AUDIT_REPORT.md                     # All 139 findings (canonical source)
REMEDIATION_PROGRESS.md             # Batch-by-batch status + code-level detail
```

---

## Important: findings that looked open but are already fixed

During Batch 12 pre-scan, the following were verified as already implemented
(do not re-implement):

| Finding | Fixed in | Evidence |
|---------|---------|---------|
| API-02 | Batch 1–6 | `WorkspaceStore._save_locked()` uses `.tmp` + `.replace()` |
| API-15 | Batch 1–6 | `@app.post("/jobs", ..., status_code=201)` |
| API-16 | Batch 1–6 | `update_status()` holds single lock across read-modify-write |
| API-17 | Batch 11 | `_background_tasks: set[asyncio.Task]` retains task refs |
| FE-02  | Batch 1–6 | `pathPattern.test(data.path)` in `fetchPrices()` |
| FE-07  | Batch 11 | `window._jobsTimer`, `_healthTimer`, `_alertsTimer` |
| FE-08  | Batch 11 | `['error','warn','info'].includes(a.severity)` allowlist |
| SEC-11 | Batch 10 | Allowlist regex replaces `\x00` before `Path()` is called |
| SEC-12 | Batch 1–6 | All GET endpoints have `dependencies=[Depends(_require_key)]` |
| SEC-13 | Batch 1–6 | `attach_csv_to_job`: `Path.resolve().relative_to(upload_dir)` |
| SEC-14 | Batch 1–6 | `prices_router` included with `dependencies=[Depends(_require_key)]` |
| TL-01  | Batch 1–6 | `_inject_draft_banner()` + `_provenance.draft=true` in RF-1159 |
| TL-02  | Batch 1–6 | `_annotate_rf1159_with_provenance()` writes `price_source` field |
| TL-11  | Batch 1–6 | Mixed XRPL+non-generic raises FAILED (not silent skip) |
| TL-12  | Batch 1–6 | `prices.py` uses `Decimal(str(...))` throughout |
| TL-13  | Batch 1–6 | `_maybe_write_carry_forward_csv()` in `services.py` |
| UX-16  | Batch 11 | `confirm()` guards in `removeAccount()` and `removeCsv()` |
| UX-17  | Batch 11 | `validateXrplAddressInput()` with blur handler |
