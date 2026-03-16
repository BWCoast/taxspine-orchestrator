# deploy.ps1 — Deploy taxspine-orchestrator to Synology NAS
#
# Pulls the latest image from ghcr.io and restarts the container on the NAS
# via SSH.  Use this for an immediate forced deploy instead of waiting for
# the 5-minute Watchtower cycle.
#
# Prerequisites on the NAS (one-time):
#   1. Enable SSH in DSM → Control Panel → Terminal & SNMP → Enable SSH service
#   2. Ensure the compose file is on the NAS at $ComposeDir
#   3. Either use password auth (you will be prompted) or pre-install an SSH key
#
# Usage:
#   .\scripts\deploy.ps1
#   .\scripts\deploy.ps1 -NasHost 192.168.1.100
#   .\scripts\deploy.ps1 -NasHost nas.local -NasUser admin -ComposeDir /volume1/docker/taxspine
#   .\scripts\deploy.ps1 -ImageTag sha-abc1234  # deploy a specific commit
#   .\scripts\deploy.ps1 -Local                 # build + load local image (no push)

param(
    # NAS hostname or IP — override if the NAS is not at the default name
    [string]$NasHost    = "diskstation",

    # SSH user on the NAS (must have docker/sudo access)
    [string]$NasUser    = "admin",

    # Absolute path on the NAS where docker-compose.synology.yml lives
    [string]$ComposeDir = "/volume1/docker/taxspine",

    # Image tag to deploy.  Defaults to "latest" (Watchtower tracks this).
    [string]$ImageTag   = "latest",

    # Docker Compose file name on the NAS
    [string]$ComposeFile = "docker-compose.synology.yml",

    # When set, build the local image with build-local.ps1 and deploy that
    # instead of pulling from GHCR.  Does not push to any registry.
    [switch]$Local,

    # Skip the SSH step — only rebuild / retag locally.  Useful for testing
    # the build half without touching the NAS.
    [switch]$BuildOnly,

    # SSH key file for passwordless auth (optional)
    [string]$SshKey = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

Set-Location $Root

# ── Helpers ───────────────────────────────────────────────────────────────────

function Write-Step([string]$Msg) {
    Write-Host ""
    Write-Host "  >> $Msg" -ForegroundColor Cyan
}

function Write-Ok([string]$Msg) {
    Write-Host "  OK  $Msg" -ForegroundColor Green
}

function Write-Fail([string]$Msg) {
    Write-Host "  !! $Msg" -ForegroundColor Red
    exit 1
}

function Invoke-Ssh([string]$Command) {
    $SshArgs = @("-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes")
    if ($SshKey) { $SshArgs += @("-i", $SshKey) }
    $SshArgs += @("${NasUser}@${NasHost}", $Command)
    Write-Host "     [ssh] $Command" -ForegroundColor DarkGray
    & ssh @SshArgs
    if ($LASTEXITCODE -ne 0) { Write-Fail "SSH command failed (rc=$LASTEXITCODE): $Command" }
}

# ── Step 0: Sanity check — ssh available ─────────────────────────────────────
if (-not $BuildOnly) {
    if (-not (Get-Command "ssh" -ErrorAction SilentlyContinue)) {
        Write-Fail "ssh not found on PATH. Install OpenSSH (Windows optional feature) or add it to PATH."
    }
}

# ── Step 1: Build local image (optional) ─────────────────────────────────────
if ($Local) {
    Write-Step "Building local image with build-local.ps1 ..."
    & "$Root\scripts\build-local.ps1" -Tag "taxspine-orchestrator:deploy"
    if ($LASTEXITCODE -ne 0) { Write-Fail "build-local.ps1 failed." }
    Write-Ok "Local image built: taxspine-orchestrator:deploy"

    if ($BuildOnly) {
        Write-Host ""
        Write-Host "  --BuildOnly set. Stopping after local build." -ForegroundColor Yellow
        exit 0
    }

    # Save the image to a tarball, SCP to NAS, load it there.
    Write-Step "Exporting image to taxspine-orchestrator.tar ..."
    docker save taxspine-orchestrator:deploy -o "$Root\taxspine-orchestrator.tar"
    if ($LASTEXITCODE -ne 0) { Write-Fail "docker save failed." }

    Write-Step "Uploading image to NAS ($NasHost) ..."
    $SshArgs = @("-o", "StrictHostKeyChecking=no")
    if ($SshKey) { $SshArgs += @("-i", $SshKey) }
    & scp @SshArgs "$Root\taxspine-orchestrator.tar" "${NasUser}@${NasHost}:/tmp/taxspine-orchestrator.tar"
    if ($LASTEXITCODE -ne 0) { Write-Fail "scp failed." }

    Remove-Item "$Root\taxspine-orchestrator.tar" -ErrorAction SilentlyContinue

    Write-Step "Loading image on NAS and restarting container ..."
    Invoke-Ssh "docker load -i /tmp/taxspine-orchestrator.tar && rm /tmp/taxspine-orchestrator.tar"
    Invoke-Ssh "cd $ComposeDir && docker compose -f $ComposeFile up -d"

} else {
    # ── Step 1 (GHCR mode): pull latest image on NAS ─────────────────────────

    if ($BuildOnly) {
        Write-Host "  --BuildOnly has no effect without --Local (nothing to build)." -ForegroundColor Yellow
        exit 0
    }

    $Image = "ghcr.io/bwcoast/taxspine-orchestrator:$ImageTag"

    Write-Step "Deploying $Image to $NasHost ..."

    # Ensure the compose directory and file exist on the NAS.
    Invoke-Ssh "test -f $ComposeDir/$ComposeFile || (echo 'ERROR: $ComposeDir/$ComposeFile not found on NAS.' && exit 1)"

    # Pull the latest image and recreate the container if the image changed.
    Invoke-Ssh "docker pull $Image"
    Invoke-Ssh "cd $ComposeDir && docker compose -f $ComposeFile up -d --remove-orphans"
}

# ── Step 2: Health check ──────────────────────────────────────────────────────
Write-Step "Waiting for health check ..."
Start-Sleep -Seconds 5

$HealthScript = 'python -c "import urllib.request,sys; r=urllib.request.urlopen(\"http://localhost:8000/health\"); print(r.status)"'
Invoke-Ssh $HealthScript

Write-Ok "Container is healthy."

# ── Step 3: Show running image ────────────────────────────────────────────────
Write-Step "Running container image:"
Invoke-Ssh "docker inspect taxspine-orchestrator --format '  Image: {{.Config.Image}}  Created: {{.Created}}'"

Write-Host ""
Write-Host "  Deployment complete." -ForegroundColor Green
Write-Host "  Dashboard: http://${NasHost}:8000/ui/" -ForegroundColor White
Write-Host ""
