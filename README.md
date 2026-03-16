# taxspine-orchestrator

Internal API and dashboard for creating, tracking, and executing tax-computation
jobs that coordinate `blockchain-reader` and `taxspine-*` CLI pipelines.

## Quick start

```bash
# Create a virtual environment and install
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run the dev server
uvicorn taxspine_orchestrator.main:app --reload

# Run tests
pytest
```

Open `ui/index.html` in a browser (or serve it statically) to use the dashboard.

---

## API overview

| Method | Path                            | Description                                 |
|--------|---------------------------------|---------------------------------------------|
| GET    | `/health`                       | Health check (liveness probe)               |
| GET    | `/alerts`                       | Active alerts across all jobs               |
| POST   | `/jobs`                         | Create a new tax job                        |
| GET    | `/jobs`                         | List jobs (filters, sort, paging)           |
| GET    | `/jobs/{id}`                    | Get a single job by ID                      |
| POST   | `/jobs/{id}/start`              | Execute the job (async background)          |
| POST   | `/jobs/{id}/cancel`             | Cancel a pending or running job             |
| DELETE | `/jobs/{id}`                    | Delete a job record (non-running only)      |
| POST   | `/jobs/{id}/attach-csv`         | Attach uploaded CSVs to a pending job       |
| GET    | `/jobs/{id}/files`              | Map of output file kinds → paths            |
| GET    | `/jobs/{id}/files/{kind}`       | Download a single output file               |
| GET    | `/jobs/{id}/reports`            | List all HTML report files for a job        |
| GET    | `/jobs/{id}/reports/{index}`    | Download HTML report by index               |
| GET    | `/jobs/{id}/review`             | Norway review summary (unlinked transfers…) |
| POST   | `/uploads/csv`                  | Upload a CSV file                           |
| GET    | `/workspace`                    | Get current workspace config                |
| POST   | `/workspace/accounts`           | Add an XRPL account to the workspace        |
| DELETE | `/workspace/accounts/{account}` | Remove an XRPL account                      |
| POST   | `/workspace/csv`                | Add a CSV file to the workspace             |
| DELETE | `/workspace/csv`                | Remove a CSV file from the workspace        |
| POST   | `/workspace/run`                | Create + start a job from workspace config  |
| GET    | `/lots`                         | Carry-forward lot store summary             |
| GET    | `/dedup`                        | Dedup store health and stats                |
| GET    | `/prices/fetch`                 | Fetch NOK price table for a tax year        |

---

## Authentication

Set `ORCHESTRATOR_KEY` in the environment to enable API key authentication.
All mutating endpoints (`POST`, `DELETE`) require the key in the
`X-API-Key` header.  Read-only endpoints (`GET`) are always public.

```bash
export ORCHESTRATOR_KEY=my-secret-key
curl -H "X-API-Key: my-secret-key" -X POST http://localhost:8000/jobs ...
```

When `ORCHESTRATOR_KEY` is unset (default for local dev), all endpoints are
unrestricted.

---

## Job lifecycle

```
PENDING ──▶ RUNNING ──▶ COMPLETED   (outputs populated)
   │                └──▶ FAILED      (error_message + log_path set)
   └── cancel ──▶ FAILED
```

- `POST /jobs` creates a job in `PENDING` state.
- `POST /jobs/{id}/start` executes the pipeline in a background thread and
  returns `202` immediately.  Poll `GET /jobs/{id}` or refresh the dashboard.
- Starting a job that is already `RUNNING` returns `409`.
- Starting a job that is already `COMPLETED` or `FAILED` returns `200` with
  the current state unchanged (idempotent).
- On server restart any jobs left in `RUNNING` state are automatically
  marked `FAILED` (crash recovery via `SqliteJobStore`).

---

## Creating and starting a job

```bash
# 1. Create
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

# 3. Poll for completion
curl -s http://localhost:8000/jobs/JOB_ID
```

### Input combinations

| xrpl_accounts | csv_files | Behaviour                                      |
|---------------|-----------|------------------------------------------------|
| non-empty     | empty     | XRPL-only: blockchain-reader + tax CLI         |
| empty         | non-empty | CSV-only: tax CLI only (reader skipped)        |
| non-empty     | non-empty | Combined: reader + tax CLI with both inputs    |
| empty         | empty     | Immediate FAILED — no inputs                   |

### Job fields

| Field             | Type    | Default     | Description                                               |
|-------------------|---------|-------------|-----------------------------------------------------------|
| `xrpl_accounts`   | list    | `[]`        | XRPL r-addresses to fetch                                 |
| `csv_files`       | list    | `[]`        | CSV file specs (`{path, source_type}`) or bare paths      |
| `tax_year`        | int     | required    | Tax year to report (e.g. `2025`)                          |
| `country`         | enum    | required    | `"norway"` or `"uk"`                                      |
| `case_name`       | string  | `null`      | Human-friendly label for dashboard display and filtering  |
| `pipeline_mode`   | enum    | `"per_file"`| `"per_file"` or `"nor_multi"` (Norway CSV jobs only)      |
| `valuation_mode`  | enum    | `"dummy"`   | `"dummy"` or `"price_table"`                              |
| `csv_prices_path` | string  | `null`      | Path to NOK price table CSV (required for `price_table`)  |
| `include_trades`  | bool    | `false`     | Include XRPL DEX swap events (OfferCreate)                |
| `debug_valuation` | bool    | `false`     | Write valuation diagnostics to the execution log          |
| `dry_run`         | bool    | `false`     | Log commands that would run; skip actual CLI execution    |

### Pipeline mode (Norway CSV jobs)

| Mode        | Behaviour                                                           |
|-------------|---------------------------------------------------------------------|
| `per_file`  | Run `taxspine-nor-report` once per CSV file (default)               |
| `nor_multi` | Run `taxspine-nor-multi` once with all files — unified FIFO pool    |

`nor_multi` produces a single combined HTML report and merges FIFO lots
across all sources.  Use it when exchange files must share a common cost
basis.  Has no effect on XRPL jobs or UK jobs.

---

## Filtering, sorting, and paging jobs

`GET /jobs` accepts optional query parameters:

| Parameter | Type   | Default | Description                                  |
|-----------|--------|---------|----------------------------------------------|
| `status`  | enum   | —       | Filter by status (`pending`/`running`/…)     |
| `country` | enum   | —       | Filter by country (`norway`/`uk`)            |
| `query`   | string | —       | Substring match on `case_name` (jobs without a `case_name` are excluded) |
| `limit`   | int    | 50      | Max results (1–200)                          |
| `offset`  | int    | 0       | Number of results to skip                    |

Results are always sorted `created_at` descending (newest first).

```bash
curl -s 'http://localhost:8000/jobs?status=completed&country=norway&limit=10'
curl -s 'http://localhost:8000/jobs?query=main+wallets'
```

---

## Cancelling and deleting jobs

```bash
# Cancel a pending or running job (marks it FAILED)
curl -s -X POST http://localhost:8000/jobs/JOB_ID/cancel

# Delete a job record — only works for non-running jobs; does NOT remove output files
curl -s -X DELETE http://localhost:8000/jobs/JOB_ID
# → {"deleted": true, "id": "JOB_ID"}
```

Attempting to delete a `RUNNING` job returns `409`.  Cancel it first, then
delete.

---

## Output files

### List all files for a job

```bash
curl -s http://localhost:8000/jobs/JOB_ID/files
```

Returns a JSON map of kind → absolute path for every file that was produced.

### Download a file

```bash
curl -OJ http://localhost:8000/jobs/JOB_ID/files/{kind}
```

| Kind      | Content-Type       | Description                                 |
|-----------|--------------------|---------------------------------------------|
| `gains`   | `text/csv`         | Realised gains/losses CSV                   |
| `wealth`  | `text/csv`         | Year-end wealth CSV                         |
| `summary` | `application/json` | Pipeline summary JSON                       |
| `report`  | `text/html`        | First HTML report (backward-compat alias)   |
| `rf1159`  | `application/json` | RF-1159 Altinn export JSON (Norway only)    |
| `review`  | `application/json` | Norway review summary (transfer warnings)   |
| `log`     | `text/plain`       | Execution log                               |

### HTML reports (multiple per job)

Jobs with multiple XRPL accounts or CSV files produce one HTML report each.

```bash
# List all report files
curl -s http://localhost:8000/jobs/JOB_ID/reports

# Download report by index (0-based)
curl -OJ http://localhost:8000/jobs/JOB_ID/reports/0
```

---

## Uploading CSVs

```bash
# Upload a file; use the returned path in csv_files
curl -F "file=@generic-events-2025.csv" http://localhost:8000/uploads/csv
# → {"id": "...", "path": "/tmp/.../uploads/....csv", "original_filename": "..."}

# Attach an uploaded CSV to an existing pending job
curl -s -X POST http://localhost:8000/jobs/JOB_ID/attach-csv \
  -H "Content-Type: application/json" \
  -d '{"csv_paths": ["/tmp/.../uploads/....csv"]}'
```

Accepted MIME types: `text/csv`, `application/vnd.ms-excel`,
`application/octet-stream`.  Obviously wrong types (e.g. `image/*`) return
`400`.

---

## Valuation mode

| Mode          | Behaviour                                                        |
|---------------|------------------------------------------------------------------|
| `dummy`       | Built-in default valuation — no extra flags passed (default)     |
| `price_table` | Passes `--csv-prices <path>` to the tax CLI                      |

When `valuation_mode=price_table` the `csv_prices_path` field is required and
the file must exist on disk.  The orchestrator checks existence but does not
validate contents.

---

## Dry-run mode

Set `"dry_run": true` to preview the pipeline without executing any CLIs:

```bash
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"xrpl_accounts":["rEXAMPLE1"],"tax_year":2025,"country":"norway","dry_run":true}'
curl -s -X POST http://localhost:8000/jobs/JOB_ID/start
```

The execution log will contain `[would run] $ ...` entries for each command
that would have been executed.  The job completes as `COMPLETED` with only
`log_path` set.

---

## Health check

`GET /health` always returns `200` (liveness probe semantics).  The response
body includes diagnostic fields:

```json
{
  "status": "ok",
  "db": "ok",
  "output_dir": "ok",
  "clis": {
    "blockchain-reader": "ok",
    "taxspine-nor-report": "ok",
    "taxspine-uk-report": "ok",
    "taxspine-nor-multi": "ok"
  }
}
```

`status` is `"ok"` when all checks pass, `"degraded"` otherwise.

---

## Configuration

All settings are controlled via environment variables:

| Variable                    | Default                              | Description                        |
|-----------------------------|--------------------------------------|------------------------------------|
| `ORCHESTRATOR_KEY`          | *(unset)*                            | API key — leave unset for open dev |
| `TEMP_DIR`                  | `/tmp/taxspine_orchestrator/tmp`     | Working dir for CLI runs           |
| `OUTPUT_DIR`                | `/tmp/taxspine_orchestrator/output`  | Persisted output files             |
| `UPLOAD_DIR`                | `/tmp/taxspine_orchestrator/uploads` | Uploaded CSV files                 |
| `DATA_DIR`                  | `/tmp/taxspine_orchestrator/data`    | Persistent data (DB, dedup, lots)  |
| `PRICES_DIR`                | `/tmp/taxspine_orchestrator/prices`  | Fetched NOK price tables           |
| `SQLITE_DB`                 | `$DATA_DIR/jobs.db`                  | Job store database                 |
| `LOT_STORE_DB`              | `$DATA_DIR/lots.db`                  | Carry-forward lot store            |
| `DEDUP_DIR`                 | `$DATA_DIR/dedup`                    | Per-source dedup stores            |
| `BLOCKCHAIN_READER_CLI`     | `blockchain-reader`                  | Path or name of blockchain-reader  |
| `TAXSPINE_NOR_REPORT_CLI`   | `taxspine-nor-report`                | Norway per-file CLI                |
| `TAXSPINE_UK_REPORT_CLI`    | `taxspine-uk-report`                 | UK report CLI                      |
| `TAXSPINE_NOR_MULTI_CLI`    | `taxspine-nor-multi`                 | Norway multi-source CLI            |
| `TAXSPINE_XRPL_NOR_CLI`     | `taxspine-xrpl-nor`                  | Norway XRPL CLI                    |

---

## Error handling

| Condition                        | Behaviour                                              |
|----------------------------------|--------------------------------------------------------|
| No inputs (empty accounts + CSV) | FAILED — `"job has no inputs"`                         |
| CSV file not found               | FAILED — `"CSV file not found: <path>"`                |
| `price_table` without path       | FAILED — `"valuation_mode=price_table requires …"`     |
| CSV price table not found        | FAILED — `"CSV price table not found: <path>"`         |
| `blockchain-reader` fails        | FAILED — `"blockchain-reader failed (rc=N)"`           |
| Tax CLI fails                    | FAILED — `"tax report CLI failed (rc=N)"`              |
| Delete a running job             | `409` — cancel the job first                           |
| Start an already-running job     | `409`                                                  |

In all failure cases `job.output.log_path` points to `execution.log` with
captured commands and stderr.
