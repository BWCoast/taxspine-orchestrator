# start.ps1 — Launch taxspine-orchestrator (API + embedded UI)
#
# Usage:
#   .\scripts\start.ps1
#   .\scripts\start.ps1 -Port 8001
#   .\scripts\start.ps1 -OpenBrowser:$false
#
# The server serves the dashboard at http://localhost:<Port>/ui/
# Root (/) redirects to /ui/ automatically.

param(
    [int]$Port       = 8000,
    [switch]$OpenBrowser = $true
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

# ── 0. Ensure tax-spine CLIs are on PATH ──────────────────────────────────────
# taxspine-xrpl-nor and taxspine-nor-report are installed as user-level scripts
# by pip install (tax-spine package from Project F / tax-nor repo).
# They live in the Python user base Scripts dir, which is not always on PATH.
if (-not (Get-Command "taxspine-xrpl-nor" -ErrorAction SilentlyContinue)) {
    try {
        # Ask the active Python where it keeps user-level scripts — portable
        # across Python versions (3.11, 3.12, 3.14, …).
        $UserBase    = python -c "import site; print(site.getuserbase())" 2>$null
        $UserScripts = Join-Path $UserBase "Scripts"
        if ($UserScripts -and (Test-Path $UserScripts)) {
            $env:PATH = "$UserScripts;" + $env:PATH
            Write-Host "[path] Added $UserScripts to PATH" -ForegroundColor DarkGray
        }
    } catch {
        # Ignore — the warning below will fire if the CLI is still missing.
    }

    if (-not (Get-Command "taxspine-xrpl-nor" -ErrorAction SilentlyContinue)) {
        Write-Warning "taxspine-xrpl-nor not found on PATH."
        Write-Warning "Real runs will fail. Install tax-spine first:"
        Write-Warning "  pip install -e `"$Root\..\Project F`""
        Write-Warning "  (adjust path to wherever Project F / tax-nor is checked out)"
    }
}

# ── 1. Ensure orchestrator deps ───────────────────────────────────────────────
if (-not (Get-Command "uvicorn" -ErrorAction SilentlyContinue)) {
    Write-Host "[setup] uvicorn not found — installing deps..." -ForegroundColor Cyan
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

# ── 4. Start API (serves /ui/ and all /jobs, /workspace endpoints) ────────────
# Uses python -m uvicorn so it works even when uvicorn is not on PATH directly
# (e.g. installed in the same Python that's running this script).
python -m uvicorn taxspine_orchestrator.main:app `
    --host 0.0.0.0 `
    --port $Port `
    --reload
