# Operations Manual — taxspine-orchestrator

## Deployment

The orchestrator runs as a Docker container on a Synology NAS.
The container image is built by GitHub Actions on every push to `main` and
pushed to GitHub Container Registry (`ghcr.io/bwcoast/taxspine-orchestrator`).
Synology Container Manager pulls the image via `docker-compose.synology.yml`.

All persistent state is stored under `/data` inside the container, which is
bind-mounted to a directory on the NAS volume.

### State directories

| Path (container) | Contents |
|---|---|
| `/data/state/jobs.db` | Job history and status (SQLite) |
| `/data/state/lots.db` | FIFO lot snapshots by tax year (SQLite) |
| `/data/state/dedup/` | Per-source deduplication databases (SQLite) |
| `/data/state/workspace.json` | Workspace configuration (JSON) |
| `/data/prices/` | Cached NOK price CSVs by year |
| `/data/output/` | Job output files (HTML reports, RF-1159 JSON) |
| `/data/uploads/` | Uploaded CSV files (transient) |

---

## Backup Policy

**All state is covered by NAS-level backup.**

The NAS bind-mount directory is included in automatic backups to a third-party
cloud backup provider. Backups run on a scheduled basis and are managed at the
NAS level — no application-layer backup scripts are required.

### What is backed up

- All SQLite databases (`jobs.db`, `lots.db`, dedup stores)
- `workspace.json` — XRPL asset registry and job configuration
- Price CSV cache — can be regenerated via `POST /prices/fetch` but is backed
  up for continuity
- Job output files (HTML reports, RF-1159 JSON)

### Recovery

To restore after data loss:

1. Restore the NAS volume from the backup provider.
2. Restart the container — it reads state from the bind-mount on startup.
3. Verify with `GET /health` and `GET /diagnostics`.

No special application-layer steps are needed beyond restarting the container
against the restored volume.

### Retention

The backup provider's retention policy governs how far back point-in-time
recovery is possible. Norwegian Ligningsloven requires accounting records to
be retained for **5 years**. Ensure the backup provider's retention period
covers at least 5 years for tax year data (job outputs, lot snapshots).

---

## Supply-Chain Pinning

### blockchain-reader

The `blockchain-reader` package is installed from the `BWCoast/blockchain-reader`
GitHub repository. To prevent supply-chain attacks, the CI workflow:

1. Fetches the HEAD commit SHA from the GitHub API using `GH_READ_TOKEN`.
2. Passes the full 40-character SHA as `BLOCKCHAIN_READER_SHA` build arg.
3. The Dockerfile **hard-fails** if `BLOCKCHAIN_READER_SHA` is left as `"main"`.

`GH_READ_TOKEN` must be set as a repository secret — the build will fail
without it. To update the pinned version, simply push to `main`; the workflow
fetches the current HEAD SHA automatically.

### tax-nor CLIs

The same SHA-based pinning applies to the `tax-nor` package
(`taxspine-nor-report`, `taxspine-xrpl-nor`, etc.). The CI workflow fetches
the `tax-nor` HEAD SHA and passes it as both `TAXNOR_TAG` and `TAXNOR_SHA`.

### Local development builds

For local builds, use `Dockerfile.local` (via `scripts/build-local.ps1`).
This installs both packages from a local `vendor/` directory rather than
from GitHub, so no SHAs or tokens are required.

---

## Log Rotation

Container logs are managed by Docker's `json-file` log driver. The
`docker-compose.synology.yml` configures log rotation:

```yaml
logging:
  driver: json-file
  options:
    max-size: "50m"
    max-file: "5"
```

This caps container log disk usage at ~250 MB.

---

## Encryption at Rest (LC-04)

All SQLite databases (`jobs.db`, `lots.db`, dedup stores) and workspace files
are stored on the NAS volume, which provides **full-disk encryption (FDE)**
at the hardware/OS level via Synology's built-in volume encryption feature.

This satisfies the encryption-at-rest requirement for personal financial data
without requiring application-layer encryption (SQLCipher or similar).

**Threat model:** The FDE key is tied to the NAS hardware. The encrypted volume
cannot be read if the drive is physically removed from the NAS. The data is
accessible while the NAS is running and the volume is mounted — this is
acceptable for a single-owner, single-location home deployment with no
third-party physical access to the hardware.

If the NAS is ever replaced, ensure the new device's volume encryption is
enabled before restoring the backup.

---

## Health and Diagnostics

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check — returns 200 OK or 503 on critical failure |
| `GET /diagnostics` | Full subsystem snapshot (lot store, prices, jobs, dedup) |

The Synology Container Manager healthcheck polls `GET /health` every 30 seconds.
