# taxspine-orchestrator

Internal API for creating, tracking, and executing tax-computation jobs that
coordinate blockchain-reader and taxspine-\* CLI pipelines.

## Quick start

```bash
# Create a virtual environment and install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run the dev server
uvicorn taxspine_orchestrator.main:app --reload

# Run tests
pytest
```

## API overview

| Method | Path                   | Description                       |
|--------|------------------------|-----------------------------------|
| GET    | `/health`              | Health check                      |
| POST   | `/jobs`                | Create a new tax job              |
| GET    | `/jobs`                | List all jobs                     |
| GET    | `/jobs/{job_id}`       | Get a single job by ID            |
| POST   | `/jobs/{job_id}/start` | Execute the job synchronously     |

### Example — create and start a job

```bash
# 1. Create
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "xrpl_accounts": ["rEXAMPLE1"],
    "tax_year": 2025,
    "country": "norway"
  }'

# 2. Start (replace JOB_ID with the id from step 1)
curl -s -X POST http://localhost:8000/jobs/JOB_ID/start
```

## Job execution

A job represents **XRPL accounts + country + tax year → tax report**.

When you `POST /jobs/{id}/start`, the orchestrator runs a synchronous
two-step pipeline:

1. **blockchain-reader** — exports XRPL events for the given accounts into
   an `events.json` file.
2. **Country-specific tax CLI** — reads the events and produces gains CSV,
   wealth CSV, and a summary JSON.

### Norway pipeline

```
blockchain-reader \
    --mode scenario \
    --xrpl-account rACCOUNT1 --xrpl-account rACCOUNT2 \
    --output <work_dir>/events.json

taxspine-nor-report \
    --xrpl-scenario <work_dir>/events.json \
    --tax-year 2025 \
    --gains-csv <work_dir>/gains.csv \
    --wealth-csv <work_dir>/wealth.csv \
    --summary-json <work_dir>/summary.json
```

### UK pipeline

```
blockchain-reader \
    --mode scenario \
    --xrpl-account rACCOUNT1 \
    --output <work_dir>/events.json

taxspine-uk-report \
    --xrpl-scenario <work_dir>/events.json \
    --tax-year 2025 \
    --uk-gains-csv <work_dir>/gains.csv \
    --uk-wealth-csv <work_dir>/wealth.csv \
    --uk-summary-json <work_dir>/summary.json
```

### Job lifecycle

```
PENDING ──▶ RUNNING ──▶ COMPLETED   (outputs populated)
                    └──▶ FAILED      (error_message + log_path set)
```

Starting a non-PENDING job is a no-op — the current state is returned
unchanged.

### Error handling

If either CLI returns a non-zero exit code:

- `job.status` is set to `"failed"`.
- `job.output.error_message` contains a short description (which step
  failed and the return code).
- `job.output.log_path` points to an `execution.log` with the full
  command lines and captured stdout/stderr.

### Configuration

CLI paths and working directories are configured via environment variables:

| Variable                   | Default                              |
|----------------------------|--------------------------------------|
| `TEMP_DIR`                 | `/tmp/taxspine_orchestrator/tmp`     |
| `OUTPUT_DIR`               | `/tmp/taxspine_orchestrator/output`  |
| `BLOCKCHAIN_READER_CLI`    | `blockchain-reader`                  |
| `TAXSPINE_NOR_REPORT_CLI`  | `taxspine-nor-report`                |
| `TAXSPINE_UK_REPORT_CLI`   | `taxspine-uk-report`                 |

## Non-goals (current scope)

- No CSV file ingestion (XRPL-only for now).
- No background workers or async queues (execution is synchronous).
- No authentication or multi-tenant concerns.
- No database (in-memory store).
