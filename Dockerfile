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
# INFRA-03: The base image is parameterised via PYTHON_IMAGE so that CI can
# pass a fully content-addressable digest for reproducible, supply-chain-safe
# builds.  For local / quick builds the mutable tag is acceptable.
#
# To pin to a digest for production:
#   1. docker pull python:3.11.9-slim
#   2. docker inspect --format='{{index .RepoDigests 0}}' python:3.11.9-slim
#   3. Pass the result as a build-arg:
#        docker build --build-arg PYTHON_IMAGE=python:3.11.9-slim@sha256:<digest> .
#
# The GitHub Actions workflow should fetch and pass this digest automatically.
# Default retains the mutable tag for developer convenience; MUST be overridden
# in any production / CI build.
ARG PYTHON_IMAGE=python:3.11.9-slim
FROM ${PYTHON_IMAGE}

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
        gosu \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps: orchestrator ─────────────────────────────────────────────────
# INFRA-04: copy the lockfile first and install exact pinned versions before
# installing the orchestrator package itself.  This ensures every Docker image
# build uses the same transitive dependency tree regardless of upstream releases,
# eliminating the "works on Monday, breaks on Tuesday" failure mode caused by a
# floating-version pip install silently upgrading a transitive dep.
#
# To update the lockfile after changing pyproject.toml:
#   pip install -e ".[dev]" && pip freeze | grep -v taxspine-orchestrator > requirements.lock
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
# Now install the orchestrator package itself (deps already satisfied above).
COPY pyproject.toml .
COPY taxspine_orchestrator/ ./taxspine_orchestrator/
RUN pip install --no-cache-dir --no-deps .

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
# TAXNOR_TAG: git ref to install — commit SHA, tag, or branch (default main).
# The CI workflow passes the HEAD SHA of tax-nor so builds are deterministic.
# TAXNOR_SHA: same SHA, used only for Docker layer cache-busting.
ARG TAXNOR_TAG=main
ARG TAXNOR_SHA=unknown
# SEC-04: version info is recorded as image LABEL metadata (accessible via
# `docker inspect`) rather than echoed to build output (which would appear in
# CI/CD logs and help an attacker identify pinned dependency versions).
LABEL taxnor.tag="${TAXNOR_TAG}" taxnor.sha="${TAXNOR_SHA}"
RUN --mount=type=secret,id=gh_token,required=false \
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
# Default is "main" so the ARG name is always defined, but the RUN step below
# hard-fails when it is left as "main".  Always pass a commit SHA in production.
# For local dev builds use Dockerfile.local (installs from vendor/, no SHA needed).
ARG BLOCKCHAIN_READER_SHA=main
# INFRA-01: Fail hard when BLOCKCHAIN_READER_SHA is left as the default "main".
# For local development use Dockerfile.local (installs from vendor/ directory,
# no git URL, no SHA required).  For CI/production, the GitHub Actions workflow
# fetches the HEAD SHA automatically and passes it via --build-arg.
RUN --mount=type=secret,id=gh_token,required=false \
    echo "# blockchain-reader pinned ref: ${BLOCKCHAIN_READER_SHA}" && \
    if [ "${BLOCKCHAIN_READER_SHA}" = "main" ]; then \
        echo "ERROR: BLOCKCHAIN_READER_SHA must be set to a commit SHA for production builds." >&2 && \
        echo "       Pass --build-arg BLOCKCHAIN_READER_SHA=<full-40-char-sha> to pin the install." >&2 && \
        echo "       For local dev builds, use Dockerfile.local instead (installs from vendor/)." >&2 && \
        exit 1; \
    fi && \
    TOKEN=$(cat /run/secrets/gh_token 2>/dev/null || echo "") && \
    if [ -n "$TOKEN" ]; then \
        pip install --no-cache-dir "git+https://${TOKEN}@github.com/BWCoast/blockchain-reader.git@${BLOCKCHAIN_READER_SHA}"; \
    else \
        pip install --no-cache-dir "git+https://github.com/BWCoast/blockchain-reader.git@${BLOCKCHAIN_READER_SHA}"; \
    fi

# ── INFRA-10: remove build-time OS deps ───────────────────────────────────────
# build-essential and git were needed only to compile native extensions and to
# install packages directly from GitHub via pip.  Both tasks are now complete,
# so purging them shrinks the final image and reduces the attack surface of the
# running container.  --auto-remove also removes any automatically-installed
# packages that are no longer required.
RUN apt-get purge -y --auto-remove build-essential git \
    && rm -rf /var/lib/apt/lists/*

# ── Application source ────────────────────────────────────────────────────────
# Copied after deps so that source edits don't invalidate the dep layers.
COPY ui/         ./ui/
COPY main.py     .
COPY scripts/    ./scripts/

# ── SEC-18: self-host Tailwind CSS ────────────────────────────────────────────
# Tailwind CSS v3 no longer ships a prebuilt dist/tailwind.min.css in the npm
# package — that file was dropped after v2.  Downloading from jsDelivr returns
# HTTP 404 regardless of version.
#
# Instead we use the official standalone Tailwind CLI binary (published on
# GitHub Releases) to generate a minified CSS file scanned from index.html.
# This keeps the file locally-served (no external CDN on each page load) while
# including only the utility classes actually used in the markup.
#
# The CLI binary is deleted after the build step so it does not appear in the
# final image.  Pin the version deliberately when upgrading Tailwind.
ARG TAILWIND_VERSION=3.4.17
RUN python3 -c "\
import urllib.request, os, stat, subprocess, sys, shutil; \
cli = '/tmp/tailwindcss-cli'; \
url = f'https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-x64'; \
print(f'Downloading Tailwind CLI {url}', file=sys.stderr); \
urllib.request.urlretrieve(url, cli); \
os.chmod(cli, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH); \
subprocess.run([cli, '--content', '/app/ui/index.html', '--output', '/app/ui/tailwind.min.css', '--minify'], check=True); \
os.unlink(cli); \
print('tailwind.min.css generated', file=sys.stderr) \
"

# ── Runtime configuration ─────────────────────────────────────────────────────
# All paths resolve inside /data which should be a bind-mount or named volume.
# Override any of these via environment variables or docker-compose.
ENV OUTPUT_DIR=/data/output \
    TEMP_DIR=/data/tmp \
    UPLOAD_DIR=/data/uploads \
    DATA_DIR=/data/state \
    LOT_STORE_DB=/data/state/lots.db \
    DEDUP_DIR=/data/state/dedup \
    PRICES_DIR=/data/prices

# INFRA-07: Run as a non-root user (UID 1000) to limit the blast radius of a
# container escape and avoid creating root-owned files on bind-mounted volumes.
#
# The entrypoint.sh script runs as root, (re-)creates the /data subdirectories,
# chowns them to app:app, then execs the CMD as the app user via gosu.
# This handles the common case where /data is a bind-mount owned by root on the
# host — the Dockerfile-time mkdir/chown would be masked by the mount.
RUN useradd -r -u 1000 -s /sbin/nologin -d /app app \
    && chown -R app:app /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

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
