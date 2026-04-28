param(
  [string]$TaskName = "RetailSentimentDashboard",
  [string]$PythonExe = "python",
  [string]$StartTime = "08:00"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $ProjectDir "sentiment_dashboard.py"

if (!(Test-Path $ScriptPath)) {
  throw "Cannot find $ScriptPath"
}

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`"" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Daily -At $StartTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Send retail sentiment dashboard email every weekday morning." -Force | Out-Null
Write-Host "Task registered: $TaskName, daily at $StartTime. The script skips non-workdays."
