$ErrorActionPreference = "Stop"

$action = New-ScheduledTaskAction -Execute (Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\run_daily.bat")
$trigger = New-ScheduledTaskTrigger -Daily -At "08:10"
# -AllowStartIfOnBatteries: laptops on battery otherwise skip the 08:10 run and
# only catch up hours later (or not at all) via StartWhenAvailable.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName "Horizon Daily Briefing" `
    -Description "Generate the finance and laser daily briefings at 08:10 Beijing time." `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force

Write-Host "Registered scheduled task: Horizon Daily Briefing (daily 08:10)."
