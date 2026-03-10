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
| POST   | `/jobs/{job_id}/attach-csv`   | Attach uploaded CSVs to a pending job|
| GET    | `/jobs/{job_id}/files`        | List output files for a job          |
| GET    | `/jobs/{job_id}/files/{kind}` | Download a single output file        |
| POST   | `/uploads/csv`                | Upload a CSV file                    |

### Example — create and start a job

```bash
# 1. Create (case_name and dry_run are optional)
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "xrpl_accounts": ["rEXAMPLE1"],
    "tax_year": 2025,
    "country": "norway",
    "csv_files": ["data/generic-events-2025.csv"],
    "case_name": "2025 Norway – main wallets",
    "dry_run": false
  }'

# 2. Start (replace JOB_ID with the id from step 1)
curl -s -X POST http://localhost:8000/jobs/JOB_ID/start
```

### Filtering, sorting, and paging

`GET /jobs` accepts optional query parameters for filtering, sorting, and
paging.  Results are always sorted by `created_at` descending (newest
first).

| Parameter | Type   | Default | Description                                  |
|-----------|--------|---------|----------------------------------------------|
| `status`  | enum   | —       | Filter by job status                         |
| `country` | enum   | —       | Filter by country                            |
| `query`   | string | —       | Case-insensitive substring match on case_name|
| `limit`   | int    | 50      | Max results (1–200)                           |
| `offset`  | int    | 0       | Number of results to skip                    |

```bash
# Only completed jobs
curl -s 'http://localhost:8000/jobs?status=completed'

# Only Norway jobs
curl -s 'http://localhost:8000/jobs?country=norway'

# Combine filters
curl -s 'http://localhost:8000/jobs?status=failed&country=uk'

# Free-text search against case_name
curl -s 'http://localhost:8000/jobs?query=wallets'

# Paging: first page of 10
curl -s 'http://localhost:8000/jobs?limit=10'

# Paging: second page of 10
curl -s 'http://localhost:8000/jobs?limit=10&offset=10'
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

Jobs also accept:

- **`case_name`** (optional string) — a human-friendly label
  (e.g. `"2025 Norway – main wallets"`) for dashboard display and
  free-text filtering.  Defaults to `null`.
- **`dry_run`** (optional bool, default `false`) — when `true`, the job
  skips actual CLI execution and only writes an execution log listing
  the commands that *would* have been run.  Useful for testing and
  previewing the pipeline.
- **`valuation_mode`** (optional enum, default `"dummy"`) — controls how
  the tax CLIs value assets.  See [Valuation mode](#valuation-mode).
- **`csv_prices_path`** (optional string, default `null`) — path to a
  CSV price table on disk.  Only used when `valuation_mode` is
  `"price_table"`.

Every job response includes **`created_at`** and **`updated_at`**
timestamps (UTC, ISO-8601).  `created_at` is set once on creation;
`updated_at` is refreshed whenever the job status or output changes.

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

### Dry-run mode

Set `"dry_run": true` when creating a job to preview the pipeline
without executing any CLI commands:

```bash
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "xrpl_accounts": ["rEXAMPLE1"],
    "tax_year": 2025,
    "country": "norway",
    "dry_run": true
  }'

# Start as usual
curl -s -X POST http://localhost:8000/jobs/JOB_ID/start
```

The resulting execution log will contain entries like:

```
[DRY RUN] — no subprocesses will be executed.
[would run] $ blockchain-reader --mode scenario --xrpl-account rEXAMPLE1 --output …/events.json
[would run] $ taxspine-nor-report --xrpl-scenario …/events.json --tax-year 2025 …
```

The job completes with `status=completed` and only `log_path` set.
Gains, wealth, and summary paths remain `null`.

If the job has no inputs (`xrpl_accounts=[]` and `csv_files=[]`), it
still fails even in dry-run mode — there is nothing useful to preview.

### Valuation mode

By default jobs use `valuation_mode: "dummy"`, which relies on the
built-in default valuation in the tax CLIs (no extra flags are passed).

Set `valuation_mode: "price_table"` to instruct the tax CLIs to use an
external CSV price table.  You must also provide `csv_prices_path`
pointing to an existing file on disk.  The orchestrator verifies the
file exists but does not validate its contents — it is passed as-is
via `--csv-prices <path>` to the tax CLI.

```bash
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "xrpl_accounts": ["rEXAMPLE1"],
    "tax_year": 2025,
    "country": "norway",
    "csv_files": [],
    "case_name": "2025 NO with price table",
    "valuation_mode": "price_table",
    "csv_prices_path": "/tmp/prices-2025-nok.csv"
  }'
```

Failure scenarios:

- `valuation_mode=price_table` without `csv_prices_path` → FAILED
  (`"valuation_mode=price_table requires csv_prices_path"`).
- `csv_prices_path` points to a non-existent file → FAILED
  (`"CSV price table not found: <path>"`).

In dry-run mode the `--csv-prices` flag appears in the logged
would-be commands but no subprocess is called.

### Error handling

| Condition                   | Behaviour                                           |
|-----------------------------|-----------------------------------------------------|
| No inputs at all            | FAILED — `"job has no inputs …"`                    |
| CSV file not found          | FAILED — `"CSV file not found: <path>"`             |
| price_table without path    | FAILED — `"valuation_mode=price_table requires …"` |
| CSV price table not found   | FAILED — `"CSV price table not found: <path>"`      |
| blockchain-reader fails     | FAILED — `"blockchain-reader failed (rc=N)"`        |
| Tax CLI fails               | FAILED — `"tax report CLI failed (rc=N)"`           |

In all failure cases `job.output.log_path` points to an `execution.log`
with captured commands and stderr.

### Configuration

CLI paths and working directories are configured via environment variables:

| Variable                   | Default                              |
|----------------------------|--------------------------------------|
| `TEMP_DIR`                 | `/tmp/taxspine_orchestrator/tmp`     |
| `OUTPUT_DIR`               | `/tmp/taxspine_orchestrator/output`  |
| `UPLOAD_DIR`               | `/tmp/taxspine_orchestrator/uploads` |
| `BLOCKCHAIN_READER_CLI`    | `blockchain-reader`                  |
| `TAXSPINE_NOR_REPORT_CLI`  | `taxspine-nor-report`                |
| `TAXSPINE_UK_REPORT_CLI`   | `taxspine-uk-report`                 |

## Uploading CSVs

The orchestrator can accept CSV files over HTTP so that dashboard users
don't need direct filesystem access.  Upload a file, then reference its
path in `csv_files` when creating a job (or attach it afterwards).

### Upload a CSV

```bash
curl -F "file=@generic-events-2025.csv" http://localhost:8000/uploads/csv
```

Example response:

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "path": "/tmp/taxspine_orchestrator/uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890.csv",
  "original_filename": "generic-events-2025.csv"
}
```

Use the returned `path` value in `JobInput.csv_files` — either when
creating a job or via the attach endpoint below.

Content-type validation is intentionally lenient: `text/csv`,
`application/vnd.ms-excel`, and `application/octet-stream` are all
accepted.  Obviously wrong types (e.g. `image/*`) are rejected with
a `400`.

### Attach CSVs to an existing job

```bash
curl -s -X POST http://localhost:8000/jobs/JOB_ID/attach-csv \
  -H "Content-Type: application/json" \
  -d '{
    "csv_paths": [
      "/tmp/taxspine_orchestrator/uploads/a1b2c3d4-....csv"
    ]
  }'
```

Behaviour:

- Only works for **PENDING** jobs (returns `400` otherwise).
- Each path must point to an existing file (returns `400` if any is
  missing).
- Paths already present in `csv_files` are not duplicated.
- Returns the updated job.

This is a convenience endpoint for the dashboard.  Users can still
supply `csv_files` directly in the initial `POST /jobs` body.

## Non-goals (current scope)

- No background workers or async queues (execution is synchronous).
- No authentication or multi-tenant concerns.
- No database (in-memory store).
- No CSV schema validation (CSVs are treated as opaque files).
