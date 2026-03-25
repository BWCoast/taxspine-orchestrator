# CI Build Gotchas — Lessons Learned

> **This document is the pre-push checklist.**
> Reference it (and run `.githooks/pre-push`) before every `git push` to `main`.
> Last updated: 2026-03-25

---

## Quick Checklist (run before every push)

```
[ ] 1. requirements.lock was regenerated from a CLEAN venv (see §1)
[ ] 2. requirements.lock cross-checked against pyproject.toml (see §1.3)
[ ] 3. No forbidden packages in lockfile: hf-xet, huggingface_hub, annotated-doc,
        weasyprint, hypothesis, rich, typer, huggingface* (see §1.4)
[ ] 4. GH_READ_TOKEN is valid and has `repo` scope (see §2)
[ ] 5. Local docker.yml changes are committed — not diverged from HEAD (see §3)
[ ] 6. Local Dockerfile changes are committed — not diverged from HEAD (see §3)
[ ] 7. TAILWIND_VERSION ARG in Dockerfile matches an existing GitHub release (see §4)
[ ] 8. Local test failures in TestPriceTableMissingPath are expected — not regressions (see §5)
[ ] 9. Python version compat: every pinned package has a Python 3.11 Linux amd64 wheel (see §6)
```

Run `.githooks/pre-push` to automate checks 1, 3, 5, 6, 7.

---

## §1 — requirements.lock Pollution

### What happened
The lockfile was generated from a **polluted venv** that had packages installed beyond
what `pyproject.toml` declares. Running `pip freeze` captured everything — including
Hugging Face tooling, Weasyprint, and a likely typo-squatting package — none of which
the orchestrator needs.

### Why it breaks Docker builds
The Dockerfile installs `pip install -r requirements.lock` on **Python 3.11 Linux amd64**.
Packages like `hf-xet` are Rust extensions with no pre-built wheel for that platform.
Without Rust in the Docker image, pip tries to compile from source and fails.

### How to regenerate cleanly

```bash
# Always use a fresh throwaway venv — never the project dev venv
python3.11 -m venv /tmp/fresh-orchestrator-venv
/tmp/fresh-orchestrator-venv/bin/pip install --upgrade pip
/tmp/fresh-orchestrator-venv/bin/pip install -e ".[dev]"
/tmp/fresh-orchestrator-venv/bin/pip freeze \
  | grep -v "^taxspine-orchestrator" \
  > requirements.lock
```

Then update the header comment with today's date and Python version.

### Cross-check after regeneration

Every package in `requirements.lock` must be reachable (directly or transitively)
from `pyproject.toml`. The legitimate direct deps are:

```
fastapi, uvicorn, pydantic, pydantic-settings, python-multipart   # runtime
pytest, httpx, ruff                                                  # dev
```

All other entries must be traceable as transitive deps of the above.

### Known forbidden packages (must never appear in lockfile)

| Package | Why forbidden |
|---------|--------------|
| `hf-xet` | Rust extension; no Python 3.11 Linux wheel; pulled in by `huggingface_hub` |
| `huggingface_hub` | Not an orchestrator dep; brings in hf-xet, fsspec, filelock, tqdm |
| ~~`annotated-doc`~~ | **Legitimate** — `fastapi/annotated-doc` by tiangolo; required by fastapi. Allow it. |
| `weasyprint` | Heavy PDF library not used by orchestrator (we use `fpdf2`) |
| `zopfli` | C extension used only by weasyprint |
| `hypothesis` | Property-based testing library; orchestrator does not use it — but tax-nor (Project F) does. Allow it in tax-nor's `requirements-dev.lock`; forbidden in orchestrator's `requirements.lock`. |
| `rich` | Pretty-printing CLI lib; not a dep |
| `typer` | CLI framework; not a dep |
| `shellingham` | typer transitive dep; not a dep |

If any of these appear after regeneration, stop and investigate before pushing.

---

## §2 — GH_READ_TOKEN Expiry

### What happens when the token is expired
1. The workflow SHA-fetch step falls back silently: tax-nor SHA → `"unknown"`, blockchain-reader SHA → `"main"`
2. The Dockerfile receives `TAXNOR_TAG=unknown` → tries `pip install git+https://...@unknown` → **fails** (no such ref)
3. Or receives `BLOCKCHAIN_READER_SHA=main` → tries pip install from floating `main` with an expired token → **auth failure**

Both produce confusing error messages deep in the Docker build log, far from the actual cause.

### How to check the token

```bash
curl -sf -H "Authorization: Bearer $GH_READ_TOKEN" \
  "https://api.github.com/repos/BWCoast/tax-nor/commits/main" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sha','ERROR:',d.get('message','')))"
```

A valid token returns a 40-char SHA. An expired/invalid token returns a JSON error message.

### Token requirements
- Scope: `repo` (read access to private repos `BWCoast/tax-nor` and `BWCoast/blockchain-reader`)
- Set as GitHub Actions secret: `GH_READ_TOKEN`
- Also set as Docker secret in the workflow (`secrets: GH_READ_TOKEN`)
- Renew before expiry — check at: GitHub → Settings → Developer Settings → Personal access tokens

### Hard-fail guardrails (local docker.yml — must be pushed)
The local (uncommitted) version of `.github/workflows/docker.yml` fails hard if:
- `GH_READ_TOKEN` is not set
- The blockchain-reader SHA cannot be fetched
- The Python base image digest cannot be resolved

**Push these changes before testing CI builds** (see §3).

---

## §3 — docker.yml / Dockerfile Divergence

### The pattern that burned us
Local changes to `.github/workflows/docker.yml` and `Dockerfile` accumulate without
being committed. CI runs the committed versions (with soft fallbacks). Local versions
have hard-fail guardrails that catch errors early. The result: CI fails with confusing
late-stage errors instead of clear early ones.

### Pre-push check

```bash
git diff HEAD -- .github/workflows/docker.yml Dockerfile
```

If this shows non-empty output, the diff has not been committed.
**Do not push application changes without first committing infra changes.**

### What the hardened local versions add (must be committed)

**docker.yml additions:**
- `repository_dispatch: types: [tax-nor-updated]` — rebuild when tax-nor publishes
- `Resolve Python base image digest` step — pins base image to content-addressable digest
- Hard-fail if `GH_READ_TOKEN` is absent or blockchain-reader SHA resolves to "main"
- Passes `PYTHON_IMAGE` build-arg to Dockerfile

**Dockerfile additions:**
- Hard `exit 1` when `BLOCKCHAIN_READER_SHA=main` (was a warning only)
- Accepts `PYTHON_IMAGE` ARG from workflow for fully pinned base image

---

## §4 — Tailwind CLI Download

### The step
The Dockerfile downloads the official Tailwind standalone CLI binary from GitHub Releases:

```
https://github.com/tailwindlabs/tailwindcss/releases/download/v{TAILWIND_VERSION}/tailwindcss-linux-x64
```

### How it can fail
- The `TAILWIND_VERSION` ARG in the Dockerfile does not match any published release
- GitHub Releases returns a 404 or rate-limits the download runner
- The binary URL format changed for a major Tailwind version bump

### How to verify before bumping Tailwind version

```bash
TAILWIND_VERSION=3.4.17   # change to the version in Dockerfile
curl -sfI "https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-x64" \
  | head -1
# Expect: HTTP/2 302  (redirect to CDN) or 200
```

A `404` means the binary does not exist at that URL — check the exact asset name on the
GitHub release page before updating.

---

## §5 — Local-Only Test Failures

### TestPriceTableMissingPath (tests/test_valuation.py)

Two tests fail locally but pass in CI:
- `test_fails_when_csv_prices_path_is_null`
- `test_fails_when_csv_prices_path_explicit_null`

**Root cause:** Local `PRICES_DIR` has a cached `combined_nok_YYYY.csv`. When the test
tries to trigger the "missing CSV path" error path, the auto-resolve logic finds the
cached file and succeeds instead of failing. In CI the runner starts with an empty
`PRICES_DIR`, so the error path is reached correctly.

**These are not regressions.** Do not attempt to fix them by adding more mocking —
the tests are correct, the local environment is the anomaly.

To verify they pass in CI conditions locally, temporarily move the cached file:
```bash
mv $PRICES_DIR/combined_nok_*.csv /tmp/ && python -m pytest tests/test_valuation.py -k "TestPriceTableMissingPath" && mv /tmp/combined_nok_*.csv $PRICES_DIR/
```

---

## §6 — Python Version and Platform Compatibility

### The mismatch
- Local dev environment: Python **3.14** (Windows)
- Docker base image: `python:3.11.9-slim` (Linux amd64)
- CI test runner: Python **3.11** (Ubuntu)

A package that installs fine locally (Python 3.14, Windows, `pip` finds a wheel)
may not install in Docker (Python 3.11, Linux amd64) if:
- The package only has wheels for Python 3.12+ or Python 3.13+
- The package only has Windows or macOS wheels, not `manylinux`
- The package requires a native compiler (Rust, C) that is not in the Docker build stage

### How to check wheel availability

```bash
pip index versions <package>==<version>          # check if version exists
pip download --only-binary=:all: --python-version 311 --platform manylinux_2_28_x86_64 \
  --no-deps "<package>==<version>" -d /tmp/wheel-check/
# If this fails: no pre-built wheel exists for that platform
```

Or check manually at `https://pypi.org/project/<package>/#files` and look for
`cp311-cp311-manylinux*x86_64.whl` or `py3-none-any.whl`.

### Packages that need special attention
- Any package with a C or Rust extension (`.pyd`, `.so` in the wheel)
- New major versions (e.g. `cffi==2.0.0`) — check wheel availability before pinning

---

## §7 — How This All Connected (Root Cause Summary)

**Timeline of the build failure after image #53:**

1. `requirements.lock` contained `hf-xet==1.4.0` — a Rust extension not needed by the orchestrator, pulled in because the lockfile was generated from a polluted venv (one that had `huggingface_hub` installed).
2. The committed `docker.yml` had a soft fallback: if `GH_READ_TOKEN` was expired or the API call failed, `BLOCKCHAIN_READER_SHA` silently became `"main"`.
3. The committed `Dockerfile` only warned (not hard-failed) when `BLOCKCHAIN_READER_SHA=main`, then attempted a pip install from the floating `main` branch — which fails if the token is expired.
4. The local hardened versions of both files were never committed.
5. Subsequent builds failed deep in the Docker layer, with error messages pointing at `pip install` rather than "token expired" or "lockfile polluted."

**Fix requires:**
1. Regenerate `requirements.lock` from a clean Python 3.11 venv
2. Commit the local hardened `docker.yml` and `Dockerfile`
3. Verify `GH_READ_TOKEN` is valid

---

---

## §8 — Intermittent Alerts Test Failures

### Pattern

Tests in `test_alerts.py` and `test_alerts_diagnostics.py` fail intermittently
when the **full suite** is run (`pytest -q`) but pass individually or in isolation.

Known intermittent failures:
- `TestAlertSorting::test_error_before_warn`
- `TestAlertsRaisedAt::test_review_alert_has_raised_at`
- `TestAlertsCategoryGrouping::test_review_alert_category_is_review`

### Root cause

Likely shared state in the in-memory job store or alert aggregation that
bleeds between test classes when the full suite runs in the same process.
`_job_store.clear()` in the `autouse` fixture clears jobs but may not reset
alert aggregation state computed lazily from completed jobs in other test modules.

### How to handle

1. **If these fail in CI on a PR:** re-run the CI job. One-shot reruns almost
   always pass — confirming this is timing/order-dependent, not a real regression.
2. **If they fail consistently (>3 consecutive runs):** investigate shared state
   in `alerts.py` — specifically whether `GET /alerts` reads from a module-level
   cache that is not cleared between tests.
3. **Not caused by:** any of the valuation, provenance, RF-1159 warnings, or
   workspace changes from 2026-03-25.

### Long-term fix (deferred)

Add an explicit alert-state reset to the `autouse` fixture in `conftest.py`,
or refactor `alerts.py` to be stateless (derive alerts from job store each time).

---

*This document lives at `docs/CI_BUILD_GOTCHAS.md`. The pre-push hook at `.githooks/pre-push` automates the mechanical checks.*
