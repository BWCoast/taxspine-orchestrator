# build-local.ps1 — Build taxspine-orchestrator image using local sources
#
# Use this instead of the main Dockerfile when tax-nor or blockchain-reader are
# private, OR when you want to build against local (unpushed) changes.
#
# Usage:
#   .\scripts\build-local.ps1
#   .\scripts\build-local.ps1 -Tag "taxspine-orchestrator:dev"
#   .\scripts\build-local.ps1 -TaxNorPath "D:\projects\tax-nor"
#   .\scripts\build-local.ps1 -BlockchainReaderPath "D:\projects\blockchain-reader"
#
# What it does:
#   1. Copies tax-nor source into vendor/tax-nor/
#   2. Copies blockchain-reader source into vendor/blockchain-reader/
#   3. Runs: docker build -f Dockerfile.local -t <Tag> .
#   4. Removes both vendor copies (cleanup — never committed)

param(
    [string]$Tag                  = "taxspine-orchestrator:local",
    [string]$TaxNorPath           = "",   # Leave empty to auto-detect
    [string]$BlockchainReaderPath = ""    # Leave empty to auto-detect
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

# ── Helper: resolve a source repo path ────────────────────────────────────────
function Resolve-RepoPath {
    param([string]$GivenPath, [string[]]$Candidates, [string]$RepoName)

    if ($GivenPath) {
        if (-not (Test-Path $GivenPath)) {
            Write-Error "Cannot find $RepoName at: $GivenPath"
            exit 1
        }
        return $GivenPath
    }

    foreach ($c in $Candidates) {
        $resolved = Resolve-Path $c -ErrorAction SilentlyContinue
        if ($resolved -and (Test-Path (Join-Path $resolved "pyproject.toml"))) {
            return $resolved.Path
        }
    }
    return $null
}

# ── 1. Resolve tax-nor source path ────────────────────────────────────────────
$TaxNorPath = Resolve-RepoPath `
    -GivenPath $TaxNorPath `
    -Candidates @(
        (Join-Path $Root "..\Project F"),
        (Join-Path $Root "..\tax-nor"),
        (Join-Path $Root "..\Repo cloning\tax-nor")
    ) `
    -RepoName "tax-nor"

if (-not $TaxNorPath) {
    Write-Error "Cannot find tax-nor source. Pass -TaxNorPath explicitly."
    Write-Error "  Example: .\scripts\build-local.ps1 -TaxNorPath `"C:\Users\you\Documents\Project F`""
    exit 1
}

# ── 2. Resolve blockchain-reader source path ──────────────────────────────────
$BlockchainReaderPath = Resolve-RepoPath `
    -GivenPath $BlockchainReaderPath `
    -Candidates @(
        (Join-Path $Root "..\blockchain-reader"),
        (Join-Path $Root "..\Repo cloning\blockchain-reader"),
        (Join-Path $Root "..\Finansprosjekt\services\blockchain-reader")
    ) `
    -RepoName "blockchain-reader"

if (-not $BlockchainReaderPath) {
    Write-Error "Cannot find blockchain-reader source. Pass -BlockchainReaderPath explicitly."
    Write-Error "  Example: .\scripts\build-local.ps1 -BlockchainReaderPath `"C:\Users\you\repos\blockchain-reader`""
    exit 1
}

Write-Host "[build-local] tax-nor source          : $TaxNorPath" -ForegroundColor Cyan
Write-Host "[build-local] blockchain-reader source: $BlockchainReaderPath" -ForegroundColor Cyan

# ── 3. Copy sources into vendor/ (inside Docker build context) ────────────────
$VendorTaxNor = Join-Path $Root "vendor\tax-nor"
$VendorBlockchain = Join-Path $Root "vendor\blockchain-reader"

foreach ($dir in @($VendorTaxNor, $VendorBlockchain)) {
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
}

Write-Host "[build-local] Copying tax-nor → vendor/tax-nor/ ..." -ForegroundColor Cyan
Copy-Item -Path $TaxNorPath -Destination $VendorTaxNor -Recurse -Force

Write-Host "[build-local] Copying blockchain-reader → vendor/blockchain-reader/ ..." -ForegroundColor Cyan
Copy-Item -Path $BlockchainReaderPath -Destination $VendorBlockchain -Recurse -Force

# ── 4. Docker build ───────────────────────────────────────────────────────────
Write-Host "[build-local] Building image: $Tag" -ForegroundColor Green
docker build -f Dockerfile.local -t $Tag .
$ExitCode = $LASTEXITCODE

# ── 5. Clean up vendor copies (never leave them committed) ────────────────────
Write-Host "[build-local] Cleaning up vendor/ ..." -ForegroundColor DarkGray
foreach ($dir in @($VendorTaxNor, $VendorBlockchain)) {
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
}

if ($ExitCode -ne 0) {
    Write-Error "[build-local] docker build failed (exit $ExitCode)"
    exit $ExitCode
}

Write-Host ""
Write-Host "  Build complete: $Tag" -ForegroundColor Green
Write-Host "  Run with:  docker run -p 8000:8000 -v `"${Root}\data:/data`" $Tag" -ForegroundColor DarkGray
Write-Host "  Or:        docker compose -f docker-compose.yml up" -ForegroundColor DarkGray
Write-Host ""
