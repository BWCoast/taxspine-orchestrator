# ── Taxspine Orchestrator — Dockerfile (Option A: CLIs from GitHub) ───────────
#
# This is the standard production Dockerfile.  It installs the tax-spine CLIs
# (taxspine-xrpl-nor, taxspine-nor-report) directly from the public GitHub repo
# at build time.
#
# Build:
#   docker build -t taxspine-orchestrator .
#
# If tax-nor is a private repo, pass a GitHub PAT at build time:
#   docker build --secret id=gh_token,src=.gh_token -t taxspine-orchestrator .
#   (create .gh_token containing your PAT — never commit this file)
#
# Or use docker-compose.yml / docker-compose.synology.yml.
# ─────────────────────────────────────────────────────────────────────────────

# syntax=docker/dockerfile:1
# F-16: Pin to a specific patch release for reproducible builds.
# To get the full content-addressable digest (supply-chain pinning), run:
#   docker pull python:3.11.9-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.11.9-slim
# Then replace the FROM line with:
#   FROM python:3.11.9-slim@sha256:<digest>
FROM python:3.11.9-slim

# Keeps Python from generating .pyc files and enables unbuffered log output.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── OS deps ───────────────────────────────────────────────────────────────────
# build-essential: needed by some native extensions
# git:             needed by pip to install directly from GitHub
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps: orchestrator ─────────────────────────────────────────────────
# Copy only the dependency manifest first so Docker can cache this layer
# independently of source changes.
COPY pyproject.toml .
COPY taxspine_orchestrator/ ./taxspine_orchestrator/
RUN pip install --no-cache-dir .

# ── Python deps: tax-spine CLIs ───────────────────────────────────────────────
# Installs taxspine-xrpl-nor, taxspine-nor-report, taxspine-uk-report
# from the tax-nor repository.
#
# Works for both public and private repos:
#   - Public:  no secret needed; leave GH_READ_TOKEN unset.
#   - Private: set GH_READ_TOKEN as a GitHub Actions secret (or --secret at
#              local build time). The token is injected via BuildKit secret
#              mount and is NEVER baked into any image layer or docker history.
#
# Local build with secret file:
#   echo "ghp_yourtoken" > .gh_token
#   docker build --secret id=gh_token,src=.gh_token -t taxspine-orchestrator .
#   rm .gh_token
#
# Cache-busting: TAXNOR_SHA is the HEAD commit SHA of the tax-nor repo,
# fetched by the GitHub Actions workflow before the build.  When tax-nor
# gets new commits, the SHA changes → Docker cannot reuse the cached layer
# → pip installs the fresh version.  Defaults to "unknown" for local builds
# where the SHA is not passed (triggering a fresh install every time locally).
# TAXNOR_TAG: semver tag to install (default v0.1.0).  Override at build time
# to deploy a different release:  --build-arg TAXNOR_TAG=v0.2.0
# TAXNOR_SHA: HEAD commit SHA used only for Docker layer cache-busting.
ARG TAXNOR_TAG=v0.1.0
ARG TAXNOR_SHA=unknown
RUN --mount=type=secret,id=gh_token,required=false \
    echo "# tax-nor tag: ${TAXNOR_TAG}  HEAD: ${TAXNOR_SHA}" && \
    TOKEN=$(cat /run/secrets/gh_token 2>/dev/null || echo "") && \
    if [ -n "$TOKEN" ]; then \
        pip install --no-cache-dir "git+https://${TOKEN}@github.com/BWCoast/tax-nor.git@${TAXNOR_TAG}"; \
    else \
        pip install --no-cache-dir "git+https://github.com/BWCoast/tax-nor.git@${TAXNOR_TAG}"; \
    fi

# ── Python deps: blockchain-reader ────────────────────────────────────────────
# Provides the blockchain_reader Python package, which is imported by
# taxspine-xrpl-nor at runtime to fetch XRPL account transactions.
# Must be installed AFTER tax-spine (blockchain-reader depends on tax-spine).
#
# INFRA-01 (supply-chain pin): BLOCKCHAIN_READER_SHA must be set to a full
# 40-character (or at minimum 12-character) commit hash from the
# BWCoast/blockchain-reader repository before any production build.
#
# The GitHub Actions workflow fetches the current HEAD SHA automatically and
# passes it here via --build-arg.  For local/manual builds, look up the
# desired commit at:
#   https://github.com/BWCoast/blockchain-reader/commits/main
# then pass:
#   docker build --build-arg BLOCKCHAIN_READER_SHA=<full-sha> ...
#
# Default "main" retains backward-compat for quick local dev builds but
# MUST NOT be used in production — it tracks the floating branch tip.
ARG BLOCKCHAIN_READER_SHA=main
RUN --mount=type=secret,id=gh_token,required=false \
    echo "# blockchain-reader pinned ref: ${BLOCKCHAIN_READER_SHA}" && \
    if [ "${BLOCKCHAIN_READER_SHA}" = "main" ]; then \
        echo "WARNING: BLOCKCHAIN_READER_SHA not pinned — using floating 'main' branch. Set a commit SHA for production builds." >&2; \
    fi && \
    TOKEN=$(cat /run/secrets/gh_token 2>/dev/null || echo "") && \
    if [ -n "$TOKEN" ]; then \
        pip install --no-cache-dir "git+https://${TOKEN}@github.com/BWCoast/blockchain-reader.git@${BLOCKCHAIN_READER_SHA}"; \
    else \
        pip install --no-cache-dir "git+https://github.com/BWCoast/blockchain-reader.git@${BLOCKCHAIN_READER_SHA}"; \
    fi

# ── Application source ────────────────────────────────────────────────────────
# Copied after deps so that source edits don't invalidate the dep layers.
COPY ui/         ./ui/
COPY main.py     .
COPY scripts/    ./scripts/

# ── Runtime configuration ─────────────────────────────────────────────────────
# All paths resolve inside /data which should be a bind-mount or named volume.
# Override any of these via environment variables or docker-compose.
ENV OUTPUT_DIR=/data/output \
    TEMP_DIR=/data/tmp \
    UPLOAD_DIR=/data/uploads \
    DATA_DIR=/data/state \
    LOT_STORE_DB=/data/state/lots.db \
    DEDUP_DIR=/data/state/dedup

# ── Port ─────────────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Healthcheck ───────────────────────────────────────────────────────────────
# Uses Python's built-in urllib — no curl required (works on Synology too).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request, sys; \
         r = urllib.request.urlopen('http://localhost:8000/health'); \
         sys.exit(0 if r.status == 200 else 1)"

# ── Start ─────────────────────────────────────────────────────────────────────
# Single worker — SQLite is not safe with multiple concurrent writers.
CMD ["python", "-m", "uvicorn", "taxspine_orchestrator.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
