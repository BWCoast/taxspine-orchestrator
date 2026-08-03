# register-service.ps1 - Register taxspine-orchestrator as a Task Scheduler task
#
# Run once (no admin required - task runs as current user at logon):
#   scripts\register-service.ps1
#
# The task:
#   - Starts at logon of the current user
#   - Runs hidden (no terminal window)
#   - Restarts automatically if it stops (up to 3 times, 1 min delay)
#   - Logs to <repo>\uvicorn.log

$TaskName   = "taxspine-orchestrator"
$RepoRoot   = Split-Path $PSScriptRoot -Parent
$ScriptPath = Join-Path $RepoRoot "scripts\start-service.ps1"
$LogPath    = Join-Path $RepoRoot "uvicorn.log"
$PSExe      = "powershell.exe"
$PSArgs     = "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$ScriptPath"""

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Trigger: at current user logon
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Action: run the service script hidden
$action = New-ScheduledTaskAction `
    -Execute  $PSExe `
    -Argument $PSArgs `
    -WorkingDirectory $RepoRoot

# Settings: restart on failure, no time limit
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0) `
    -RestartCount        3 `
    -RestartInterval     (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -MultipleInstances   IgnoreNew

# Register
Register-ScheduledTask `
    -TaskName  $TaskName `
    -Trigger   $trigger `
    -Action    $action `
    -Settings  $settings `
    -RunLevel  Limited `
    -Force | Out-Null

Write-Host ""
Write-Host "  Task '$TaskName' registered." -ForegroundColor Green
Write-Host "  Starts at logon - no terminal window." -ForegroundColor White
Write-Host ""
Write-Host "  Start now:  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor DarkGray
Write-Host "  Stop:       Stop-ScheduledTask  -TaskName '$TaskName'" -ForegroundColor DarkGray
Write-Host "  Status:     Get-ScheduledTask   -TaskName '$TaskName' | Select-Object State" -ForegroundColor DarkGray
Write-Host "  Logs:       $LogPath" -ForegroundColor DarkGray
Write-Host "  Unregister: Unregister-ScheduledTask -TaskName taxspine-orchestrator -Confirm:$false" -ForegroundColor DarkGray
Write-Host ""
