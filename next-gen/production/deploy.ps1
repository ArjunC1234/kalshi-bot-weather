param(
    [string]$HostName = "root@134.122.9.101",
    [string]$RemotePath = "/opt/kalshi-weather-next-gen"
)

$ErrorActionPreference = "Stop"
$DeployablePath = Join-Path $PSScriptRoot "deployable"
$Destination = "${HostName}:${RemotePath}/"

ssh $HostName "mkdir -p $RemotePath"
scp -r (Join-Path $DeployablePath "*") $Destination
