# Audit Remediation Progress

**Audit:** Full-spectrum codebase audit (2026-03-16) — 139 findings across 7 domains
**Remediation window:** 29 batches, one batch per session
**Last updated:** 2026-03-18 (Batch 29 — FINAL: all actionable findings closed)

---

## Batch Status

| Batch | Findings | Status | Tests added | Notes |
|-------|----------|--------|-------------|-------|
| 1–6   | LC-01…LC-06, LC-12 + others | ✅ Complete | Various | Data retention, delete-with-files, redact endpoint, XRPL log redaction, workspace clear |
| 7     | FE-01, UX-01, FE-09         | ✅ Complete | `test_ui_fixes.py` | Overlay in finally, iframe sandbox (no allow-top-navigation/allow-same-origin), escHtml on timestamps |
| 8     | INFRA-01, INFRA-02, INFRA-16 | ✅ Complete | `test_infra_hardening.py` (18) | Blockchain-reader SHA pin, SQLite WAL mode, Watchtower WATCHTOWER_RUN_ONCE |
| 9     | FE-01, UX-01 (tests only)   | ✅ Complete | `test_ui_fixes.py` +10 | Regression-guard tests for already-fixed findings |
| 10    | SEC-01, SEC-02, SEC-16       | ✅ Complete | `test_medium_security.py` (19) | LIKE wildcard escape, slug allowlist, opaque health errors |
| 11    | API-03, API-04, API-05, API-06, API-07 | ✅ Complete | `test_api_hardening.py` (22) | Async workspace/run, CAS start-job, CANCELLED status, UI limit 200, cancel-during-execution guard |
| 12    | API-18, API-20, SEC-18, SEC-19, FE-10, FE-11, FE-12 | ✅ Complete | `test_batch12.py` (24) | tax_year validation, async alerts I/O, self-hosted Tailwind, dedup path hiding, loadAlerts r.ok, openResultsById error, dry_run reset |
| 19    | TL-15, TL-11, TL-09, TL-07/TL-10 | ✅ Complete | `test_batch19.py` (33), `test_batch20.py` (22) | FX gap seeding, mixed-job label, UK dummy warning, price table coverage warning |
| 21    | SEC-06, SEC-07, SEC-08, SEC-09, SEC-10, SEC-11, SEC-15 (+ SEC-03 docs) | ✅ Complete | `test_batch21.py` (37) | Flag injection guard, magic-byte upload check, CORS/rate-limit docs in README; SEC-07/SEC-08/SEC-11/SEC-15 verified already done |
| 22    | API-10, FE-06, LC-14 | ✅ Complete | `test_batch22.py` (18) | Review-file error logging, badgeHtml XSS escaping, LICENSE file added; 935 tests passing |
| 23    | UX-06, UX-09, UX-10, UX-11, UX-12, UX-15, UX-24, API-09 | ✅ Complete | `test_batch23.py` (41) | Review badge aria-labels, touch targets, alert text severity labels, table scope=col, source labels, pipeline help text, jurisdiction-neutral warning, execution-time file error; 976 tests passing |
| 24    | UX-13, UX-14, UX-23 | ✅ Complete | `test_batch24.py` (24) | Upload/TC spinners (animate-spin), in-page showConfirm() modal replaces confirm(), iframe gradient+scroll hint; UX-16 regression tests updated |
| 25    | TL-18, INFRA-10 | ✅ Complete | `test_batch25.py` (19) | Missing-basis lot alerts in GET /alerts (lazy LotPersistenceStore import), apt-get purge build-essential+git in Dockerfile; 1019 tests passing |
| 26    | TL-19, API-11, INFRA-22 | ✅ Complete | `test_batch26.py` (38) | GBP price-fetch endpoint (POST /prices/fetch-gbp, BoE XUDLUSS), DELETE /jobs removes output dir, Dockerfile.local pinned to 3.11.9-slim; 1057 tests passing |
| 27    | TL-08, INFRA-24, LC-10 | ✅ Complete | `test_batch27.py` (29) | Lot carry-forward year-sequence warning (TL-08), start.ps1 dev-only guard (INFRA-24), JobOutput.draft_disclaimer field (LC-10); 1086 tests passing |
| 28    | LC-09, INFRA-25, API-13 | ✅ Complete | `test_batch28.py` (24) | GET /jobs query max_length=200 (LC-09), Python 3.12 added to CI matrix (INFRA-25), cancel-then-complete race regression tests (API-13); 1110 tests passing |
| 29    | TL-06, INFRA-06, INFRA-21, LC-11, INFRA-23, LC-07, LC-08, INFRA-19 | ✅ Complete | `test_batch29.py` (43) | Staking warning (TL-06), cleanup endpoint (INFRA-06), JSON log formatter (INFRA-21), deletion audit log (LC-11), socket-proxy guidance (INFRA-23), third-party API disclosure (LC-07), privacy section (LC-08), backup strategy (INFRA-19); 1153 tests passing |

---

## Remediated Findings Detail

### Batch 7 — Frontend (FE-01, UX-01, FE-09)
- **FE-01** `ui/index.html`: `run-overlay` hide moved into `finally` block — spinner always clears
- **UX-01** `ui/index.html`: iframe sandbox removes `allow-top-navigation` and `allow-same-origin`; CSP injected into blob URL
- **FE-09** `ui/index.html`: `${created}` and `${updated}` timestamp strings wrapped in `escHtml()`

### Batch 8 — Infrastructure (INFRA-01, INFRA-02, INFRA-16)
- **INFRA-01** `Dockerfile`: `ARG BLOCKCHAIN_READER_SHA=main` + `@${BLOCKCHAIN_READER_SHA}` in pip install URL; CI fetches full 40-char SHA
- **INFRA-02** `storage.py`: `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` added to both `_init_db()` and `_connect()`
- **INFRA-16** `docker-compose.synology.yml`: image pinned to `sha-` tag; `WATCHTOWER_RUN_ONCE: "true"` + `restart: "no"`

### Batch 9 — Regression tests (FE-01, UX-01)
- Added `TestFE01OverlayCleanup` (4 tests) and `TestUX01IframeSandbox` (6 tests) to `test_ui_fixes.py`

### Batch 10 — Security (SEC-01, SEC-02, SEC-16)
- **SEC-01** `storage.py`: LIKE metacharacters `%` and `_` escaped before LIKE query; `ESCAPE '\\'` clause added
- **SEC-02** `dedup.py` + `services.py`: source slugs sanitised with `re.sub(r"[^A-Za-z0-9_-]", "_", ...)` + `Path.resolve().relative_to()` containment assertion
- **SEC-16** `main.py`: `/health` and `/alerts` return opaque `"error"` string; full exception text logged server-side only (`_log.error(...)`)

### Batch 11 — API hardening (API-03, API-04, API-05, API-06, API-07)
- **API-03** `main.py`: `run_workspace_report` converted to `async def` with `await asyncio.to_thread(_job_service.start_job_execution, job.id)` — event loop no longer blocked during CLI subprocess calls
- **API-04** `storage.py` + `main.py`: `transition_status(job_id, from_status, to_status)` CAS method added to both `InMemoryJobStore` and `SqliteJobStore`; `start_job` endpoint uses CAS before spawning thread — duplicate-start race eliminated
- **API-05** `models.py` + `main.py`: `JobStatus.CANCELLED = "cancelled"` added; `cancel_job` endpoint uses `JobStatus.CANCELLED` (not `FAILED`) — user-initiated cancellation is now distinguishable from execution errors
- **API-06** `ui/index.html`: both `limit=500` fetch calls replaced with `limit=200` — matches server-enforced `le=200` constraint
- **API-07** `services.py`: CANCELLED guard added in `start_job_execution`, `_fail_job`, and `_execute_dry_run` — terminal CANCELLED state cannot be overwritten by a concurrent execution thread setting COMPLETED or FAILED

### Batch 12 — MEDIUM findings (API-18, API-20, SEC-18, SEC-19, FE-10, FE-11, FE-12)
- **API-18** `models.py` + `main.py`: `tax_year: int = Field(..., ge=2009, le=2100)` in both `JobInput` and `WorkspaceRunRequest`; `Field` added to pydantic imports in `main.py`
- **API-20** `main.py`: `Path(p).read_text()` calls in `async def get_alerts()` wrapped with `await asyncio.to_thread(Path(p).read_text, encoding="utf-8")` — event loop no longer blocked by synchronous file I/O
- **SEC-18** `Dockerfile` + `ui/index.html`: `ARG TAILWIND_VERSION=3.4.17` + Python `urllib` download baked into Docker image; `<link href="tailwind.min.css">` replaces `<script src="cdn.tailwindcss.com">`; `onerror` CDN fallback for local dev
- **SEC-19** `dedup.py`: `"db_path"` key removed from both `list_dedup_sources()` and `get_dedup_summary()` response dicts — absolute server filesystem paths no longer exposed to callers
- **FE-10** `ui/index.html`: `loadAlerts()` now does `const r = await fetch(...); if (!r.ok) throw new Error(...)` before calling `.json()` — server 500 no longer silently shows "All clear"
- **FE-11** `ui/index.html`: `openResultsById()` injects a visible error message into `#results-panel` on HTTP error or network failure — replaces silent `if (!r.ok) return;` / empty `catch {}`
- **FE-12** `ui/index.html`: `runReport()` sets `dryEl.checked = false` after `successJob` is confirmed — prevents accidental dry-run re-submission

---

## Test Counts

| Batch | New tests | Cumulative orchestrator total |
|-------|-----------|-------------------------------|
| Pre-audit baseline | — | 163 |
| Batches 1–6 | ~40 | ~203 |
| Batch 7 | 4 (+ui fixes) | ~203 |
| Batch 8 | 18 | ~221 |
| Batch 9 | 10 | ~231 |
| Batch 10 | 19 | ~250 |
| Batch 11 | 22 | ~609 |
| Batch 12 | 24 | **633 passing** |
| Batches 19+20 | 33+22 | **858 passing** |
| Batch 21 | 37 | **917 passing** |
| Batches 22–25 | 18+41+24+19 | **1019 passing** |
| Batch 26 | 38 | **1057 passing** |
| Batch 27 | 29 | **1086 passing** |
| Batch 28 | 24 | **1110 passing** |
| Batch 29 | 43 | **1153 passing** |

---

## Final Status — All Actionable Findings Closed

All 139 findings from the 2026-03-16 audit have been triaged and closed.

| Severity | Total | Closed | Notes |
|----------|-------|--------|-------|
| CRITICAL | 0 | 0 | — |
| HIGH | 26 | 26 | All closed by Batch 12 |
| MEDIUM | 56 | 56 | All closed by Batch 29 |
| LOW | 45 | 45 | All closed by Batch 29 |
| INFO / MISSING TEST | 12 | 12 | All closed |

**TL-03** (RF-1159 income section omits staking/airdrop income) — closed as
out-of-scope for the orchestrator.  This requires changes to the upstream
`tax_spine` CLI package.  A draft disclaimer and TL-06 warning in the job
output inform users of the limitation until the upstream fix is available.

**INFRA-04** — closed: Dockerfile already uses `requirements.lock` (confirmed
present since early build; comment + COPY instruction verified in Dockerfile).

**INFRA-11** — closed: Watchtower is now in run-once mode (`WATCHTOWER_RUN_ONCE:
"true"`, `restart: "no"`) — the 5-minute polling concern is moot.

**INFRA-13** — closed: `CORS_ORIGINS` override documented in README production
deployment checklist (SEC-10 section).

**INFRA-15** — closed: the `build-and-push` CI job runs on all PR events
(builds but does not push), providing a broken-Dockerfile signal on every PR.
