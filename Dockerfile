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
FROM python:3.11-slim

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
# This installs taxspine-xrpl-nor, taxspine-nor-report, taxspine-uk-report
# from the tax-nor repository.  The layer is cached until this RUN line changes.
#
# Public repo (no authentication):
RUN pip install --no-cache-dir \
        "git+https://github.com/BWCoast/tax-nor.git"
#
# Private repo: replace the RUN above with the secret-mount variant below.
# Then build with: docker build --secret id=gh_token,src=.gh_token ...
#
# RUN --mount=type=secret,id=gh_token \
#     TOKEN=$(cat /run/secrets/gh_token) && \
#     pip install --no-cache-dir "git+https://${TOKEN}@github.com/BWCoast/tax-nor.git"

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
    DATA_DIR=/data/state

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
