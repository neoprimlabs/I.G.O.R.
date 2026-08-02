<#
.SYNOPSIS
    Pull IGOR's memory files and .env off the server to local storage.

.DESCRIPTION
    memory/ is gitignored and .env is never committed, so both exist only on the
    host. Oracle terminated the instance once already (2026-07). This closes that
    single point of failure.

    Pull-based on purpose: the server has no git push credentials and no
    credential helper, so a server-side push would need a token provisioned
    first. This uses the SSH key that already works.

    Register as a scheduled task with -Install.

.PARAMETER Install
    Register a daily Scheduled Task instead of running a backup.

.PARAMETER Keep
    Number of dated archives to retain. Default 30.
#>
param(
    [switch]$Install,
    [int]$Keep = 30
)

$ErrorActionPreference = 'Stop'

$SshKey     = 'C:\Users\Nucbox\Documents\IGOR_Keys\ssh-key-2026-05-26.key'
$ServerHost = 'ubuntu@129.80.181.77'
$BackupRoot = 'C:\Dev\IGOR_backup'
$TaskName   = 'IGOR memory backup'

if ($Install) {
    $script = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    $trigger = New-ScheduledTaskTrigger -Daily -At 3am
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Pulls IGOR memory/ and .env off the server' -Force | Out-Null
    Write-Output "Registered scheduled task '$TaskName' (daily, 3am)."
    Write-Output "Run now with: Start-ScheduledTask -TaskName '$TaskName'"
    exit 0
}

$stamp   = Get-Date -Format 'yyyyMMdd-HHmm'
$archive = Join-Path $BackupRoot "igor-memory-$stamp.tar.gz"
$remote  = '/tmp/igor-memory-backup.tar.gz'

if (-not (Test-Path $BackupRoot)) {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
}

Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Archiving memory/ and .env on the server..."

# sudo tar because memory/ is owned by the igor user; chown so scp can read it.
$remoteCmd = "sudo tar czf $remote -C /opt/igor memory .env && sudo chown ubuntu:ubuntu $remote && stat -c%s $remote"
$size = & ssh -i $SshKey -o BatchMode=yes -o ConnectTimeout=20 $ServerHost $remoteCmd

if ($LASTEXITCODE -ne 0) {
    Write-Error "Server unreachable or archive failed (exit $LASTEXITCODE). IGOR may be down."
    exit 1
}

Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Downloading $size bytes..."
& scp -i $SshKey -o BatchMode=yes -q "${ServerHost}:$remote" $archive
if ($LASTEXITCODE -ne 0) {
    Write-Error "Download failed (exit $LASTEXITCODE)."
    exit 1
}

& ssh -i $SshKey -o BatchMode=yes $ServerHost "rm -f $remote" | Out-Null

if (-not (Test-Path $archive) -or (Get-Item $archive).Length -eq 0) {
    Write-Error "Archive is missing or empty: $archive"
    exit 1
}

Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Saved $archive ($((Get-Item $archive).Length) bytes)"

# Prune oldest, keeping $Keep most recent.
$all = Get-ChildItem $BackupRoot -Filter 'igor-memory-*.tar.gz' | Sort-Object LastWriteTime -Descending
if ($all.Count -gt $Keep) {
    $all | Select-Object -Skip $Keep | ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Output "  pruned $($_.Name)"
    }
}

Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Done. $([math]::Min($all.Count + 1, $Keep)) archives retained."
