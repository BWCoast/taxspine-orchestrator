# start.ps1 — Launch taxspine-orchestrator API + open UI
# Usage:  .\scripts\start.ps1
#         .\scripts\start.ps1 -Port 8001
#         .\scripts\start.ps1 -OpenUI:$false

param(
    [int]$Port    = 8000,
    [switch]$OpenUI = $true
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

# ── 1. Check Python venv / deps ───────────────────────────────────────────────
if (-not (Get-Command uvicorn -ErrorAction SilentlyContinue)) {
    Write-Host "[setup] Installing deps (pip install -e .[dev])..." -ForegroundColor Cyan
    pip install -e ".[dev]" --quiet
}

# ── 2. Start API server ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Starting taxspine-orchestrator on http://localhost:$Port" -ForegroundColor Green
Write-Host "  API docs:  http://localhost:$Port/docs" -ForegroundColor DarkGray
Write-Host "  Stop:      Ctrl+C" -ForegroundColor DarkGray
Write-Host ""

if ($OpenUI) {
    # Open the UI in the default browser after a short delay
    $uiPath = Join-Path $Root "ui.html"
    Start-Job -ScriptBlock {
        param($p, $ui)
        Start-Sleep 2
        Start-Process "http://localhost:$p"
        # Also open the HTML UI directly (file://)
        if (Test-Path $ui) { Start-Process $ui }
    } -ArgumentList $Port, $uiPath | Out-Null
}

uvicorn taxspine_orchestrator.main:app --host 0.0.0.0 --port $Port --reload
