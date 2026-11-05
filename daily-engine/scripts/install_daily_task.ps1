$ErrorActionPreference = "Stop"

$action = New-ScheduledTaskAction -Execute "D:\Docker\Horizon\scripts\run_daily.bat"
$trigger = New-ScheduledTaskTrigger -Daily -At "08:10"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun

Register-ScheduledTask `
    -TaskName "Horizon Daily Briefing" `
    -Description "Generate the finance and laser daily briefings at 08:10 Beijing time." `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force

Write-Host "Registered scheduled task: Horizon Daily Briefing (daily 08:10)."
