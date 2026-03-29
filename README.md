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
All endpoints require the key in the `X-Orchestrator-Key` header.

```bash
export ORCHESTRATOR_KEY=my-secret-key
curl -H "X-Orchestrator-Key: my-secret-key" -X POST http://localhost:8000/jobs ...
```

When `ORCHESTRATOR_KEY` is unset (default for local dev), all endpoints are
unrestricted.

---

## Job lifecycle

```
PENDING ──▶ RUNNING ──▶ COMPLETED   (outputs populated)
   │                └──▶ FAILED      (error_message + log_path set)
   │                └──▶ CANCELLED   (user-initiated via /cancel mid-run)
   └── cancel ──▶ CANCELLED
```

- `POST /jobs` creates a job in `PENDING` state.
- `POST /jobs/{id}/start` atomically transitions the job to `RUNNING` via a
  compare-and-swap, then executes the pipeline in a background thread and
  returns `202` immediately.  Poll `GET /jobs/{id}` or refresh the dashboard.
- Starting a job that is already `RUNNING` returns `409`.
- Starting a job that is already `COMPLETED`, `FAILED`, or `CANCELLED` returns
  `200` with the current state unchanged (idempotent).
- `POST /jobs/{id}/cancel` marks the job `CANCELLED` — a distinct terminal
  state from `FAILED` so callers can distinguish user cancellation from errors.
  If execution is in progress, the `CANCELLED` state is preserved when the
  thread finishes (it does not overwrite with `COMPLETED` or `FAILED`).
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
| `valuation_mode`  | enum    | `"price_table"` | `"dummy"` or `"price_table"`                          |
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
# Cancel a pending or running job (marks it CANCELLED)
curl -s -X POST http://localhost:8000/jobs/JOB_ID/cancel

# Delete a job record (also removes output files and input CSVs from disk by default)
curl -s -X DELETE http://localhost:8000/jobs/JOB_ID
# → {"deleted": true, "id": "JOB_ID", "files_removed": 3}

# Delete without removing files
curl -s -X DELETE "http://localhost:8000/jobs/JOB_ID?delete_files=false"
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

| Mode          | Behaviour                                                                    |
|---------------|------------------------------------------------------------------------------|
| `price_table` | **Default.** Auto-resolves `combined_nok_{year}.csv`; fetches from Kraken + Norges Bank if absent. Passes `--csv-prices <path>` to every tax CLI call. |
| `dummy`       | No price lookup. Output marked `draft=true` in RF-1159 `_provenance`. Must not be filed. |

When `valuation_mode=price_table` and `csv_prices_path` is omitted, the orchestrator
auto-resolves `combined_nok_{year}.csv` from `PRICES_DIR`. If it does not exist it
calls `POST /prices/fetch` inline (requires network access to Kraken and Norges Bank).

### Price tiers

| Tier | Source | Assets |
|------|--------|--------|
| 1 | Kraken OHLC × Norges Bank USD/NOK | XRP, BTC, ETH, ADA, LTC |
| 2a | OnTheDEX OHLC (XRP-denominated) | XRPL IOU tokens |
| 2b | XRPL.to OHLC (XRP-denominated, fallback) | XRPL IOU tokens |
| 2c | CoinGecko `market_chart/range?vs_currency=nok` | Any token with a CoinGecko listing |
| 3 | Static USD peg | RLUSD = $1.00 |
| 4 | XRPL AMM `amm_info` year-end NAV | AMM LP tokens |

XRPL tokens held in registered workspace accounts are auto-discovered via `account_lines`
RPC and included in every price fetch without manual registration.

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
| Start an already-running job     | `409` (CAS prevents duplicate starts)                  |
| Cancel a completed/failed job    | `400` — only PENDING and RUNNING jobs can be cancelled |

In all failure cases `job.output.log_path` points to `execution.log` with
captured commands and stderr.

---

## Job output fields

Key fields populated on a `COMPLETED` Norway job:

| Field | Type | Description |
|-------|------|-------------|
| `rf1159_json_path` | string \| null | Path to the primary RF-1159 Altinn export JSON |
| `rf1159_json_paths` | list[string] | All RF-1159 paths (one per CLI invocation) |
| `rf1159_warnings` | list[string] \| null | Filing-completeness warnings extracted from RF-1159 output. `null` = no RF-1159 produced. `[]` = clean filing. Non-empty = must review before filing. |
| `valuation_mode_used` | string | `"price_table"` or `"dummy"` — which engine was used |
| `price_source` | string | `"price_table_csv"` or `"dummy"` |
| `price_table_path` | string \| null | Path to the NOK price CSV that was used |
| `draft_disclaimer` | string \| null | Populated for all Norway RF-1159 jobs; show to users |
| `pipeline_mode_used` | string \| null | `"per_file"` or `"nor_multi"` (Norway only) |
| `log_path` | string | Execution log with all CLI invocations and output |

`rf1159_warnings` examples:

```json
"rf1159_warnings": []                         // clean — safe to review for filing
"rf1159_warnings": [
  "UNRESOLVED COST BASIS: XRP, SOLO. ...",    // missing prices → gains may be wrong
  "UNRESOLVED INCOME: staking rewards ..."    // income totals understated
]
"rf1159_warnings": null                       // no RF-1159 output (UK job, no activity)
```

---

## Production deployment checklist (SEC-03, SEC-08, SEC-10)

Before exposing the orchestrator on any network-reachable host:

### 1. Set an API key (SEC-08)

```bash
# Generate a strong random key
python -c "import secrets; print(secrets.token_urlsafe(32))"

export ORCHESTRATOR_KEY=<generated-value>
```

Without `ORCHESTRATOR_KEY` the entire API is publicly accessible.  The server
logs a `WARNING` at startup when this variable is unset, so check startup logs
before deploying.

### 2. Set allowed CORS origins (SEC-10)

```bash
export CORS_ORIGINS=https://your-dashboard.example.com
```

The default `CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000` blocks
browser requests from any other origin.  Update it to match the actual hostname
where you serve `ui/index.html`.  Note: CORS only restricts *browser* clients;
command-line tools such as `curl` are unaffected, which is why `ORCHESTRATOR_KEY`
is the primary access control.

### 3. HTTPS is required in production (S-M3)

The orchestrator transmits the `ORCHESTRATOR_KEY` API key over HTTP by default.
**Never run it on a publicly accessible interface without TLS.**

- **Self-hosted (recommended):** Place the orchestrator behind a reverse proxy
  (nginx, Caddy, Traefik) that terminates TLS.  The orchestrator binds to
  `127.0.0.1` only; the proxy handles HTTPS on port 443.

  Example Caddy snippet:
  ```
  taxspine.example.com {
      reverse_proxy 127.0.0.1:8000
  }
  ```

- **Docker Compose:** See the `docker-compose.synology.yml` example which
  routes traffic through the Synology reverse-proxy infrastructure.

- **Local development only:** Plain HTTP on `localhost` is acceptable because
  traffic never leaves the machine.  Do not expose `0.0.0.0:8000` on a
  network-accessible interface without TLS.

### 4. Consider a reverse proxy for rate limiting (SEC-03)

The orchestrator has no built-in rate limiter.  In production, place it behind
nginx, Caddy, or another reverse proxy and configure per-IP or per-key request
limits on mutating endpoints (`POST /jobs`, `POST /uploads/csv`, etc.) to
prevent resource exhaustion.

See `.env.example` for the full list of configurable variables.

---

## Third-Party Data Sources (LC-07)

This service makes outbound HTTP requests to third-party APIs on behalf of the
operator.  No personal data is transmitted; only the requested asset ticker and
date range are included in each call.

| Service | Endpoint | Purpose | Data sent |
|---------|----------|---------|-----------|
| **CoinGecko** | `https://api.coingecko.com/api/v3/coins/{id}/market_chart` | Fetch historical USD prices for NOK valuation | Asset ticker + date range |
| **Bank of England** | `https://www.bankofengland.co.uk/boeapps/database/_iadb-...` | Fetch USD/GBP exchange rate (XUDLUSS series) | Date range only |
| **XRPL public nodes** | `wss://xrplcluster.com` (or configured node) | Fetch account transaction history | XRPL r-address |
| **jsDelivr CDN** | `https://cdn.jsdelivr.net/npm/tailwindcss@...` | Download Tailwind CSS at Docker build time | None (build-time only) |

**Operator responsibility:** By running this software you acknowledge that it
will make network calls to the above services.  For deployments processing data
on behalf of third parties (e.g. clients), ensure that your data-processing
agreements permit outbound API calls to external price data providers.

---

## Data Handling & Privacy (LC-08)

This service processes personal financial data — transaction records that may
identify individuals.  The following data handling practices apply:

### What data is stored

| Data | Location | Retention |
|------|----------|-----------|
| Uploaded CSV files | `UPLOAD_DIR` | Until job is deleted |
| Pipeline output (HTML, JSON, CSV) | `OUTPUT_DIR` | Until job is deleted |
| Job metadata | `DATA_DIR/jobs.db` | Until job is deleted |
| FIFO carry-forward lots | `DATA_DIR/lots.db` | Until explicitly purged |
| Dedup keys (tx hashes only) | `DATA_DIR/dedup/` | Until explicitly purged |
| XRPL addresses in execution logs | `OUTPUT_DIR/{id}/execution.log` | Redacted (replaced with `[XRPL-ADDRESS]`) |

### Deletion

`DELETE /jobs/{id}` removes the job record **and** all associated output and
input files from disk.  Use `POST /admin/cleanup?older_than_days=N` to purge
old jobs automatically.

### Statutory retention period

Norwegian Bokføringsloven § 13 requires tax records to be kept for **7 years**.
Do not delete jobs within 7 years of the tax year they relate to unless advised
by a qualified accountant.

### GDPR

If you process data for other people (e.g. a tax adviser running this service
for clients), you act as a data processor under GDPR.  Ensure you have a lawful
basis, a data processing agreement with any sub-processors, and a documented
retention-and-deletion procedure.

---

## Backup Strategy (INFRA-19)

The service relies on three SQLite databases under `DATA_DIR` (default
`/data/state/`).  Loss of these files means loss of job history, carry-forward
lots, and deduplication records.

### Recommended daily backup (cron or Synology Task Scheduler)

```bash
#!/bin/bash
# Run on the NAS host — adjust paths to match your bind-mount.
BACKUP_DIR=/volume1/taxspine/backups
DATA_DIR=/volume1/docker/taxspine/data/state
DATE=$(date +%Y%m%d)

mkdir -p "$BACKUP_DIR"

# Hot backup using the SQLite online backup API (safe while the server runs).
sqlite3 "$DATA_DIR/jobs.db"  ".backup $BACKUP_DIR/jobs-$DATE.db"
sqlite3 "$DATA_DIR/lots.db"  ".backup $BACKUP_DIR/lots-$DATE.db"

# Backup each per-source dedup database.
for db in "$DATA_DIR/dedup/"*.db; do
    name=$(basename "$db" .db)
    sqlite3 "$db" ".backup $BACKUP_DIR/dedup-${name}-$DATE.db"
done

# Remove backups older than 30 days.
find "$BACKUP_DIR" -name "*.db" -mtime +30 -delete
echo "Backup complete: $BACKUP_DIR"
```

**Synology:** Use DSM Task Scheduler → Create → Scheduled Task → User-defined script,
or enable Synology Hyper Backup on the `/volume1/docker/taxspine/` folder.

**Restore test:** verify a restore at least once:
```bash
sqlite3 /tmp/jobs-restore-test.db ".restore $BACKUP_DIR/jobs-$(date +%Y%m%d).db"
sqlite3 /tmp/jobs-restore-test.db "SELECT COUNT(*) FROM jobs;"
```
