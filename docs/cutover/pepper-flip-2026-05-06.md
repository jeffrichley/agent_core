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

### 2. Pull the latest agent-core repo — ✅ DONE 2026-05-06 08:01

Tree clean (after committing the in-progress doc edits as `289adf3`); `git pull` returned "Already up to date" since local was ahead post-push; HEAD at `289adf3 docs(cutover): flip in progress 2026-05-06 — decisions + Steps 0+1 done`, well past the `363085f` threshold.


```powershell
cd E:\workspaces\ai\agents\agent_core
git status                    # confirm clean tree
git pull                      # should be at or past 363085f (last practice-run commit)
git log -1 --oneline          # eyeball latest commit
```

Expected: latest commit is `363085f docs(cutover): round-3 verification closes Obs 1 — sticky cache + fetch_user fallback` or newer.

### 3. Refresh the venv (covers blockers 1, 2, 4, 5, 6, 7, 8, 9 — all repo fixes) — ✅ DONE 2026-05-06 08:02

`uv sync`: 140 packages resolved, 135 audited, no changes (venv was already current). `pytest -q`: **680 passed, 3 skipped, 46.35s** — same as yesterday's post-fix baseline.


```powershell
uv sync
uv run pytest -q              # sanity: 680 passing, 3 skipped
```

### 4. Reinstall the global agent-core CLI (Bug #3 fix — environmental, per-machine) — ✅ DONE 2026-05-06 08:07

`uv tool install --reinstall E:\workspaces\ai\agents\agent_core\packages\core` reinstalled `agent-core.exe`. Smoke check: piped `{}` to `agent-core hooks run SessionStart` from the repo cwd; CLI registered 3 tools using the new `builtin.*` schema (NOT the old `tool: agent_core.hooks.tools...` shape that was crashing silently), executed builtin.time_injector cleanly, returned `hookSpecificOutput` with current timestamp. **No ValidationError.** Bug #3 fix verified.


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

### 5. Re-run cutover #06 dry-run against Pepper's vault — ✅ DONE 2026-05-06 08:09

Doc-bug found mid-flip: original command used `--base` (no such flag; the CLI takes `--vault`) and didn't pass `--config`, which would have made the `configs: []` expectation tautologically true. Corrected command:

```powershell
agent-core vault plan-dry-run `
    --vault "C:\Users\jeffr\.pepper\Memory" `
    --config "E:\workspaces\ai\agents\agent_core\docs\cutover\pepper-flip-2026-05-06-config\agent_core.yaml" `
    2>&1 | Tee-Object pepper-vault-plan.txt
```

Real freshness check: `vault_layout.missing_recommended_files` empty, `vault_layout.missing_recommended_dirs` empty, every `absolute_paths` entry resolves to either an existing file/dir OR an expected-to-be-created file. Result: ✅ — 4 absolute paths in the new yaml (`C:\Users\jeffr\.pepper\Memory`, `C:\Users\jeffr\.pepper\Memory\pepper`, `handoff-status.json`, `handoff.md`); 3 exist, `handoff-status.json` is expected-created-on-first-SessionEnd. Vault layout is clean.

### 5b. Snapshot Pepper's scheduled-tasks inventory — ✅ DONE 2026-05-06 08:11

`scheduler.db` re-read confirmed unchanged from yesterday: same 17 IDs, same set as the decoded inventory at `docs/cutover/pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md`. **Pepper's own inventory pass deferred to post-flip** (she was stopped before the backup window, so couldn't do a pre-shutdown pass). Cross-reference happens when she's back online: she reads the decoded inventory, flags anything missing/wrong, and we dig out the binary `scheduler.db` from any of the 3 backups if needed.


The 17 active jobs in `~/.pepper/scheduler.db` will go silent the moment we stop `pepper-scheduler.exe` in step 7. Pepper rebuilds her schedule against the new `SchedulerEndpoint` post-flip — but she needs to know what was there. The full inventory (with prompts decoded) is already captured at [`pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md`](pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md) — 532 lines, every job with cron, args, and full prompt text.

Independently, ask Pepper to take her own inventory pass before going offline (in her current session, before /exit):

> Inventory your active scheduled tasks. List every job: name, cron expression, what it does, what channel it posts to (if any), how critical it is. We're flipping you to a new substrate tomorrow morning and the scheduler doesn't auto-migrate — you'll recreate the schedule from your own list post-flip. Cross-reference with `docs/cutover/pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md` to confirm we both see the same set.

Two independent sources reduce the chance of a job being lost in the cutover.

### 6. Provision the Discord endpoint env file — ✅ DONE 2026-05-06 08:14

`C:\Users\jeffr\.agent-core\discord-pepper.env` created (Jeff filled in the real token, never echoed back). Validator confirmed: file present, format `DISCORD_PEPPER_TOKEN=<value>`, token 72 chars, 3 dot-separated parts (Discord canonical shape). Same env-file pattern as testbot's `discord-testbot.env`.


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

### 7. Stop Pepper's existing processes — ✅ DONE 2026-05-06 08:18

Pepper was already stopped before we started (Jeff brought her down pre-backup so heartbeats wouldn't churn the logs during snapshot). Re-verified with a targeted process check at 08:18: no `pepper-channel.exe`, `pepper-discord.exe`, `pepper-scheduler.exe`, `pepper.exe`, or `uv\tools\pepper\` python instances running. The original verification's broader pattern (`*\.pepper\*` in CommandLine) flagged a Notepad with `~/.pepper/.env` open, which is a false positive (text editor with a file open ≠ Pepper service); refined check excludes editors. Discord token is already free for `discord-pepper` to claim on next daemon boot.


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

### 8. Stop the bus daemon — ✅ DONE 2026-05-06 08:18

Read `~/.agent-core/daemon.pid` → 13936 (the one started yesterday 16:49 for Obs 1 round-3 verification). `Stop-Process` succeeded; verified gone.


```powershell
$pidPath = "C:\Users\jeffr\.agent-core\daemon.pid"
$old = (Get-Content $pidPath -Raw).Trim() -as [int]
Stop-Process -Id $old -ErrorAction Stop
```

### 9. Replace Pepper's project-scope yaml — ✅ DONE 2026-05-06 08:21

`Copy-Item` from `docs/cutover/pepper-flip-2026-05-06-config/agent_core.yaml` (5256 bytes, new shape) to `C:\Users\jeffr\.pepper\agent_core.yaml` (overwrote the 2025-byte old yaml with the deprecated `tool:` schema). Paranoia checks: `mailbox: "pepper"` ✅; `handoff_jobs_url` port 8789 ✅; `vault_root` scoped to `Memory\pepper` ✅; no old `tool:` key ✅; 5 SessionStart injector entries + 1 UserPromptSubmit time_injector ✅.


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

### 9b. Replace Pepper's `.mcp.json` (CHECKLIST GAP — caught mid-flip) — ✅ DONE 2026-05-06 08:38

**Missed step.** Step 9 only handled `~/.pepper/agent_core.yaml` (hook pipeline). Claude Code reads a separate `~/.pepper/.mcp.json` to decide which MCP servers to launch on session start. Pepper's old `.mcp.json` registered `pepper-channel` / `pepper-discord` / `pepper-scheduler` (the OLD substrate). When she launched with `--dangerously-load-development-channels server:agent-core-channel`, Claude Code loaded `agent-core-channel` ADDITIONALLY but ALSO honored the three pepper-* servers from `.mcp.json`. Her test send hit `mcp__pepper-discord__send_discord_message` (old adapter) — landed in Discord with a real message_id but **zero traffic in the bus daemon log for `pepper`**. testbot escaped this trap because her `.mcp.json` was already migrated cleanly during initial setup; only Pepper's was left on the old shape.

Fix: replaced `~/.pepper/.mcp.json` with the bus-pointing shape, mirroring testbot's:

```json
{
  "mcpServers": {
    "agent-core":         { "type": "http",  "url": "http://localhost:8789/mcp/pepper" },
    "agent-core-channel": { "type": "stdio", "command": "uv",
                            "args": ["run", "--project", "E:\\workspaces\\ai\\agents\\agent_core",
                                     "agent-core-channel", "--agent", "pepper",
                                     "--daemon-url", "http://127.0.0.1:8789"] }
  }
}
```

Two differences vs testbot's: URL ends in `/mcp/pepper` (matches daemon yaml's `mount: /mcp/pepper` from step 10), and `--agent pepper` (matches the bus endpoint name). Old `.mcp.json` is in all three Step 0 backups; rollback is a single `Copy-Item` if needed.

**Lesson for future flip docs:** the `.mcp.json` migration is a separate concern from the project-scope yaml migration. Both files live at the agent_root and both need to be updated; missing either keeps the agent on the old substrate. Future template updates should bundle them.

---

### 10. Add Pepper's endpoints to the daemon yaml — ✅ DONE 2026-05-06 08:23

Edited `~/.agent-core/agent_core.yaml`: inserted three new endpoints (`pepper` / `briefs.pepper` / `discord-pepper`) after testbot's `discord-testbot` entry, before the `bus_hooks:` section. Comment header marks the Pepper block + points at this checklist. PyYAML parser confirms valid yaml; final endpoint count = 9 (was 6); bus_hooks unchanged. `briefs.pepper` is named distinctly from testbot's `briefs.orchestrator` so both coexist; `pepper`'s `briefs_orchestrator: briefs.pepper` param wires the right cross-endpoint MCP tool mounting.


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

### 11. Restart the bus daemon — ✅ DONE 2026-05-06 08:21

Daemon launched (PID 62680) with the new yaml. Boot took 6 seconds: HTTP on 127.0.0.1:8789 with 3 mounts; all 9 endpoints registered cleanly; both Discord shards connected to Gateway (testbot session `6ebb79082...`, Pepper session `a0228d134...`). Two expected warnings (Pepper's `Memory\briefs\fetchers` + `Memory\briefs\destinations` dirs don't exist yet — brief framework deferred per Decision 4; loader skipped gracefully). Pepper + Violet flipped from offline → online in Discord coincident with Gateway connect — verified live by Jeff at 08:22.


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

The launch invocation post-flip uses the bus's generic `agent-core-channel` server (replaces Pepper's old custom `pepper-channel`). Same flag shape Jeff already uses for testbot:

```powershell
# Drive testbot first as launch-path proof, then Pepper.
cd C:\Users\jeffr\.testbot
claude --dangerously-load-development-channels server:agent-core-channel --continue

# After testbot's SessionStart proves the launch chain works:
cd C:\Users\jeffr\.pepper
claude --dangerously-load-development-channels server:agent-core-channel --continue
```

Flag breakdown:
- `--dangerously-load-development-channels server:agent-core-channel` — opens the shared MCP channel server that bridges the Claude Code session to the bus daemon's NotificationBroker (inbox / wake events).
- `--continue` — resumes the previous session; preserves conversation history.

The first `SessionStart` hook should fire `agent-core hooks run SessionStart`, which now reads the new yaml.

---

## Post-flip smoke test (do BEFORE walking away)

Five quick checks that exercise the substrate end-to-end. Each should take under 60 seconds.

### A. SessionStart injection landed — ✅ PASS 2026-05-06 08:26

Asked Pepper "what identity files did you receive at SessionStart?". She named all 4 sources with the right headings (SOUL → "Identity — Critical Core", IDENTITY → "Identity — Self-model", preferences → "Identity — Preferences", handoff → "Continuity") and TimeInjector emitted "Current Time". Cross-checked her response against `~/.pepper/agent_core.yaml`: 5 SessionStart tools = 5 successful runs in the hook log. Her own self-recognition ("I have my voice (SOUL), the role/avatar baseline (IDENTITY), my preferences, and yesterday's handoff narrative") confirms the content landed in context, not just on disk.

In Pepper's first session, ask: *"What identity files did you just receive?"*

Expected: she names SOUL.md, IDENTITY.md, pepper/preferences.md, and (if present) pepper/handoff.md, each surfaced as its own block. If she says "I don't see any identity files," the hooks are silently failing — go check the daemon log + Bug #3's CLI reinstall.

### B. Discord round-trip — ✅ PASS 2026-05-06 08:50

After fixing Step 9b (.mcp.json migration), Pepper relaunched and sent `Pepper online — bus substrate verified 2026-05-06` to #pepper-chat (channel 1488680018077945978). Tool name: `mcp__agent-core__send` (NOT `mcp__pepper-discord__*`) — confirms substrate is the bus. Synchronous return: `{"status":"published","id":"5b8b0a2d8ddc4154bc6a7b193b865768"}` — that's the bus envelope_id (publish receipt), not a Discord message_id. Real shape change vs old surface. Async Acknowledgment from `discord-pepper` arrived `in_reply_to: 5b8b0a2d...` with `payload.note: {"status":"sent","message_ids":["1501566715241435169"]}` — correlation chain intact.

Daemon log corroborates: transport session `9431dfa8...` created at 08:46:46, wake `notifications/claude/channel ... (count=2)` pushed at 08:50:25, missing-ack timer fired for `5b8b0a2d...` at 08:50:54 — same envelope_id Pepper received back from her publish.

**Schema-discovery note Pepper surfaced**: Pepper had to feel out the publish payload shape via three rejections. The discriminator `kind` lives at BOTH envelope level AND inside the payload — accepted shape is `kind: ToolInvocation` envelope with `payload: {"kind": "ToolInvocation", ...}`. Worth a doc improvement on the agent-facing publish surface; not cutover-blocking (she got through it).

In Pepper's session: *"Send 'Pepper online — flipped to bus 2026-05-06' to her test channel."*

Expected: message lands in `#pepper-chat`. Echo a reply yourself — Pepper should surface the inbound `discord.message` Event in her inbox without you prompting her to look.

### C. Engagement Events surface — ✅ PASS 2026-05-06 08:55

Jeff added a 👍🏽 reaction to message_id `1501566715241435169` (the bus-substrate-verified test post from Smoke Test B). Pepper surfaced the inbound `discord.reaction_add` Event unprompted: envelope `663f63e0670d4fbf8a2be36a7bc11d21` from `discord-pepper`, emoji 👍🏽, channel + message + user IDs all populated, **`user_display_name: "Jeff Richley"`**. **Production validation of yesterday's `637c2ec` (sticky cache + fetch_user fallback for Obs 1)** — Jeff wasn't in discord.py's `_users` cache when the reaction fired (cache cold post-restart), so `get_user` returned None → handler fell through to `fetch_user` HTTP → cached for next time → field populated cleanly. Exactly the path the round-3 unit tests covered, working live.

Round-trip integrity: same `message_id` on the inbound reaction matches the outbound send from B. Full Discord lifecycle traceable through one envelope chain.

Add a 👍 reaction to Pepper's message in Discord. Expected: she sees a `discord.reaction_add` Event arrive unprompted, with `user_display_name` populated.

### D. Scheduler is alive — ✅ PASS 2026-05-06 08:58

Pepper invoked the scheduler via `mcp__agent-core__send` envelopes (kind: `ToolInvocation`, target endpoint: `scheduler`) — same bus pattern as discord-pepper. **Read** (`list_jobs`): returned 1 job (testbot residue from yesterday — see follow-ups below). **Write** (`create_job`): accepted on third attempt after schema discovery — `trigger: "cron"`, `schedule: {month: 12, day: 31, hour: 12, minute: 0}` (dict of APScheduler kwargs, NOT a 5-field cron string). Returned `{"status": "created", "name": "smoke-test-d"}`. **Delete** (`delete_job`): returned `{"status": "deleted", "name": "smoke-test-d"}`. Bus envelope correlation_id chain intact across all three (publish receipt → Acknowledgment with `in_reply_to` matching).

```powershell
agent-core scheduler list-jobs --config C:\Users\jeffr\.agent-core\agent_core.yaml
```

Expected: returns the scheduler's persisted jobs (may be empty post-flip if Pepper hasn't recreated any). At minimum, no error.

### E. Handoff round-trip works — ✅ PASS 2026-05-06 08:50 (bonus, surfaced during Smoke Test B)

Verified earlier than expected: alongside the Smoke Test B inbox wake, Pepper received a `HandoffReady` Event envelope from `handoff-jobs` for her PRIOR session's SessionEnd (`session_id c92cdbdd-...`, `handoff_path C:\Users\jeffr\.pepper\Memory\pepper\handoff.md`, `content_sha256 9291d55a9bb81b83adc20d71ff9fbd4a62f1fc7f8ecb032b733e9dc619b147a2`, `handoff_event SessionEnd`, `created_at 12:42:02 UTC`). Confirms async daemon-side handoff worker is live in production: PreCompact/SessionEnd hook → enqueues job at `/internal/handoff-jobs` → daemon worker writes handoff.md + status.json → publishes HandoffReady back to mailbox. Cutover #02 working end-to-end.

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

- **`testbot-morning-brief` cleanup (✅ done 2026-05-06 09:09).** Surfaced during Smoke Test D as residue from yesterday's testbot Phase 3 practice run; would have fired at 13:51 ET today otherwise. Deleted via operator-tier HTTP MCP call (uv-run Python script using `mcp.streamablehttp_client` against `/mcp/agent-testbot`, calling `send` with a `delete_job` ToolInvocation envelope to the scheduler). Three operator Acks generated during the cleanup were acked out of testbot's mailbox so she didn't surface unsolicited traffic.

- **Architectural finding from the cleanup: scheduler has no per-agent access control.** Tracked as [issue #34](https://github.com/jeffrichley/agent_core/issues/34). The single shared bus endpoint `scheduler` accepts `delete_job` / `update_job` / `pause_job` / `resume_job` from any caller regardless of which agent originally created the job. The job's `target` field controls where the FIRED envelope goes when cron triggers; it does not scope read/write access. For today's operator-tier setup (one trust domain, one human running both agents) this is fine — the cleanup itself relied on this property. For multi-trust-domain deployments later, this is a real access-control gap with concrete remote-execution implications via `create_job`.

- **`create_job` schema discovery doc** — Pepper had to schema-iterate three times during Smoke Test D (`trigger` is a discriminator literal `"interval"|"cron"|"date"`; `schedule` is an APScheduler kwargs dict, NOT a 5-field cron string). Pepper has 17 jobs to recreate from the inventory; documenting the shape once collapses the per-job rejection cost. Sibling doc or section in `scheduled-tasks-inventory.md` recommended.

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

## Sign-off — 2026-05-06 Pepper cutover flip — **GREEN**

**Cutover complete 09:09 ET.** Pepper migrated from her single-process synchronous-handoff substrate to the new bus-based architecture in a methodical, one-step-at-a-time walkthrough.

### Sequence of events

| Time | Step |
|---|---|
| 07:55 | Pepper stopped pre-backup so logs don't churn during snapshot |
| 07:57 | Step 0 — comprehensive backup × 3 destinations (C: SSD, D: external, E: HDD), 448.7 MB / 1146 files each, SHA256-verified byte-identical |
| 08:00 | Step 1 — quick rollback set (4 small files) |
| 08:01 | Step 2 — `git pull`; HEAD at `289adf3` |
| 08:02 | Step 3 — `uv sync` + 680/683 tests passing |
| 08:07 | Step 4 — global `agent-core` CLI reinstalled (Bug #3 fix verified — no `ValidationError`) |
| 08:09 | Step 5 — vault dry-run clean against `~/.pepper/Memory` |
| 08:11 | Step 5b — scheduler.db inventory unchanged from yesterday's decode (17 jobs) |
| 08:14 | Step 6 — `discord-pepper.env` provisioned (token validated, 72 chars, canonical 3-part shape) |
| 08:18 | Step 7 — Pepper processes verified down |
| 08:18 | Step 8 — daemon stopped (PID 13936 → gone) |
| 08:21 | Step 9 — `~/.pepper/agent_core.yaml` replaced with the pre-staged new-shape file |
| 08:23 | Step 10 — daemon yaml extended from 6 → 9 endpoints (Pepper's `pepper` / `briefs.pepper` / `discord-pepper` added alongside testbot's) |
| 08:21 | Step 11 — daemon restarted; both Discord shards Gateway-connected; Pepper + Violet flipped offline → online |
| 08:24 | Step 12 — Pepper launched (`claude --dangerously-load-development-channels server:agent-core-channel --continue`) |
| 08:26 | **Smoke Test A** ✅ — SessionStart injection landed (5 tools, 4 identity files surfaced cleanly) |
| 08:38 | **Step 9b discovered + fixed** — `~/.pepper/.mcp.json` was still pointing at old `pepper-channel`/`pepper-discord`/`pepper-scheduler` MCP servers; rewrote to bus-pointing shape (parallel to testbot's). This was a real checklist gap — first attempted send hit the OLD adapter. |
| 08:50 | **Smoke Test B** ✅ — outbound through bus (`mcp__agent-core__send` → `discord-pepper` → real Discord message_id), inbound wake fired unprompted, full envelope correlation chain |
| 08:50 | **Smoke Test E** ✅ (bonus from B) — handoff round-trip verified via prior session's `HandoffReady` event from `handoff-jobs` |
| 08:55 | **Smoke Test C** ✅ — `discord.reaction_add` Event from real reaction surfaced unprompted, **`user_display_name: "Jeff Richley"` populated** (production validation of yesterday's `637c2ec` sticky cache + `fetch_user` fallback under genuine cache-cold conditions) |
| 08:58 | **Smoke Test D** ✅ — scheduler reachable via `mcp__agent-core__send` to `to: scheduler`; list/create/delete all round-trip through the bus correctly |
| 09:09 | `testbot-morning-brief` residue cleaned via operator-tier HTTP MCP script; scheduler now has only Pepper's `floor-feedback-meeting-prep` (genuinely created by Pepper at your request post-flip) |

### Findings caught during the flip

1. **`.mcp.json` migration was a missed checklist step.** Step 9 only handled the project-scope yaml (hooks); the MCP server registry at `~/.pepper/.mcp.json` is a SEPARATE concern. Future flip docs should bundle both files.
2. **Vault dry-run command had wrong flag in the original checklist** (`--base` doesn't exist; correct is `--vault`). Fixed inline.
3. **Scheduler has no per-agent access control** — any caller can delete/update/pause any job. Tracked as [issue #34](https://github.com/jeffrichley/agent_core/issues/34). Not blocking today (single-operator setup); real gap for multi-trust-domain deployments later.
4. **`create_job` schema discovery cost** — `trigger` is a discriminator literal, `schedule` is APScheduler kwargs dict (NOT 5-field cron string). Pepper schema-iterated 3 times during Smoke Test D. Documenting once will save 17× when she recreates her schedule.

### Closing observations

- **Pepper is on the new substrate, organically using it.** Within 30 minutes of going live she'd already created her first job through the new scheduler (`floor-feedback-meeting-prep` for Friday). She schema-discovered the publish API on her own and wrote it up cleanly. That's exactly the agent-side discipline the practice run was meant to validate before flipping.
- **Three of nine practice-run blockers traced to fakes-mirror-real fixture drift; the round-3 sticky-cache fix proved load-bearing in production.** When Pepper got her first reaction post-flip, `user_display_name` came back populated cleanly because `fetch_user` ran on cache miss (Jeff wasn't in discord.py's `_users` cache). That path was untested in production until this morning.
- **Backup paranoia paid off in confidence even though it wasn't needed.** Three byte-identical 448.7 MB snapshots across three independent failure surfaces (SSD/external/HDD) plus daily GitHub push gave us four independent recovery points before any cutover-window action. Rollback never came up.

### Recommendation

**Cutover is complete. Pepper is live.** Three small follow-ups remain (see Post-flip section above), all non-blocking. The original epic's "all 9 cutover tickets reach Verified" gate passes — see the per-ticket table updated in `docs/requirements/pepper-cutover-agent-playbook.md`.

---

## What this flip does NOT change

- Pepper's identity files (SOUL.md, IDENTITY.md, preferences.md) — content unchanged, just newly injected per-block by separate IdentityInjector hooks.
- Pepper's vault layout under `C:\Users\jeffr\.pepper\Memory\` — unchanged. Cutover #06's dry-run is the verification.
- Pepper's existing `~/.claude/settings.json` hook bindings — already point to `agent-core hooks run *`; only the underlying yaml schema changes.
- Pepper's morning-brief delivery shape — still hand-authored, still posts to `#pepper-chat`. Brief framework adoption deferred.
- testbot's setup — endpoints stay registered, available for ad-hoc verification post-flip.
