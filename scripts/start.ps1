# start.ps1 — Launch taxspine-orchestrator (API + embedded UI)
#
# Usage:
#   .\scripts\start.ps1
#   .\scripts\start.ps1 -Port 8001
#   .\scripts\start.ps1 -OpenBrowser:$false
#
# The server also serves the dashboard UI at http://localhost:<Port>/ui/
# Root (/) redirects to /ui/ automatically.

param(
    [int]$Port       = 8000,
    [switch]$OpenBrowser = $true
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

# ── 1. Ensure deps ────────────────────────────────────────────────────────────
if (-not (Get-Command uvicorn -ErrorAction SilentlyContinue)) {
    Write-Host "[setup] Installing deps (pip install -e .)..." -ForegroundColor Cyan
    pip install -e "." --quiet
}

# ── 2. Print startup info ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Taxspine Orchestrator" -ForegroundColor Green
Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Dashboard:  http://localhost:$Port/ui/" -ForegroundColor White
Write-Host "  API docs:   http://localhost:$Port/docs" -ForegroundColor DarkGray
Write-Host "  Health:     http://localhost:$Port/health" -ForegroundColor DarkGray
Write-Host "  Stop:       Ctrl+C" -ForegroundColor DarkGray
Write-Host ""

# ── 3. Open browser after short delay ─────────────────────────────────────────
if ($OpenBrowser) {
    Start-Job -ScriptBlock {
        param($p)
        Start-Sleep 2
        Start-Process "http://localhost:$p/ui/"
    } -ArgumentList $Port | Out-Null
}

# ── 4. Start API (also serves /ui/) ──────────────────────────────────────────
# Both of these work:
#   uvicorn main:app --reload
#   uvicorn taxspine_orchestrator.main:app --reload
uvicorn taxspine_orchestrator.main:app --host 0.0.0.0 --port $Port --reload
