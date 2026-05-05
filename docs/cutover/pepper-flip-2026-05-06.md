# Pepper cutover flip — 2026-05-06

**Operator-facing checklist for tomorrow morning's actual flip.** Practice-run record + diagnoses live in [`testbot-practice-run-2026-05-05.md`](testbot-practice-run-2026-05-05.md); this doc only contains executable steps. Read top-to-bottom; no skipping.

**Goal:** move Pepper from her old single-process synchronous-handoff substrate to the new bus-based architecture (identity injectors, daemon-side handoff worker, Discord adapter on the bus, scheduler endpoint, daily JSONL pipeline, brief framework available but not yet adopted).

---

## Pre-flip decisions (confirm before starting)

These have defaults; override only if you have a specific reason.

- [ ] **Discord endpoint name: `discord-pepper`** (mirrors testbot's `discord-*` convention; matches what Bug #6's example placeholder assumes).
- [ ] **Bus mailbox: `pepper`** (lowercase). Matches the example yaml; decouples bus routing from the human identity `"Pepper"` (Bug #5).
- [ ] **testbot endpoints stay registered.** Same daemon serves both agents post-flip. testbot remains available for ad-hoc verification without spinning up new infra. Cost: 6 endpoints instead of 3 in `~/.agent-core/agent_core.yaml`.
- [ ] **Brief framework: NOT adopted tomorrow.** Pepper's existing `morning-brief.md` is hand-authored prose with embedded JSON — converting it to the new orchestrator format is a separate design exercise. Tomorrow's flip is bus + identity + handoff + Discord verbs only. Brief framework migration scheduled later.
- [ ] **Vault location stays at `C:\Users\jeffr\.pepper\Memory\pepper\`.** Cutover #06 dry-run already confirmed zero false-positive migration findings; no operator file moves needed (run [`agent-core vault plan-dry-run --base C:\Users\jeffr\.pepper`](test-playbooks/06-vault-continuity.md) one more time tomorrow to confirm).

---

## Pre-flip preparation (do BEFORE Pepper goes offline)

### 1. Snapshot the current state (rollback insurance)

```powershell
$snap = "C:\Users\jeffr\.pepper-pre-cutover-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item C:\Users\jeffr\.pepper\agent_core.yaml "$snap-agent_core.yaml"
Copy-Item C:\Users\jeffr\.pepper\.claude\settings.json "$snap-settings.json"
Copy-Item C:\Users\jeffr\.pepper\Memory\pepper\handoff.md "$snap-handoff.md" -ErrorAction SilentlyContinue
Copy-Item C:\Users\jeffr\.agent-core\agent_core.yaml "$snap-daemon-agent_core.yaml"
Write-Output "snapshots: $snap-*"
```

If anything goes sideways, these four files are the rollback set.

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

### 7. Stop Pepper's existing process(es)

If Pepper has any long-lived process running (in-process scheduler, bot worker, etc.), stop them. Discord-side: if her old setup had its own bot connection on the same token, kill it before we start the new bus-side discord-pepper endpoint, otherwise both will compete for the same gateway session.

```powershell
# Inspect what's running under .pepper:
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*\.pepper\*' } | Select-Object ProcessId,CommandLine
```

Stop anything you don't recognize as needed.

### 8. Stop the bus daemon

```powershell
$pidPath = "C:\Users\jeffr\.agent-core\daemon.pid"
$old = (Get-Content $pidPath -Raw).Trim() -as [int]
Stop-Process -Id $old -ErrorAction Stop
```

### 9. Replace Pepper's project-scope yaml

The new shape is in [`docs/examples/pepper-agent-core.yaml`](../examples/pepper-agent-core.yaml) (lines 103-172 — the `pipelines:` + `bus_hooks:` block). Pepper's existing `agent_core.yaml` uses the deprecated `tool:` key and the old synchronous HandoffWriter — replace it entirely with the new shape.

Key facts for tomorrow:
- `mailbox: "pepper"` MUST be present on PreCompact + SessionEnd handoff_writer params (Bug #5 — without it, every SessionEnd raises "publish to unregistered endpoint" and burns 2-3 SDK calls per session via the retry loop).
- `handoff_jobs_url` port: **8789** (the example yaml is now consistent with the daemon's operational port; the code-default 8788 doesn't apply because `~/.agent-core/agent_core.yaml` overrides it).
- `vault_root` is `C:\Users\jeffr\.pepper\Memory\pepper` (NOT the broader `Memory` dir).
- Identity files: keep Pepper's three-block split (SOUL / IDENTITY / preferences) plus the dedicated HandoffInjector for handoff.md.

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

- **Brief framework adoption.** Migrate `morning-brief.md` from hand-authored prose-with-JSON to the new `brief_type: morning_brief` MD/YAML format. Design exercise: section specs, color palette, fetcher catalog (calendar / gmail / tasks / projects / weather), gather YAML. Delivery via `discord_embed` + `markdown_file` destinations. Until then, Pepper's existing morning-brief flow continues to work as a Pepper-authored process.
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
