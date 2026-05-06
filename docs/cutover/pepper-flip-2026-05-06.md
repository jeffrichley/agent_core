# Pepper cutover flip — 2026-05-06

**Operator-facing checklist for tomorrow morning's actual flip.** Practice-run record + diagnoses live in [`testbot-practice-run-2026-05-05.md`](testbot-practice-run-2026-05-05.md); this doc only contains executable steps. Read top-to-bottom; no skipping.

**Goal:** move Pepper from her old single-process synchronous-handoff substrate to the new bus-based architecture (identity injectors, daemon-side handoff worker, Discord adapter on the bus, scheduler endpoint, daily JSONL pipeline, brief framework available but not yet adopted).

---

## Pre-flip decisions (confirm before starting)

These have defaults; override only if you have a specific reason.

- [x] **Discord endpoint name: `discord-pepper`** (mirrors testbot's `discord-*` convention; matches what Bug #6's example placeholder assumes). *Confirmed 2026-05-06 morning.*
- [x] **Bus mailbox: `pepper`** (lowercase). Matches the example yaml; decouples bus routing from the human identity `"Pepper"` (Bug #5). *Confirmed 2026-05-06 morning.*
- [x] **testbot endpoints stay registered.** Same daemon serves both agents post-flip. testbot remains available for ad-hoc verification without spinning up new infra. Cost: 6 endpoints instead of 3 in `~/.agent-core/agent_core.yaml`. *Confirmed 2026-05-06 morning. Channel-allowlist lockdown for testbot deferred to post-flip work (untested today; DM workaround used instead — `dm_policy` defaults to `"open"`).*
- [x] **Brief framework: NOT adopted tomorrow.** Pepper's existing `morning-brief.md` is hand-authored prose with embedded JSON — converting it to the new orchestrator format is a separate design exercise. Tomorrow's flip is bus + identity + handoff + Discord verbs only. Brief framework migration scheduled later. *Confirmed 2026-05-06 morning.*
- [x] **Vault location stays at `C:\Users\jeffr\.pepper\Memory\pepper\`.** Cutover #06 dry-run already confirmed zero false-positive migration findings; no operator file moves needed (run [`agent-core vault plan-dry-run --base C:\Users\jeffr\.pepper`](test-playbooks/06-vault-continuity.md) one more time tomorrow to confirm). *Confirmed 2026-05-06 morning.*

---

## Pre-flip preparation (do BEFORE Pepper goes offline)

### 0. Comprehensive backup (do TONIGHT, then again tomorrow morning) — ✅ DONE 2026-05-06 07:57

Pepper was already stopped pre-backup (no log churn during snapshot), so we ran `-Mode clean` directly on three destinations: `C:\pepper-backups\`, `D:\pepper-backups\` (external), `E:\pepper-backups\` (HDD). 448.7 MB / 1146 files per snapshot. Critical files (`SOUL.md`, `IDENTITY.md`, `credentials.kdbx`, `scheduler.db`) SHA256-verified byte-identical across source + all three snapshots. Three independent failure surfaces confirmed.


**Don't lose Pepper.** The 4-file snapshot in earlier drafts of this checklist isn't enough — Pepper's full state spans `~/.pepper/` (195M including `credentials.kdbx`, `scheduler.db`, `attachments/`, identity files, the whole vault) PLUS `~/.claude/projects/C--Users-jeffr--pepper/` (237M of Claude Code session history + auto-memory).

Pepper's existing daily GitHub push to `pepperrichley/peppers-life` covers the Memory vault and tracked config but `.gitignore` excludes:
- `credentials.kdbx` (KeePass — encrypted but local-only)
- `google/` (OAuth tokens — refresh tokens you can't easily regenerate)
- `discord/` (access config)
- `scheduler.db` (the 17 active jobs from the inventory)
- `attachments/`
- `*.log`, `__pycache__`, etc.

And the entire `~/.claude/projects/C--Users-jeffr--pepper/` is outside `~/.pepper/` — in zero backup today.

A comprehensive backup script is pre-staged at [`pepper-flip-2026-05-06-config/backup-pepper.ps1`](pepper-flip-2026-05-06-config/backup-pepper.ps1). Run it twice:

**Tonight (live mode — Pepper still running, locked-DB-friendly via robocopy `/B`):**

```powershell
pwsh E:\workspaces\ai\agents\agent_core\docs\cutover\pepper-flip-2026-05-06-config\backup-pepper.ps1
# default destination: C:\pepper-backups\pepper-snapshot-<ts>-live\
```

Optional: also trigger Pepper's GitHub push tonight while she's still up so the cloud copy is current. Run from her bash:
```bash
bash ~/.pepper/hooks/backup-to-github.sh
```

**Tomorrow morning AFTER step 7 (clean mode — Pepper fully stopped, simple Copy-Item-style):**

```powershell
pwsh E:\workspaces\ai\agents\agent_core\docs\cutover\pepper-flip-2026-05-06-config\backup-pepper.ps1 -Mode clean
# refuses to run unless Pepper processes are gone
```

Both snapshots include:
- `~/.pepper/` (entire tree including .gitignored secrets + scheduler.db + attachments)
- `~/.claude/projects/C--Users-jeffr--pepper/` (session history + auto-memory)
- `~/.agent-core/agent_core.yaml` (daemon config that gets rewritten in step 10)
- A manifest.txt summarizing what was captured + any warnings

Total snapshot size ~432 MB. Two snapshots = ~864 MB. **Strongly recommend copying tonight's snapshot to off-disk storage** (external drive, second computer, encrypted cloud) since same-disk backup doesn't protect against drive failure. The script prints the recommendation when it finishes; the off-disk copy is your call.

### 1. Quick rollback set (small files, redundant with Step 0 but fast to read) — ✅ DONE 2026-05-06 08:00

Four files captured at `C:\Users\jeffr\.pepper-pre-cutover-20260506-080046-*`: agent_core.yaml (2025 b), daemon-agent_core.yaml (3347 b), handoff.md (3316 b), settings.json (1234 b).


The full backups in step 0 are the real safety net. This is just the four most-likely-to-be-edited config files extracted into a flat dir for quick `git diff`-style inspection if something looks wrong post-flip:

```powershell
$snap = "C:\Users\jeffr\.pepper-pre-cutover-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item C:\Users\jeffr\.pepper\agent_core.yaml "$snap-agent_core.yaml"
Copy-Item C:\Users\jeffr\.pepper\.claude\settings.json "$snap-settings.json"
Copy-Item C:\Users\jeffr\.pepper\Memory\pepper\handoff.md "$snap-handoff.md" -ErrorAction SilentlyContinue
Copy-Item C:\Users\jeffr\.agent-core\agent_core.yaml "$snap-daemon-agent_core.yaml"
Write-Output "rollback set: $snap-*"
```

### 2. Pull the latest agent-core repo

```powershell
cd E:\workspaces\ai\agents\agent_core
git status                    # confirm clean tree
git pull                      # should be at or past 363085f (last practice-run commit)
git log -1 --oneline          # eyeball latest commit
```

Expected: latest commit is `363085f docs(cutover): round-3 verification closes Obs 1 — sticky cache + fetch_user fallback` or newer.

### 3. Refresh the venv (covers blockers 1, 2, 4, 5, 6, 7, 8, 9 — all repo fixes)

```powershell
uv sync
uv run pytest -q              # sanity: 680 passing, 3 skipped
```

### 4. Reinstall the global agent-core CLI (Bug #3 fix — environmental, per-machine)

This is the **silent-failure** blocker. Pepper's hooks call `uv run agent-core hooks run <event>`; without `pyproject.toml` in `<agent_root>`, that falls back to the globally-installed CLI. The global tool was on the old `tool:` schema — every hook silently crashed and Claude Code swallowed the non-zero exit, producing **no error and no identity injection**.

```powershell
uv tool install --reinstall E:\workspaces\ai\agents\agent_core\packages\core
uv tool list | Select-String agent-core
```

Expected: `agent-core v...` listed, pointing at the editable-install path.

Smoke check the schema:

```powershell
echo '{}' | agent-core hooks run SessionStart 2>&1 | Select-String -Pattern "ValidationError|Identity|Time"
```

Expected: NO `ValidationError`. May see "Identity ... not configured" or similar — that's fine (we haven't pointed it at Pepper's yaml yet). What matters is the CLI parses its own config without complaint.

### 5. Re-run cutover #06 dry-run against Pepper's vault

```powershell
agent-core vault plan-dry-run --base C:\Users\jeffr\.pepper 2>&1 | Tee-Object pepper-vault-plan.txt
```

Expected: `configs: []`. If anything appears, do NOT proceed — investigate before the flip window.

### 5b. Snapshot Pepper's scheduled-tasks inventory

The 17 active jobs in `~/.pepper/scheduler.db` will go silent the moment we stop `pepper-scheduler.exe` in step 7. Pepper rebuilds her schedule against the new `SchedulerEndpoint` post-flip — but she needs to know what was there. The full inventory (with prompts decoded) is already captured at [`pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md`](pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md) — 532 lines, every job with cron, args, and full prompt text.

Independently, ask Pepper to take her own inventory pass before going offline (in her current session, before /exit):

> Inventory your active scheduled tasks. List every job: name, cron expression, what it does, what channel it posts to (if any), how critical it is. We're flipping you to a new substrate tomorrow morning and the scheduler doesn't auto-migrate — you'll recreate the schedule from your own list post-flip. Cross-reference with `docs/cutover/pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md` to confirm we both see the same set.

Two independent sources reduce the chance of a job being lost in the cutover.

### 6. Provision the Discord endpoint env file

```powershell
# Create or confirm the env file holding Pepper's Discord bot token.
$envPath = "C:\Users\jeffr\.agent-core\discord-pepper.env"
# Contents (one line):
#   DISCORD_PEPPER_TOKEN=<her_actual_bot_token>
```

If Pepper's existing setup already had a Discord token somewhere (`~/.pepper/discord/...`?), reuse it — but the `.env` file in `~/.agent-core/` is the new canonical location read by the bus's Discord endpoint.

Confirm the channel id Pepper currently posts to from her existing morning-brief: `1488680018077945978` (`#pepper-chat`, per her playbook header). Same channel post-flip.

---

## Cutover window (Pepper offline)

### 7. Stop Pepper's existing processes

Pepper's pre-cutover tree (audited 2026-05-05):

| Process | Role | Cutover action |
|---|---|---|
| `pepper.exe start` | Supervisor — auto-restarts the others | Stop FIRST so the others don't respawn |
| `pepper-discord.exe` | Holds Discord token gateway connection | **MUST stop** before `discord-pepper` starts (else gateway session conflict) |
| `pepper-scheduler.exe` | Fires the 17 scheduled jobs | Stop. Pepper recreates schedule from inventory post-flip (see [`pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md`](pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md)) |
| `pepper-channel.exe` chain (3 procs) | Old MCP server on **port 8788** | Stop. Daemon stays on 8789; port 8788 frees up |
| `claude.exe` (Pepper's session) | Active Claude Code session attached to pepper-channel | `/exit` gracefully BEFORE stopping pepper-channel |

You stop them; reboot is acceptable if anything's stuck. Then verify everything came down with this single check:

```powershell
$leftover = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*\.pepper\*' -or
    $_.CommandLine -like '*pepper-channel*' -or
    $_.CommandLine -like '*pepper-discord*' -or
    $_.CommandLine -like '*pepper-scheduler*' -or
    $_.ExecutablePath -like '*\uv\tools\pepper\*'
} | Select-Object ProcessId,Name,@{Name='Cmd';Expression={$_.CommandLine.Substring(0, [Math]::Min(120, $_.CommandLine.Length))}}
if ($leftover) {
    Write-Output "STILL RUNNING — review and kill or reboot:"
    $leftover | Format-Table -AutoSize -Wrap
} else {
    Write-Output "CLEAN — no Pepper processes remain. Safe to proceed to step 8."
}
```

If `STILL RUNNING` shows your active Claude Code window (the one driving the cutover), that's expected — exclude that one and treat anything else as something that needs killing or rebooting.

Optional: `uv tool uninstall pepper` to remove the old Python tool entirely. Not required for the flip — the tool can sit dormant — but it prevents accidental re-launch later.

### 8. Stop the bus daemon

```powershell
$pidPath = "C:\Users\jeffr\.agent-core\daemon.pid"
$old = (Get-Content $pidPath -Raw).Trim() -as [int]
Stop-Process -Id $old -ErrorAction Stop
```

### 9. Replace Pepper's project-scope yaml

A literal copy target is pre-staged at [`pepper-flip-2026-05-06-config/agent_core.yaml`](pepper-flip-2026-05-06-config/agent_core.yaml) — already filled in with Pepper's paths, mailbox, the post-fix port (8789), the three-block identity split, and the dedicated HandoffInjector. Just copy it:

```powershell
Copy-Item E:\workspaces\ai\agents\agent_core\docs\cutover\pepper-flip-2026-05-06-config\agent_core.yaml `
          C:\Users\jeffr\.pepper\agent_core.yaml -Force
```

Pepper's existing `agent_core.yaml` uses the deprecated `tool:` key and the old synchronous HandoffWriter — the copy fully replaces it.

Quick spot-checks against the new file (paranoia for tomorrow):
- `mailbox: "pepper"` present on PreCompact AND SessionEnd handoff_writer params.
- `handoff_jobs_url` port is **8789** (matches the daemon's operational port).
- `vault_root` is `C:\Users\jeffr\.pepper\Memory\pepper` (NOT the broader `Memory` dir).
- Five SessionStart blocks total: TimeInjector → 3× IdentityInjector (SOUL/IDENTITY/preferences) → HandoffInjector.

### 10. Add Pepper's endpoints to the daemon yaml

Edit `C:\Users\jeffr\.agent-core\agent_core.yaml` — keep testbot's endpoints, ADD these alongside:

```yaml
  - type: builtin.claude_code_mcp
    name: pepper
    description: "Pepper's MCP endpoint — flipped to the bus 2026-05-06."
    params:
      mount: /mcp/pepper
      briefs_orchestrator: briefs.pepper

  - type: builtin.briefs_orchestrator
    name: briefs.pepper
    description: "Pepper's briefs orchestrator (framework wired but not yet adopted by morning_brief)."
    params:
      playbooks_path: "C:\\Users\\jeffr\\.pepper\\Memory\\playbooks"
      fetcher_paths:
        - "C:\\Users\\jeffr\\.pepper\\Memory\\briefs\\fetchers"
      destination_paths:
        - "C:\\Users\\jeffr\\.pepper\\Memory\\briefs\\destinations"
      audit_log_path: "C:\\Users\\jeffr\\.agent-core\\briefs\\audit.jsonl"
      vars:
        agent_root: "C:\\Users\\jeffr\\.pepper"
      default_target_agent: "pepper"

  - type: builtin.discord
    name: discord-pepper
    description: "Discord adapter for Pepper. 13 verbs; engagement Events (poll votes, edits, deletes) verified live 2026-05-05 round-3."
    params:
      target: pepper
      token_env: DISCORD_PEPPER_TOKEN
      env_file: ~/.agent-core/discord-pepper.env
```

Note `briefs.pepper` is named distinctly from testbot's `briefs.orchestrator` so both can coexist. Pepper's MCP endpoint references it via `briefs_orchestrator: briefs.pepper`.

The `fetcher_paths` / `destination_paths` directories may not exist yet (Pepper hasn't authored framework-style fetchers). The orchestrator handles missing dirs gracefully — built-in fetchers/destinations always load; user-supplied dirs are merged when present, skipped when absent.

### 11. Restart the bus daemon

Same launch shape that's been working all day:

```powershell
$daemonLog = "C:\Users\jeffr\.agent-core\daemon.log"
if (Test-Path $daemonLog) { Move-Item $daemonLog "$daemonLog.pre-flip" -Force }
$errLog = "$daemonLog.err"
if (Test-Path $errLog) { Move-Item $errLog "$errLog.pre-flip" -Force }
$p = Start-Process -FilePath "E:\workspaces\ai\agents\agent_core\.venv\Scripts\python.exe" `
    -ArgumentList "-m","agent_core.cli","bus","run","--config","C:\Users\jeffr\.agent-core\agent_core.yaml" `
    -WindowStyle Hidden `
    -RedirectStandardOutput $daemonLog `
    -RedirectStandardError $errLog `
    -PassThru
$p.Id | Out-File "C:\Users\jeffr\.agent-core\daemon.pid" -Encoding ascii -NoNewline
Start-Sleep -Seconds 5
if (Get-Process -Id $p.Id -ErrorAction SilentlyContinue) { Write-Output "ALIVE pid=$($p.Id)" } else { Write-Output "DIED" }
```

Then confirm the new endpoints registered:

```powershell
Get-Content $daemonLog | Select-String -Pattern "ClaudeCodeMCPEndpoint|DiscordEndpoint|BriefsOrchestrator|HandoffJobs|bus running"
```

Expected to see (alongside the existing testbot lines):
- `ClaudeCodeMCPEndpoint(name=pepper) started at mount=/mcp/pepper`
- `BriefsOrchestratorEndpoint(name=briefs.pepper) started`
- `DiscordEndpoint(name=discord-pepper) started; target=pepper`
- `bus running — 9 endpoint(s) + http on :8789`

If `discord-pepper` doesn't connect to gateway, check `~/.agent-core/discord-pepper.env` exists, contains `DISCORD_PEPPER_TOKEN=...`, and is readable.

### 12. Bring Pepper online (start her first post-flip Claude Code session)

```powershell
cd C:\Users\jeffr\.pepper
claude   # or however Pepper's session normally launches
```

The first `SessionStart` hook should fire `agent-core hooks run SessionStart`, which now reads the new yaml.

---

## Post-flip smoke test (do BEFORE walking away)

Five quick checks that exercise the substrate end-to-end. Each should take under 60 seconds.

### A. SessionStart injection landed

In Pepper's first session, ask: *"What identity files did you just receive?"*

Expected: she names SOUL.md, IDENTITY.md, pepper/preferences.md, and (if present) pepper/handoff.md, each surfaced as its own block. If she says "I don't see any identity files," the hooks are silently failing — go check the daemon log + Bug #3's CLI reinstall.

### B. Discord round-trip

In Pepper's session: *"Send 'Pepper online — flipped to bus 2026-05-06' to her test channel."*

Expected: message lands in `#pepper-chat`. Echo a reply yourself — Pepper should surface the inbound `discord.message` Event in her inbox without you prompting her to look.

### C. Engagement Events surface

Add a 👍 reaction to Pepper's message in Discord. Expected: she sees a `discord.reaction_add` Event arrive unprompted, with `user_display_name` populated.

### D. Scheduler is alive

```powershell
agent-core scheduler list-jobs --config C:\Users\jeffr\.agent-core\agent_core.yaml
```

Expected: returns the scheduler's persisted jobs (may be empty post-flip if Pepper hasn't recreated any). At minimum, no error.

### E. Handoff round-trip works

End Pepper's session via `/exit`. The daemon worker should pick up the SessionEnd job and write `C:\Users\jeffr\.pepper\Memory\pepper\handoff.md` + `handoff-status.json` within 30 seconds.

```powershell
Get-Item C:\Users\jeffr\.pepper\Memory\pepper\handoff-status.json | Select-Object LastWriteTime
Get-Content C:\Users\jeffr\.pepper\Memory\pepper\handoff-status.json
```

Expected: status file shows `state: "ready"` and `mtime` within the last minute.

If all five smoke checks pass: **flip is done.** Pepper is on the new substrate.

---

## Rollback (if smoke test fails on something irrecoverable)

The snapshots from step 1 are the recovery set. To roll back:

```powershell
# Stop the new daemon
$pid = (Get-Content C:\Users\jeffr\.agent-core\daemon.pid -Raw).Trim() -as [int]
Stop-Process -Id $pid

# Restore old yamls (use the actual snapshot filenames from step 1)
Copy-Item "$snap-agent_core.yaml" C:\Users\jeffr\.pepper\agent_core.yaml -Force
Copy-Item "$snap-daemon-agent_core.yaml" C:\Users\jeffr\.agent-core\agent_core.yaml -Force

# Reinstall whichever agent-core CLI version Pepper was on previously
# (if you don't remember, ``git log packages/core -1 --before='2026-05-04'``
#  on the repo gives the SHA; ``uv tool install --reinstall`` against
#  that checkout is the recovery path)
```

Pepper's `.claude/settings.json` doesn't need rollback — the hook commands (`uv run agent-core hooks run *`) are unchanged across versions; only the yaml schema changed.

---

## Post-flip follow-ups (NOT blocking the flip)

These are explicitly out of scope for tomorrow morning:

- **Pepper recreates her 17 scheduled tasks.** Cross-reference her own inventory against [`pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md`](pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md) and recreate each via the new `SchedulerEndpoint`'s `create_job` MCP tool. Two architectural notes:
  - Most jobs (`morning_briefing`, `daily_sync`, `evening_routine`, `weekly_*`, etc.) are prompt-driven and drop into the new shape directly — `SchedulerEndpoint` publishes a `TextMessage` envelope to Pepper at the cron time; she acts on the prompt.
  - Infrastructure jobs (`attachment_cleanup`, `vault_backup`, `github_backup`, `heartbeat`) used `pepper.scheduler.core:execute_function_job` to invoke Python directly. The new substrate doesn't have an equivalent — these need a different shape (Pepper-authored subprocess invocation? Windows Task Scheduler? Skip and re-evaluate?). Pepper's call.
  - **Time-critical:** `whoi_trip_briefing` is date-triggered for **2026-05-08 09:00 ET** (Friday). Recreate this one first.
  - **Daily-critical:** `morning_briefing` fires 7:28 ET. If it's not recreated by then, tomorrow morning is silent — but you'll be awake doing the cutover at that point, so just recreate it before walking away.
- **Brief framework adoption.** Migrate `morning-brief.md` from hand-authored prose-with-JSON to the new `brief_type: morning_brief` MD/YAML format. Design exercise: section specs, color palette, fetcher catalog (calendar / gmail / tasks / projects / weather), gather YAML. Delivery via `discord_embed` + `markdown_file` destinations. Until then, Pepper's existing morning-brief flow continues to work as a Pepper-authored process. Pepper already knows she'll need to rebuild her briefs.
- **issue [#33](https://github.com/jeffrichley/agent_core/issues/33)** — bus wake-builder count + urgency_max snapshot lag. Confirmed 4+ times during the practice run; functionally harmless. Pick up when convenient.
- **Briefs CLI UX** — duplicate `--fetcher-path` to the built-in dir produces a confusing "duplicate type_id" error. Loader should de-dupe paths.
- **Claude Code skill loading from secondary working directories.** testbot noticed three skills in `e:\workspaces\businesses\47tabs\.claude\skills` aren't surfaced. Likely intentional isolation; worth confirming with Anthropic.

---

## What this flip does NOT change

- Pepper's identity files (SOUL.md, IDENTITY.md, preferences.md) — content unchanged, just newly injected per-block by separate IdentityInjector hooks.
- Pepper's vault layout under `C:\Users\jeffr\.pepper\Memory\` — unchanged. Cutover #06's dry-run is the verification.
- Pepper's existing `~/.claude/settings.json` hook bindings — already point to `agent-core hooks run *`; only the underlying yaml schema changes.
- Pepper's morning-brief delivery shape — still hand-authored, still posts to `#pepper-chat`. Brief framework adoption deferred.
- testbot's setup — endpoints stay registered, available for ad-hoc verification post-flip.
