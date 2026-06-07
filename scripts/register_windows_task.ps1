param(
    [string]$DailyTaskName = "iddaa-daily-pipeline",
    [string]$LiveScoreTaskName = "iddaa-live-score-pipeline",
    [string]$DailyTime = "09:00",
    [int]$LiveScoreIntervalMinutes = 15
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$taskScript = Join-Path $PSScriptRoot "run_ingest_cycle.ps1"
$powershellExe = (Get-Command powershell).Source

$dailyAction = "`"$powershellExe`" -ExecutionPolicy Bypass -File `"$taskScript`" -Mode daily"
$liveScoreAction = "`"$powershellExe`" -ExecutionPolicy Bypass -File `"$taskScript`" -Mode live-scores"

schtasks /Create `
  /TN $DailyTaskName `
  /TR $dailyAction `
  /SC DAILY `
  /ST $DailyTime `
  /F `
  /RL LIMITED

schtasks /Create `
  /TN $LiveScoreTaskName `
  /TR $liveScoreAction `
  /SC MINUTE `
  /MO $LiveScoreIntervalMinutes `
  /F `
  /RL LIMITED
