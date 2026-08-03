# start-service.ps1 - Run taxspine-orchestrator as a persistent background service
#
# Designed for Task Scheduler: no terminal window, auto-restart on crash.
#
# Register once with:  scripts\register-service.ps1
# Start/stop:         Start-ScheduledTask / Stop-ScheduledTask -TaskName taxspine-orchestrator
# Logs:               <repo>\uvicorn.log  (rotated at 10 MB)

param(
    [int]$Port            = 8000,
    [int]$RestartDelaySec = 5,
    [long]$MaxLogBytes    = 10MB
)

$Root    = Split-Path $PSScriptRoot -Parent
$LogFile = Join-Path $Root "uvicorn.log"
$EnvFile = Join-Path $Root ".env"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Write-Log ([string]$Msg) {
    $ts   = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    $line = "[$ts] $Msg"
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Rotate-Log {
    if (-not (Test-Path $LogFile)) { return }
    if ((Get-Item $LogFile).Length -lt $MaxLogBytes) { return }
    for ($i = 2; $i -ge 1; $i--) {
        $src = "$LogFile.$i"
        $dst = "$LogFile.$($i + 1)"
        if (Test-Path $src) { Move-Item $src $dst -Force }
    }
    Move-Item $LogFile "$LogFile.1" -Force
}

function Load-DotEnv {
    if (-not (Test-Path $EnvFile)) { return }
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^\s*#' -or $line -eq '') { return }
        if ($line -match '^([^=]+)=(.*)$') {
            $key = $Matches[1].Trim()
            $val = $Matches[2].Trim().Trim('"').Trim("'")
            if (-not [System.Environment]::GetEnvironmentVariable($key, 'Process')) {
                [System.Environment]::SetEnvironmentVariable($key, $val, 'Process')
            }
        }
    }
}

# ── Add user Scripts dir to PATH (for tax-spine CLIs) ────────────────────────
try {
    $userBase    = & python -c "import site; print(site.getuserbase())" 2>$null
    $userScripts = Join-Path $userBase "Scripts"
    if ($userScripts -and (Test-Path $userScripts)) {
        $env:PATH = "$userScripts;" + $env:PATH
    }
} catch {}

# ── Main restart loop ─────────────────────────────────────────────────────────
Set-Location $Root
Load-DotEnv

Write-Log "=== taxspine-orchestrator service starting (port $Port) ==="

$uvicornArgs = @(
    "-m", "uvicorn",
    "taxspine_orchestrator.main:app",
    "--host", "0.0.0.0",
    "--port", "$Port",
    "--no-access-log"
)

while ($true) {
    Rotate-Log
    Write-Log "Starting uvicorn on port $Port..."

    # Run uvicorn via cmd so stdout+stderr both append to the log file.
    # cmd /c "..." >> file 2>&1  is the most reliable cross-stream redirect on Windows.
    $argStr  = ($uvicornArgs | ForEach-Object { "`"$_`"" }) -join " "
    $cmdLine = "python $argStr >> `"$LogFile`" 2>&1"
    cmd /c $cmdLine

    $exit = $LASTEXITCODE
    Write-Log "uvicorn exited (code $exit). Restarting in ${RestartDelaySec}s..."
    Start-Sleep $RestartDelaySec
}
