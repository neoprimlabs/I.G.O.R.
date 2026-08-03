<#
.SYNOPSIS
    Back up IGOR's memory files and alert on Discord if the bot is unhealthy.

.DESCRIPTION
    Two jobs in one daily pass, because both need the same SSH session:

    1. Pull memory/ and .env off the server. Both are gitignored, so until this
       existed they lived only on a host Oracle has already terminated once.
    2. Check that IGOR is actually running, and alert via Discord webhook if not.

    Checking the service matters: an SSH-only check succeeds while the host is up
    and the bot is dead, which is the most common failure mode. The webhook is
    used rather than IGOR itself because the alert has to work when IGOR cannot.

    Alerts fire on: server unreachable, backup failure, service not active, or
    the service restarting repeatedly (crash loop). One all-clear is sent weekly
    so that prolonged silence is itself a signal that this task stopped running.

.PARAMETER Install
    Register a daily Scheduled Task instead of running a backup.

.PARAMETER Keep
    Number of dated archives to retain. Default 30.

.PARAMETER TestAlert
    Send a test alert and exit. Verifies the webhook path without breaking IGOR.
#>
param(
    [switch]$Install,
    [switch]$TestAlert,
    [int]$Keep = 30
)

$ErrorActionPreference = 'Stop'

# PowerShell 5.1 still negotiates TLS 1.0 by default and Discord refuses it.
# Without this the alert fails silently at exactly the moment it is needed.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$SshKey      = 'C:\Users\Nucbox\Documents\IGOR_Keys\ssh-key-2026-05-26.key'
$WebhookFile = 'C:\Users\Nucbox\Documents\IGOR_Keys\discord_webhook.txt'
$ServerHost  = 'ubuntu@129.80.181.77'
$BackupRoot  = 'C:\Dev\IGOR_backup'
$StateFile   = Join-Path $BackupRoot '.watchdog-state.json'
$TaskName    = 'IGOR memory backup'

# Restart count growth above this between runs is treated as a crash loop.
$RestartThreshold = 3


function Send-Alert {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$Detail
    )
    if (-not (Test-Path $WebhookFile)) {
        Write-Warning "No webhook file at $WebhookFile - cannot alert."
        return
    }
    $url = (Get-Content $WebhookFile -Raw).Trim()
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
    $text = "**$Title**`n$Detail`n`nHost: 129.80.181.77`nChecked: $stamp"
    $body = @{ username = 'IGOR Watchdog'; content = $text } | ConvertTo-Json -Compress
    try {
        Invoke-WebRequest -Uri $url -Method Post -Body $body `
            -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30 | Out-Null
        Write-Output "  alert sent: $Title"
    } catch {
        # Never let a failed alert mask the original problem.
        Write-Warning "Alert delivery failed: $($_.Exception.Message)"
    }
}


if ($Install) {
    $script = $MyInvocation.MyCommand.Path
    # conhost --headless runs the console host with no window at all. A plain
    # powershell.exe action under an Interactive logon opens a real console in the
    # user's session - at 3am that stole focus from a fullscreen game. -WindowStyle
    # Hidden alone still flashes. Interactive logon is kept deliberately: S4U would
    # hide it too but strips the network token, and this task needs SSH.
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
    $trigger = New-ScheduledTaskTrigger -Daily -At 3am
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Backs up IGOR memory and alerts if the bot is down' -Force | Out-Null
    Write-Output "Registered scheduled task '$TaskName' (daily, 3am)."
    exit 0
}

if ($TestAlert) {
    Send-Alert -Title 'Test alert' -Detail 'Triggered manually with -TestAlert. IGOR is fine.'
    exit 0
}

if (-not (Test-Path $BackupRoot)) {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
}

$stamp   = Get-Date -Format 'yyyyMMdd-HHmm'
$archive = Join-Path $BackupRoot "igor-memory-$stamp.tar.gz"
$remote  = '/tmp/igor-memory-backup.tar.gz'

# One SSH round trip does health checks and builds the archive. Output is a
# key=value block for easy parsing.
#
# No double quotes anywhere in this script on purpose. Windows argument escaping
# mangles embedded quotes on the way to ssh.exe, which silently broke an earlier
# version that used date -d "$started" - the value contains spaces, arrived
# unquoted, and split. Uptime now comes from the monotonic clock instead, so no
# value in this block ever contains a space.
$remoteScript = @'
echo ACTIVE=$(systemctl is-active igor)
echo WATCHDOG=$(systemctl is-active igor-watchdog)
mono=$(systemctl show igor --property=ActiveEnterTimestampMonotonic --value)
up=$(cut -d. -f1 /proc/uptime)
echo UPTIME_S=$(( up - mono/1000000 ))
echo NRESTARTS=$(systemctl show igor --property=NRestarts --value)
sudo tar czf REMOTE_PATH -C /opt/igor memory .env 2>/dev/null && sudo chown ubuntu:ubuntu REMOTE_PATH
echo TAR_SIZE=$(stat -c%s REMOTE_PATH 2>/dev/null || echo 0)
'@ -replace 'REMOTE_PATH', $remote

Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Checking IGOR and archiving memory..."

# ErrorActionPreference must drop to Continue around native calls that redirect
# stderr. In PowerShell 5.1, 2>&1 on an exe wraps each stderr line in a
# NativeCommandError, which under Stop terminates the script. That killed this
# script before it could alert - on the unreachable-host path, which is the one
# case the whole watchdog exists for.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$raw = & ssh -i $SshKey -o BatchMode=yes -o ConnectTimeout=20 $ServerHost $remoteScript 2>&1
$sshRc = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

if ($sshRc -ne 0) {
    $detail = "Could not reach the server over SSH (exit $sshRc). The host may be down, terminated, or its IP may have changed.`n``````n$($raw -join "`n")`n``````"
    Write-Output "[$(Get-Date -Format 'HH:mm:ss')] SERVER UNREACHABLE"
    Send-Alert -Title 'IGOR unreachable' -Detail $detail
    exit 1
}

# Parse the key=value block.
$info = @{}
foreach ($line in $raw) {
    if ($line -match '^([A-Z_]+)=(.*)$') { $info[$matches[1]] = $matches[2].Trim() }
}

$active    = $info['ACTIVE']
$watchdog  = $info['WATCHDOG']
$uptime    = [int]($info['UPTIME_S']    | ForEach-Object { if ($_) { $_ } else { -1 } })
$nrestarts = [int]($info['NRESTARTS']   | ForEach-Object { if ($_) { $_ } else { 0 } })
$tarSize   = [int64]($info['TAR_SIZE']  | ForEach-Object { if ($_) { $_ } else { 0 } })

Write-Output "  igor=$active watchdog=$watchdog uptime=${uptime}s restarts=$nrestarts"

# --- Download the archive -------------------------------------------------
$backupOk = $false
if ($tarSize -gt 0) {
    & scp -i $SshKey -o BatchMode=yes -q "${ServerHost}:$remote" $archive
    if ($LASTEXITCODE -eq 0 -and (Test-Path $archive) -and (Get-Item $archive).Length -gt 0) {
        $backupOk = $true
        Write-Output "  saved $archive ($((Get-Item $archive).Length) bytes)"
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & ssh -i $SshKey -o BatchMode=yes $ServerHost "rm -f $remote" 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP
}

# --- Evaluate health ------------------------------------------------------
$problems = @()

if ($active -ne 'active') {
    $problems += "The igor service is **$active**, not active. IGOR is offline."
}
if ($watchdog -ne 'active') {
    $problems += "The igor-watchdog service is **$watchdog**. Automatic restarts are not covered."
}
if (-not $backupOk) {
    $problems += "Memory backup FAILED. The server answered but memory/ and .env were not retrieved."
}

# Crash-loop detection: compare restart count against the previous run.
$prev = $null
if (Test-Path $StateFile) {
    try { $prev = Get-Content $StateFile -Raw | ConvertFrom-Json } catch { $prev = $null }
}
if ($prev -and $prev.NRestarts -ne $null) {
    $delta = $nrestarts - [int]$prev.NRestarts
    if ($delta -ge $RestartThreshold) {
        $problems += "IGOR restarted $delta times since the last check. That is a crash loop, not a deploy."
    }
}

@{ NRestarts = $nrestarts; LastRun = (Get-Date -Format 'o'); Active = $active } |
    ConvertTo-Json | Set-Content $StateFile -Encoding utf8

# --- Prune ----------------------------------------------------------------
$all = Get-ChildItem $BackupRoot -Filter 'igor-memory-*.tar.gz' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending
if ($all -and $all.Count -gt $Keep) {
    $all | Select-Object -Skip $Keep | ForEach-Object { Remove-Item $_.FullName -Force }
}

# --- Report ---------------------------------------------------------------
if ($problems.Count -gt 0) {
    $detail = ($problems | ForEach-Object { "- $_" }) -join "`n"
    Write-Output "[$(Get-Date -Format 'HH:mm:ss')] PROBLEMS FOUND"
    $problems | ForEach-Object { Write-Output "  ! $_" }
    Send-Alert -Title 'IGOR needs attention' -Detail $detail
    exit 1
}

# Weekly all-clear so that silence is not mistaken for health.
if ((Get-Date).DayOfWeek -eq 'Sunday') {
    $days = if ($uptime -ge 0) { [math]::Round($uptime / 86400, 1) } else { 'unknown' }
    Send-Alert -Title 'IGOR weekly check: all clear' `
        -Detail "Service active, uptime $days days, $($all.Count) backups retained. Nothing needed."
}

Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Healthy. $($all.Count) archives retained."
exit 0
