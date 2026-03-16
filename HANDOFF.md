# Session Handoff — Audit Remediation

**Last updated:** 2026-03-17
**Completed through:** Batch 11 of 18
**Test count:** 609 passing, 0 failing

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
3. Identify the next unfixed findings (HIGH severity first, then MEDIUM, LOW, INFO)
4. The user will say "Batch N. Execute." — implement all findings in that batch,
   write tests, verify `pytest --tb=short -q` → all pass, then stop

---

## Current state (end of Batch 11)

### All remediated findings
See `REMEDIATION_PROGRESS.md` for the full list with code-level detail.

### Key architectural changes made during remediation

**Storage layer (`taxspine_orchestrator/storage.py`)**
- `SqliteJobStore` and `InMemoryJobStore` both now have `transition_status(job_id, from_status, to_status)` — atomic CAS for status transitions
- SQLite WAL mode enabled in both `_init_db()` and `_connect()` (`PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`)
- LIKE wildcard injection defence: `%` and `_` escaped with `ESCAPE '\\'` in `list()` query

**Models (`taxspine_orchestrator/models.py`)**
- `JobStatus.CANCELLED = "cancelled"` — distinct terminal state for user-initiated cancellation

**Services (`taxspine_orchestrator/services.py`)**
- `start_job_execution` accepts RUNNING state (endpoint now does the CAS before calling service)
- CANCELLED guard in `start_job_execution`, `_fail_job`, `_execute_dry_run` — prevents CANCELLED being overwritten by COMPLETED/FAILED
- Source slug allowlist: `re.sub(r"[^A-Za-z0-9_-]", "_", slug)` + `Path.resolve().relative_to()` containment

**API (`taxspine_orchestrator/main.py`)**
- `start_job`: uses `_job_store.transition_status(PENDING, RUNNING)` CAS before spawning thread
- `cancel_job`: sets `JobStatus.CANCELLED` (not FAILED); docstring updated
- `run_workspace_report`: `async def` with `await asyncio.to_thread(...)` — non-blocking
- `/health` and `/alerts`: opaque `"error"` string returned to callers; full exception logged server-side

**UI (`ui/index.html`)**
- `limit=500` → `limit=200` in both job-fetch calls (matches server `le=200` constraint)
- `run-overlay` hide in `finally` block (FE-01)
- iframe sandbox: no `allow-top-navigation`, no `allow-same-origin`; CSP in blob URL (UX-01)
- `${created}` / `${updated}` wrapped in `escHtml()` (FE-09)

**Infrastructure**
- `Dockerfile`: `ARG BLOCKCHAIN_READER_SHA=main` + `@${BLOCKCHAIN_READER_SHA}` in pip URL (INFRA-01)
- `.github/workflows/docker.yml`: full 40-char SHA fetched (not `[:12]`), passed as build-arg
- `docker-compose.synology.yml`: pinned `sha-` image tag, `WATCHTOWER_RUN_ONCE: "true"`, `restart: "no"` (INFRA-16)

**Dedup (`taxspine_orchestrator/dedup.py`)**
- `_db_path()`: allowlist regex + containment assertion (SEC-02)

---

## Test files added during remediation

| File | Batch | Tests |
|------|-------|-------|
| `tests/test_ui_fixes.py` | 7, 9 | FE-01 overlay, UX-01 sandbox, FE-09 escHtml, path validation |
| `tests/test_infra_hardening.py` | 8 | INFRA-01 SHA pin, INFRA-02 WAL, INFRA-16 Compose pin |
| `tests/test_medium_security.py` | 10 | SEC-01 LIKE injection, SEC-02 slug sanitisation, SEC-16 opaque errors |
| `tests/test_api_hardening.py` | 11 | API-03 async, API-04 CAS, API-05 CANCELLED, API-06 limit, API-07 cancel guard |

---

## Key files to read before starting Batch 12

```
taxspine_orchestrator/main.py       # FastAPI app, all HTTP endpoints
taxspine_orchestrator/services.py   # JobService — pipeline execution
taxspine_orchestrator/storage.py    # InMemoryJobStore, SqliteJobStore, WorkspaceStore
taxspine_orchestrator/models.py     # Pydantic models, JobStatus enum
taxspine_orchestrator/config.py     # Settings (env vars, directory paths)
taxspine_orchestrator/dedup.py      # Dedup store router + _db_path
taxspine_orchestrator/prices.py     # Price fetch router
ui/index.html                       # Single-file dashboard SPA
AUDIT_REPORT.md                     # All 139 findings (canonical source)
REMEDIATION_PROGRESS.md             # What's been fixed
```

---

## Running tests

```bash
cd taxspine-orchestrator
pip install -e ".[dev]"
pytest --tb=short -q        # full suite — must be 0 failures
pytest tests/test_api_hardening.py -v   # batch 11 tests
```

---

## What to tackle next (Batch 12 and beyond)

Open HIGH findings from `AUDIT_REPORT.md` not yet remediated:

**Legal / Compliance (HIGH)**
- LC-07: No consent mechanism for third-party API calls (Kraken, Norges Bank)
- LC-08: No privacy notice / data processing documentation

**Tax Law (HIGH — TL-xx series)**
- Review `AUDIT_REPORT.md` TL findings — dummy-valuation warnings, RF-1159 draft disclaimer, etc.

**Backend (HIGH — BE-xx series)**
- Any remaining BE findings (check audit report for BE-01…BE-04 status)

**Security (MEDIUM — SEC-xx series)**
- SEC-18: SRI-less Tailwind CDN in index.html
- Other remaining SEC MEDIUM findings

**Infrastructure (MEDIUM — INFRA-xx series)**
- Remaining INFRA findings not covered by Batch 8

Work through HIGH first, then MEDIUM, LOW, INFO, MISSING TEST.
