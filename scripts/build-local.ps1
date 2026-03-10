# build-local.ps1 — Build taxspine-orchestrator image using local tax-nor source
#
# Use this instead of the main Dockerfile when tax-nor is private OR when you
# want to build against local (unpushed) changes to tax-spine.
#
# Usage:
#   .\scripts\build-local.ps1
#   .\scripts\build-local.ps1 -Tag "taxspine-orchestrator:dev"
#   .\scripts\build-local.ps1 -TaxNorPath "D:\projects\tax-nor"
#
# What it does:
#   1. Copies the tax-nor source into vendor/tax-nor/ (inside build context)
#   2. Runs: docker build -f Dockerfile.local -t <Tag> .
#   3. Removes vendor/tax-nor/ (cleanup — never committed)

param(
    [string]$Tag        = "taxspine-orchestrator:local",
    [string]$TaxNorPath = ""   # Leave empty to auto-detect
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

# ── 1. Resolve tax-nor source path ────────────────────────────────────────────
if (-not $TaxNorPath) {
    # Default: sibling directory named "Project F" (the local tax-nor working copy)
    # Adjust this path if your Project F folder is elsewhere.
    $Candidates = @(
        (Join-Path $Root "..\Project F"),
        (Join-Path $Root "..\tax-nor"),
        (Join-Path $Root "..\Repo cloning\tax-nor")
    )
    foreach ($c in $Candidates) {
        $resolved = Resolve-Path $c -ErrorAction SilentlyContinue
        if ($resolved -and (Test-Path (Join-Path $resolved "pyproject.toml"))) {
            $TaxNorPath = $resolved
            break
        }
    }
}

if (-not $TaxNorPath -or -not (Test-Path $TaxNorPath)) {
    Write-Error "Cannot find tax-nor source. Pass -TaxNorPath explicitly."
    Write-Error "  Example: .\scripts\build-local.ps1 -TaxNorPath C:\Users\you\Documents\Project F"
    exit 1
}

Write-Host "[build-local] tax-nor source: $TaxNorPath" -ForegroundColor Cyan

# ── 2. Copy source into vendor/ (inside Docker build context) ─────────────────
$VendorDir = Join-Path $Root "vendor\tax-nor"
if (Test-Path $VendorDir) {
    Remove-Item $VendorDir -Recurse -Force
}
Write-Host "[build-local] Copying tax-nor → vendor/tax-nor/ ..." -ForegroundColor Cyan
Copy-Item -Path $TaxNorPath -Destination $VendorDir -Recurse -Force

# ── 3. Docker build ───────────────────────────────────────────────────────────
Write-Host "[build-local] Building image: $Tag" -ForegroundColor Green
docker build -f Dockerfile.local -t $Tag .
$ExitCode = $LASTEXITCODE

# ── 4. Clean up vendor copy (never leave it committed) ────────────────────────
Write-Host "[build-local] Cleaning up vendor/tax-nor/ ..." -ForegroundColor DarkGray
Remove-Item $VendorDir -Recurse -Force

if ($ExitCode -ne 0) {
    Write-Error "[build-local] docker build failed (exit $ExitCode)"
    exit $ExitCode
}

Write-Host ""
Write-Host "  Build complete: $Tag" -ForegroundColor Green
Write-Host "  Run with:  docker run -p 8000:8000 -v `"${Root}\data:/data`" $Tag" -ForegroundColor DarkGray
Write-Host "  Or:        docker compose -f docker-compose.yml up" -ForegroundColor DarkGray
Write-Host ""
