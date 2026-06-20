param(
    [string]$Cohort = "pilot-v1",
    [string]$DataRoot = "backtest_data",
    [string]$TaskName = "KalshiWeatherBacktestCollector",
    [Parameter(Mandatory = $true)]
    [string]$NwsUserAgent
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$python = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $projectRoot "weather_backtest.py"
$resolvedDataRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $DataRoot))
$arguments = '"{0}" --root "{1}" --cohort "{2}" --nws-user-agent "{3}" collect-due' -f $script, $resolvedDataRoot, $Cohort, $NwsUserAgent

$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Collect immutable point-in-time Kalshi weather model snapshots." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Cohort: $Cohort"
Write-Host "Data root: $resolvedDataRoot"
