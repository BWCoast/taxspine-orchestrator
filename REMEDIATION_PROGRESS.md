# Audit Remediation Progress

**Audit:** Full-spectrum codebase audit (2026-03-16) — 139 findings across 7 domains
**Remediation window:** 18 batches, one batch per session
**Last updated:** 2026-03-17

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
| 12–18 | Remaining MEDIUM/LOW/INFO/MISSING TEST | 🔲 Pending | — | — |

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
| Batch 11 | 22 | **609 passing** |

---

## Remaining Work (Batches 12–18)

Approximate distribution of open findings:

| Severity | Open | Domain focus |
|----------|------|--------------|
| HIGH     | ~10  | LC (legal), TL (tax law), BE (backend) |
| MEDIUM   | ~40  | Mixed |
| LOW      | ~40  | Mixed |
| INFO     | ~4   | Backend |
| MISSING TEST | ~4 | Backend |

Next batch will target remaining HIGH findings not yet addressed.
