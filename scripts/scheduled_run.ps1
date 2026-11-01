# Registers a Windows Scheduled Task that runs social2shorts unattended.
#
# Each run processes new catalog posts and inbox videos until the day's YouTube quota is
# spent (`run --continuous`), then stops cleanly; the next run picks up where it left off.
#
# Usage (from any folder, in PowerShell):
#   .\scripts\scheduled_run.ps1                                   # every 4 h, scheduled publishing
#   .\scripts\scheduled_run.ps1 -EveryHours 6 -Privacy unlisted -Source raw
#   .\scripts\scheduled_run.ps1 -Unregister

param(
    [string]$TaskName = "social2shorts",
    [int]$EveryHours = 4,
    [ValidateSet("catalog", "raw", "all")][string]$Source = "all",
    [ValidateSet("public", "unlisted", "private", "scheduled")][string]$Privacy = "scheduled",
    [string]$Config = "config.toml",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) {
    throw "uv was not found on PATH. Install it (https://docs.astral.sh/uv/) and run this script again."
}
if (-not (Test-Path (Join-Path $RepoRoot $Config))) {
    throw "Config '$Config' not found in $RepoRoot. Copy config.example.toml to config.toml first."
}

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$RunArgs = "run --continuous --source $Source --privacy $Privacy -c `"$Config`""
# cmd /c so the output (and any OAuth alert) lands in logs\social2shorts.log. PYTHONUTF8: the
# Windows code page (cp1252) cannot print emoji or Arabic captions and stops the run.
$Command = "set PYTHONUTF8=1&& `"$Uv`" run social2shorts $RunArgs >> `"$LogDir\social2shorts.log`" 2>&1"

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $Command" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours $EveryHours)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "social2shorts: turn new moto videos into YouTube Shorts" -Force | Out-Null

Write-Host "Scheduled task '$TaskName' runs every $EveryHours h from $RepoRoot."
Write-Host "Logs: $LogDir\social2shorts.log  |  Check progress: uv run social2shorts status"
