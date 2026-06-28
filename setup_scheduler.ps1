# Run this once as Administrator to set up Windows Task Scheduler
# Right-click this file → Run with PowerShell (as Administrator)

$taskName = "PivotBoss AI Trading Bot"
$botPath  = "D:\pivotboss-ai\start_bot_auto.bat"
$logPath  = "D:\pivotboss-ai\logs"

# Create logs folder if missing
New-Item -ItemType Directory -Force -Path $logPath | Out-Null

# Remove existing task if any
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Create trigger: every weekday at 8:50 AM
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:50AM"

# Run as current user, only when logged on
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# Action: run the batch file
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$botPath`"" `
    -WorkingDirectory "D:\pivotboss-ai"

# Settings: run missed task, restart on failure
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

# Register the task
Register-ScheduledTask `
    -TaskName $taskName `
    -Trigger $trigger `
    -Principal $principal `
    -Action $action `
    -Settings $settings `
    -Description "Auto-starts PivotBoss AI trading bot on weekday mornings" `
    -Force

Write-Host ""
Write-Host "Task '$taskName' scheduled successfully!" -ForegroundColor Green
Write-Host "It will run every weekday at 8:50 AM automatically." -ForegroundColor Green
Write-Host ""
Write-Host "To verify: Open Task Scheduler → Task Scheduler Library → look for '$taskName'"
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
