# Comprehensive Pepper backup — pre-cutover safety net.
#
# Pepper's existing backups don't cover everything:
#   - GitHub repo `pepperrichley/peppers-life` (daily 4 AM ET) — covers
#     Memory/ vault + tracked config, BUT .gitignore excludes:
#       * credentials.kdbx (KeePass — encrypted, but still local-only)
#       * google/ (OAuth tokens)
#       * discord/ (access config; potentially tokens)
#       * scheduler.db (the 17 active jobs)
#       * attachments/ (11M of downloaded content)
#       * *.log, __pycache__, etc.
#   - Daily local tarballs in ~/.pepper/backups/ — same disk; .gitignored
#     things ARE included, but only as same-disk archives.
#   - ~/.claude/projects/C--Users-jeffr--pepper/ (237M Claude Code session
#     history + auto-memory) — outside ~/.pepper/, in NO backup today.
#
# This script creates a complete local snapshot of everything, with
# robocopy /B for live (Pepper still running) mode so locked DBs and
# in-progress JSONL files copy cleanly via VSS-friendly backup mode.
#
# Usage:
#   pwsh .\backup-pepper.ps1                           # default destination
#   pwsh .\backup-pepper.ps1 -Destination D:\backups\  # different drive
#   pwsh .\backup-pepper.ps1 -Mode clean               # after Pepper stops
#
# Run TONIGHT (live mode) AND tomorrow morning AFTER step 7 of the flip
# checklist (clean mode, with Pepper fully stopped). Two snapshots reduce
# the chance of a corrupted DB or in-flight write costing recoverability.

[CmdletBinding()]
param(
    [string]$Destination = "C:\pepper-backups",
    [ValidateSet("live","clean")]
    [string]$Mode = "live"
)

$ErrorActionPreference = "Stop"

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$dest = Join-Path $Destination "pepper-snapshot-$ts-$Mode"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Write-Host ""
Write-Host "=== Pepper backup ==="
Write-Host "Mode:        $Mode"
Write-Host "Destination: $dest"
Write-Host ""

# Sanity check — refuse to run live mode if Pepper is offline (would be
# pointless), refuse clean mode if Pepper still running (defeats the
# purpose of a clean snapshot).
$pepperRunning = $null -ne (Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*pepper-channel*' -or
    $_.CommandLine -like '*pepper-discord*' -or
    $_.CommandLine -like '*pepper-scheduler*'
})
if ($Mode -eq "live" -and -not $pepperRunning) {
    Write-Warning "Live mode but no Pepper processes detected — proceeding anyway, but did you mean -Mode clean?"
}
if ($Mode -eq "clean" -and $pepperRunning) {
    throw "Clean mode requires Pepper to be fully stopped. Live processes detected — re-run after step 7 of the flip checklist."
}

# Source list — order matters for the manifest.
$sources = @(
    @{
        Name = "pepper"
        Path = "C:\Users\jeffr\.pepper"
        SubDest = "pepper"
        Critical = @("Memory\SOUL.md", "Memory\IDENTITY.md", "credentials.kdbx", "scheduler.db", "agent_core.yaml")
    },
    @{
        Name = "claude-projects-pepper"
        Path = "C:\Users\jeffr\.claude\projects\C--Users-jeffr--pepper"
        SubDest = "claude-projects-pepper"
        Critical = @("memory")
    }
)

# Robocopy flags:
#   /E       — copy subdirectories including empty ones
#   /B       — backup mode (handles locked files; live mode only)
#   /R:2 /W:1 — retry twice with 1s wait on transient failures
#   /XJ      — skip junction points (avoids reparse-point loops)
#   /NFL /NDL /NP — quieter output (no per-file/dir/progress noise; we
#                    log to file instead)
$rcCommon = @("/E", "/R:2", "/W:1", "/XJ", "/NFL", "/NDL", "/NP")
if ($Mode -eq "live") { $rcCommon += "/B" }

$step = 1
$totalSteps = $sources.Count + 2  # +1 for daemon yaml, +1 for verification
$failures = @()

foreach ($src in $sources) {
    Write-Host "[$step/$totalSteps] Mirroring $($src.Path)..."
    $subDest = Join-Path $dest $src.SubDest
    $logPath = Join-Path $dest "robocopy-$($src.Name).log"

    if (-not (Test-Path $src.Path)) {
        Write-Warning "  SOURCE MISSING: $($src.Path) — skipping"
        $failures += "Source missing: $($src.Path)"
        $step++
        continue
    }

    $args = @($src.Path, $subDest) + $rcCommon + @("/LOG:$logPath")
    & robocopy @args | Out-Null

    # Robocopy exit codes: 0 = no copy needed, 1 = files copied OK,
    # 2-7 = informational (extras, mismatches), 8+ = real failure.
    if ($LASTEXITCODE -ge 8) {
        Write-Warning "  Robocopy exit=$LASTEXITCODE for $($src.Path) — see $logPath"
        $failures += "Robocopy failed for $($src.Path): exit=$LASTEXITCODE"
    } else {
        Write-Host "  OK (robocopy exit=$LASTEXITCODE)"
    }
    $step++
}

# Daemon yaml (single file — will be modified tomorrow when Pepper's
# endpoints get added to it).
Write-Host "[$step/$totalSteps] Snapshot of daemon yaml..."
$daemonYaml = "C:\Users\jeffr\.agent-core\agent_core.yaml"
if (Test-Path $daemonYaml) {
    Copy-Item $daemonYaml (Join-Path $dest "daemon-agent_core.yaml") -Force
    Write-Host "  OK"
} else {
    Write-Warning "  DAEMON YAML MISSING: $daemonYaml"
    $failures += "Daemon yaml missing"
}
$step++

# Manifest — what's in the snapshot.
$totalSize = (Get-ChildItem $dest -Recurse -File -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
$totalSizeMB = [math]::Round($totalSize / 1MB, 1)
$fileCount = (Get-ChildItem $dest -Recurse -File -ErrorAction SilentlyContinue).Count

$manifest = @"
Pepper pre-cutover backup
Created:         $ts
Mode:            $Mode
Destination:     $dest
Total size:      $totalSizeMB MB
Total files:     $fileCount

Sources mirrored:
"@
foreach ($src in $sources) {
    $subDest = Join-Path $dest $src.SubDest
    if (Test-Path $subDest) {
        $sz = ((Get-ChildItem $subDest -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum) / 1MB
        $cnt = (Get-ChildItem $subDest -Recurse -File -ErrorAction SilentlyContinue).Count
        $manifest += "`n  - $($src.Path)"
        $manifest += "`n      → $subDest"
        $manifest += "`n      $([math]::Round($sz, 1)) MB, $cnt files"
    }
}
if ($failures.Count -gt 0) {
    $manifest += "`n`nWarnings:`n"
    foreach ($f in $failures) { $manifest += "  - $f`n" }
}
$manifest | Out-File (Join-Path $dest "manifest.txt") -Encoding utf8

# Verification — sample-test critical files.
Write-Host "[$step/$totalSteps] Verifying critical files..."
$verifyOk = $true
foreach ($src in $sources) {
    foreach ($critical in $src.Critical) {
        $full = Join-Path (Join-Path $dest $src.SubDest) $critical
        if (Test-Path $full) {
            Write-Host "  OK: $($src.SubDest)\$critical"
        } else {
            Write-Warning "  MISSING: $($src.SubDest)\$critical"
            $verifyOk = $false
        }
    }
}

Write-Host ""
Write-Host "=== Summary ==="
Write-Host "Snapshot:     $dest"
Write-Host "Total:        $totalSizeMB MB across $fileCount files"
if ($failures.Count -gt 0) { Write-Warning "$($failures.Count) warning(s) recorded in manifest.txt" }
if (-not $verifyOk) { Write-Warning "Critical-file verification incomplete" }
if ($verifyOk -and $failures.Count -eq 0) {
    Write-Host "BACKUP VERIFIED — safe to proceed." -ForegroundColor Green
} else {
    Write-Host "BACKUP COMPLETED WITH WARNINGS — review manifest before relying on it." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Recommended next step:"
Write-Host "  Copy this snapshot to off-disk storage (external drive or different machine)."
Write-Host "  Same-drive backup protects against accidental deletion + flip mishaps but"
Write-Host "  not disk failure. ~$totalSizeMB MB transfer."
Write-Host ""
