# PivotBoss AI - Windows Task Scheduler Setup
# Run as Administrator: Right-click PowerShell -> Run as Administrator

$taskName = "PivotBoss AI Trading Bot"
$botPath  = "D:\pivotboss-ai\start_bot.bat"
$logPath  = "D:\pivotboss-ai\logs"

New-Item -ItemType Directory -Force -Path $logPath | Out-Null
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:50AM"

# Use SYSTEM account - no permission issues
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$botPath`"" `
    -WorkingDirectory "D:\pivotboss-ai"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask `
    -TaskName $taskName `
    -Trigger $trigger `
    -Principal $principal `
    -Action $action `
    -Settings $settings `
    -Description "PivotBoss AI trading bot - weekday mornings" `
    -Force

if ($?) {
    Write-Host "SUCCESS - Task scheduled for 8:50 AM every weekday!" -ForegroundColor Green
} else {
    Write-Host "FAILED - Try manual setup below" -ForegroundColor Red
    Write-Host ""
    Write-Host "MANUAL SETUP:" -ForegroundColor Yellow
    Write-Host "1. Press Win+R -> type taskschd.msc -> Enter"
    Write-Host "2. Action -> Create Basic Task"
    Write-Host "3. Name: PivotBoss AI Trading Bot"
    Write-Host "4. Trigger: Weekly, Mon-Fri, 8:50 AM"
    Write-Host "5. Action: Start a program"
    Write-Host "6. Program: D:\pivotboss-ai\start_bot.bat"
}
