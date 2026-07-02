param(
    [string]$HostName = "root@134.122.9.101",
    [string]$RemotePath = "/opt/kalshi-weather-next-gen",
    [switch]$NoDelete,
    [string]$KeyPath = "$env:USERPROFILE\.ssh\id_ed25519",
    [switch]$NoAgent
)

$ErrorActionPreference = "Stop"
$DeployablePath = Join-Path $PSScriptRoot "deployable"
$Destination = "${HostName}:${RemotePath}/"
$ArchiveName = "kalshi-weather-next-gen-deploy.tar.gz"

function Use-SshAgent {
    param([string]$IdentityPath)

    if ($NoAgent) {
        return
    }
    if (-not (Test-Path $IdentityPath)) {
        Write-Warning "SSH key not found at $IdentityPath; continuing without ssh-agent."
        return
    }

    $agent = Get-Service ssh-agent -ErrorAction SilentlyContinue
    if ($agent -and $agent.Status -ne "Running") {
        try {
            Start-Service ssh-agent
        } catch {
            Write-Warning "Could not start ssh-agent. Run 'Start-Service ssh-agent' as admin or use -NoAgent."
            return
        }
    }

    $loadedKeys = (& ssh-add -l 2>$null) -join "`n"
    $loadedExit = $LASTEXITCODE
    $fingerprintLine = (& ssh-keygen -lf $IdentityPath 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0 -or -not $fingerprintLine) {
        Write-Warning "Could not read SSH key fingerprint for $IdentityPath; continuing without ssh-agent."
        return
    }

    $fingerprint = ($fingerprintLine -split "\s+")[1]
    if ($loadedExit -eq 0 -and $loadedKeys.Contains($fingerprint)) {
        Write-Host "SSH key already loaded in ssh-agent."
        return
    }

    Write-Host "Loading SSH key into ssh-agent. You should only need to type the passphrase once."
    ssh-add $IdentityPath
    if ($LASTEXITCODE -ne 0) {
        throw "ssh-add failed for $IdentityPath"
    }
}

if (-not (Test-Path $DeployablePath)) {
    throw "Deployable folder not found: $DeployablePath"
}

Use-SshAgent -IdentityPath $KeyPath

$RemoteSafePath = "'" + $RemotePath.Replace("'", "'\''") + "'"
ssh $HostName "mkdir -p $RemoteSafePath"

$Rsync = Get-Command rsync -ErrorAction SilentlyContinue
if ($Rsync) {
    $DeleteArgs = @()
    if (-not $NoDelete) {
        $DeleteArgs = @("--delete")
    }

    rsync -avz @DeleteArgs `
        --exclude ".env" `
        --exclude ".venv/" `
        --exclude "*.pem" `
        --exclude "*.key" `
        --exclude "collector_spool*/" `
        --exclude "logs/" `
        --exclude "exports/" `
        "$DeployablePath/" `
        $Destination
} else {
    $Tar = Get-Command tar -ErrorAction SilentlyContinue
    if (-not $Tar) {
        throw "Neither rsync nor tar is available. Install rsync or make tar available on PATH."
    }

    $TempArchive = Join-Path ([System.IO.Path]::GetTempPath()) $ArchiveName
    if (Test-Path $TempArchive) {
        Remove-Item -LiteralPath $TempArchive -Force
    }

    tar -czf $TempArchive -C $DeployablePath .
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create deployment archive."
    }

    try {
        scp $TempArchive "${HostName}:/tmp/$ArchiveName"
        if ($LASTEXITCODE -ne 0) {
            throw "Archive upload failed."
        }

        $CleanCommand = ""
        if (-not $NoDelete) {
            $CleanCommand = @"
find . -mindepth 1 -maxdepth 1 \
  ! -name '.env' \
  ! -name '.venv' \
  ! -name '*.pem' \
  ! -name '*.key' \
  ! -name 'collector_spool*' \
  ! -name 'logs' \
  ! -name 'exports' \
  -exec rm -rf {} +;
"@
        }

        ssh $HostName @"
set -e
mkdir -p $RemoteSafePath
cd $RemoteSafePath
$CleanCommand
tar -xzf /tmp/$ArchiveName
rm -f /tmp/$ArchiveName
"@
        if ($LASTEXITCODE -ne 0) {
            throw "Remote extract failed."
        }
    } finally {
        Remove-Item -LiteralPath $TempArchive -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Deployed $DeployablePath to $Destination"
