# scripts/scheduled_run.ps1
# Registers the social2shorts app as a Windows Scheduled Task.

$TaskName = "Insta2YouTube_Continuous"
$PythonExe = "C:\workdir\uw\scraper\insta2yorube\.venv\Scripts\python.exe"
$ScriptPath = "C:\workdir\uw\scraper\insta2yorube\src\social2shorts\cli.py"
$Args = "run-continuous" # Assuming cli.py has this command

# Run every 4 hours
$Trigger = New-ScheduledTaskTrigger -Daily -At 00:00
$Trigger.RepetitionInterval = (New-TimeSpan -Hours 4)

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "$ScriptPath $Args"

# Register the task
Register-ScheduledTask -TaskName $TaskName -Trigger $Trigger -Action $Action -Description "Continuously discover and process Instagram posts to YouTube" -Force

Write-Host "Successfully registered Scheduled Task: $TaskName"
