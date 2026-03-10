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

| Method | Path                          | Description                          |
|--------|-------------------------------|--------------------------------------|
| GET    | `/health`                     | Health check                         |
| POST   | `/jobs`                       | Create a new tax job                 |
| GET    | `/jobs`                       | List jobs (with optional filters)    |
| GET    | `/jobs/{job_id}`              | Get a single job by ID               |
| POST   | `/jobs/{job_id}/start`        | Execute the job synchronously        |
| GET    | `/jobs/{job_id}/files`        | List output files for a job          |
| GET    | `/jobs/{job_id}/files/{kind}` | Download a single output file        |

### Example — create and start a job

```bash
# 1. Create (case_name is optional — handy for dashboard display)
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "xrpl_accounts": ["rEXAMPLE1"],
    "tax_year": 2025,
    "country": "norway",
    "csv_files": ["data/generic-events-2025.csv"],
    "case_name": "2025 Norway – main wallets"
  }'

# 2. Start (replace JOB_ID with the id from step 1)
curl -s -X POST http://localhost:8000/jobs/JOB_ID/start
```

### Filtering jobs

`GET /jobs` accepts optional query parameters `status`, `country`, and
`query`:

```bash
# Only completed jobs
curl -s 'http://localhost:8000/jobs?status=completed'

# Only Norway jobs
curl -s 'http://localhost:8000/jobs?country=norway'

# Combine filters
curl -s 'http://localhost:8000/jobs?status=failed&country=uk'

# Free-text search against case_name (case-insensitive substring)
curl -s 'http://localhost:8000/jobs?query=wallets'

# All three filters combined
curl -s 'http://localhost:8000/jobs?status=completed&country=norway&query=main'
```

Valid values — `status`: `pending`, `running`, `completed`, `failed`;
`country`: `norway`, `uk`.  Invalid enum values return `422`.
`query` is a free-text substring match against `case_name`; jobs without
a `case_name` are excluded when `query` is provided.

### Listing and downloading output files

```bash
# All output files for a job (JSON map of kind → path)
curl -s http://localhost:8000/jobs/JOB_ID/files

# Download a specific file (gains | wealth | summary | log)
curl -OJ http://localhost:8000/jobs/JOB_ID/files/gains
```

`GET /jobs/{id}/files` returns a JSON map of populated kinds → paths.

`GET /jobs/{id}/files/{kind}` streams the actual file with appropriate
headers:

| Kind      | Content-Type       | Filename pattern           |
|-----------|--------------------|----------------------------|
| `gains`   | `text/csv`         | `gains-<job_id>.csv`       |
| `wealth`  | `text/csv`         | `wealth-<job_id>.csv`      |
| `summary` | `application/json` | `summary-<job_id>.json`    |
| `log`     | `text/plain`       | `log-<job_id>.txt`         |

Returns `404` if the job does not exist, the path is not recorded, or the
file is missing from disk.

## Job execution

A job represents **inputs + country + tax year → tax report**.

Inputs can be any combination of:

- **XRPL accounts** — fetched via `blockchain-reader` into `events.json`.
- **Generic-events CSV files** — already in the canonical CSV schema
  understood by the taxspine CLIs.  Passed through as-is; the orchestrator
  does not parse or validate CSV content.

At least one of `xrpl_accounts` or `csv_files` must be non-empty.  If both
are empty the job fails immediately.

### Input combinations

| xrpl_accounts | csv_files | Behaviour                                     |
|---------------|-----------|-----------------------------------------------|
| non-empty     | empty     | XRPL-only: reader + tax CLI                   |
| empty         | non-empty | CSV-only: tax CLI only (reader skipped)        |
| non-empty     | non-empty | Combined: reader + tax CLI with both inputs    |
| empty         | empty     | Immediate FAILED — no inputs                   |

Jobs also accept an optional `case_name` string — a human-friendly label
(e.g. `"2025 Norway – main wallets"`) that makes it easier to distinguish
jobs in a dashboard.  It has no effect on execution and defaults to `null`.

### Pipeline steps

1. **Validate CSV paths** — each path in `csv_files` is checked for
   existence.  If any file is missing the job fails before any CLI is called.
2. **blockchain-reader** _(XRPL-only / combined)_ — exports XRPL events
   for the given accounts into `events.json`.
3. **Country-specific tax CLI** — processes all inputs and produces gains
   CSV, wealth CSV, and summary JSON.

### Norway — combined example

```
blockchain-reader \
    --mode scenario \
    --xrpl-account rACCOUNT1 \
    --output <work_dir>/events.json

taxspine-nor-report \
    --xrpl-scenario <work_dir>/events.json \
    --generic-events-csv data/generic-events-2025.csv \
    --tax-year 2025 \
    --gains-csv <work_dir>/gains.csv \
    --wealth-csv <work_dir>/wealth.csv \
    --summary-json <work_dir>/summary.json
```

### Norway — CSV-only example

```
taxspine-nor-report \
    --generic-events-csv data/file1.csv \
    --generic-events-csv data/file2.csv \
    --tax-year 2025 \
    --gains-csv <work_dir>/gains.csv \
    --wealth-csv <work_dir>/wealth.csv \
    --summary-json <work_dir>/summary.json
```

### UK pipeline

Uses `taxspine-uk-report` with `--uk-gains-csv`, `--uk-wealth-csv`,
`--uk-summary-json` instead.  The `--xrpl-scenario` and
`--generic-events-csv` flags work the same way.

### Job lifecycle

```
PENDING ──▶ RUNNING ──▶ COMPLETED   (outputs populated)
                    └──▶ FAILED      (error_message + log_path set)
```

Starting a non-PENDING job is a no-op — the current state is returned
unchanged.

### Error handling

| Condition               | Behaviour                                       |
|-------------------------|-------------------------------------------------|
| No inputs at all        | FAILED — `"job has no inputs …"`                |
| CSV file not found      | FAILED — `"CSV file not found: <path>"`         |
| blockchain-reader fails | FAILED — `"blockchain-reader failed (rc=N)"`    |
| Tax CLI fails           | FAILED — `"tax report CLI failed (rc=N)"`       |

In all failure cases `job.output.log_path` points to an `execution.log`
with captured commands and stderr.

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

- No background workers or async queues (execution is synchronous).
- No authentication or multi-tenant concerns.
- No database (in-memory store).
- No CSV schema validation (CSVs are treated as opaque files).
