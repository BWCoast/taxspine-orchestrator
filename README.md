# taxspine-orchestrator

Internal API for creating, tracking, and (eventually) executing tax-computation
jobs that coordinate blockchain-reader and taxspine-\* CLI pipelines.

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

| Method | Path                  | Description                    |
|--------|-----------------------|--------------------------------|
| GET    | `/health`             | Health check                   |
| POST   | `/jobs`               | Create a new tax job           |
| GET    | `/jobs`               | List all jobs                  |
| GET    | `/jobs/{job_id}`      | Get a single job by ID         |
| POST   | `/jobs/{job_id}/start`| Start (stub) job execution     |

### Example — create a job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "xrpl_accounts": ["rEXAMPLE1"],
    "tax_year": 2025,
    "country": "norway"
  }'
```
