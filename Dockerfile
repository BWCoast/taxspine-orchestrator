# ── Taxspine Orchestrator — Production Dockerfile ────────────────────────────
#
# Build:  docker build -t taxspine-orchestrator .
# Run:    docker run -p 8000:8000 -v taxspine-data:/data taxspine-orchestrator
#
# Or use docker-compose.yml for the full stack.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Keeps Python from generating .pyc files and enables unbuffered log output.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install OS deps (needed by uvicorn standard extras).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (Docker layer cache).
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy the application code.
COPY . .

# Runtime directories — override via env vars or volume mounts in production.
ENV OUTPUT_DIR=/data/output \
    TEMP_DIR=/data/tmp \
    UPLOAD_DIR=/data/uploads \
    DATA_DIR=/data/state

# Expose the API port.
EXPOSE 8000

# Start server.  No --reload in production.
CMD ["uvicorn", "taxspine_orchestrator.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1"]
