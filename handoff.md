# Pepper parity — `agent_core` handoff

This file tracks **migration from the Pepper repo** into `agent_core`: what is already implemented here versus what still matches Pepper’s surface area or explicit requirements. It replaces the older handoff that only described the responsive-inbox + channel-relay branch.

**Do not treat the Pepper repo as mutable** — use `E:\workspaces\ai\pepper` as read-only reference when porting or comparing behavior.

---

## Where the truth lives

| Kind | Location |
|------|----------|
| Hook-tool specs (Identity / Handoff / SessionEnd) | `docs/requirements/pepper-requirements.md` |
| Email CLI product requirements | `docs/requirements/pepper-email-cli.md` |
| Identity truncation (Claude Code ~2KB limit) | `docs/requirements/pepper-identity-injection-size-limit.md` |
| Handoff extraction history / investigation | `docs/requirements/pepper-handoff-writer-bugfix.md` |
| **Pre-cutover must-haves (Pepper's WHAT list)** | **`docs/requirements/pepper-pre-cutover-must-haves.md`** |
| Apple surfaces (vision only — not an agent_core queue item) | `docs/requirements/pepper-ios-watch-vision.md` |
| Strategic sub-projects A–I | `docs/ROADMAP.md` |
| Step-by-step implementation contracts | `docs/superpowers/plans/*.md` |
| Example Pepper hook config | `docs/examples/pepper-agent-core.yaml` |

---

## Already done (Pepper-shaped work in this repo)

Use this as a checklist when comparing to Pepper; details live in ROADMAP and merged PRs.

### Workspace and packaging

- **uv workspace** with member packages: `packages/core` (`agent-core`), `packages/notify` (`agent-core-notify`), `packages/credentials` (`agent-core-credentials`), `packages/agent-core-discord`, `packages/agent-core-channel`.
- Shared **ruff / pytest / mypy** config at repo root; per-package `pyproject.toml` where needed.

### Hook pipeline (Claude Code lifecycle)

- **TimeInjector**, **IdentityInjector**, **FileInjector** under `packages/core/src/agent_core/hooks/tools/`.
- **HandoffWriter** — writes continuity notes (PreCompact + SessionEnd supported; deduplication; current implementation uses a detached `claude -p` subprocess — see module docstring in `handoff_writer.py`).
- **SessionEndWriter** — Tool 3: daily JSONL append + `## End of Session` / HandoffWriter fallback (`session_end_writer.py`, `builtin.session_end_writer`).
- **Pluggable hook tools**: YAML `type:` / builtins, plugin **entry points** and **builtin_aliases** (see `packages/core/docs/plugins.md`).
- **Example config** for Pepper-style pipelines: `docs/examples/pepper-agent-core.yaml`.

### Bus, daemon, and “responsive inbox”

- **Daemon lifecycle** (`agent-core daemon start/stop/status`), **HTTP host**, **ClaudeCodeMCPEndpoint**, bus **envelopes**, persistence, etc. (see `docs/superpowers/specs/2026-04-28-bus-daemon-design.md`).
- **Urgency-aware inbox**, push path, **NotificationBroker**, **`/notify/<agent>` SSE**, same-sender batching (responsive inbox design + plans).
- **`agent-core-channel`**: stdio MCP server bridging SSE → **`notifications/claude/channel`** (strict meta typing, capability `experimental.claude/channel` — see channel-relay spec).

### Endpoints and integrations

- **SchedulerEndpoint** — `packages/core/src/agent_core/endpoints/scheduler.py` (bus-native scheduler; see scheduler plan/spec).
- **Discord** — `packages/agent-core-discord`: v1 **DiscordEndpoint** (1:1 bot↔agent; core outbound tools + inbound messages/reactions + access gate). *Pepper’s monolith had a larger tool surface; v2+ is explicitly deferred in ROADMAP.*

### Credentials and notify

- **Credentials** ported as **`agent_core_credentials`** + **`agent-core-creds`** CLI (`packages/credentials/`), de-Pepperized default paths (`~/.agent-core/…`).
- **Notify** carved out to **`agent_core_notify`** (`packages/notify/`).

### Email

- **`agent-core email`** Typer subcommand group and shared **agentmail** client (`packages/core/src/agent_core/email/`). Invoked as `uv run agent-core email …` (not the literal `uv run email` name from the original Pepper requirements doc — functionally equivalent).

### MCP / session / relay hardening

- Ongoing fixes for **multi-session** MCP behavior, **notify broker** fan-out, **Discord** `in_reply_to` / ack handling, and channel relay **contracts** (see git history / bus + discord + channel tests).

---

## Still to do for Pepper parity (prioritized)

Order is **impact for “Pepper feels like Pepper”** first, then platform depth. Adjust with product calls.

### 1. Identity injection size limit (Claude Code truncation)

**Spec:** `docs/requirements/pepper-identity-injection-size-limit.md`.

**Problem:** Hook output can be ~11KB; Claude Code may surface only ~2KB in the system reminder for **fresh** sessions, so Pepper can get partial identity.

**Work:** Product/engineering decision: compressed “core” identity, two-phase boot, host-side change, or documented operator workaround — then implement the chosen approach.

### 2. Handoff continuity in real sessions

**Spec / history:** `docs/requirements/pepper-handoff-writer-bugfix.md` (written before the detached `claude -p` redesign).

**Work:** Re-validate **live** PreCompact + SessionEnd: `transcript_path` availability, handoff file quality, and debug logging path (today’s `_DEBUG_LOG` still under `~/.pepper/...` — consider making it configurable or agent-neutral).

### 3. Discord v2+ (Pepper feature parity)

**Pointer:** `docs/ROADMAP.md` sub-project **E** — v1 shipped; **polls, scheduled events, threads, typing, briefing/slash tools**, etc. still in Pepper only.

### 4. Transcript / daily log on the bus

**Pointer:** ROADMAP table — Pepper’s pipeline **JSONL to `Memory/daily/raw/`** as a **bus hook** (`pre_deliver`) is called out as **not started** (separate from `SessionEndWriter`, which is hook-pipeline scoped).

### 5. Email × bus × scheduler

**Pointer:** ROADMAP — **email CLI shipped**; **integration** with bus envelopes and/or scheduler jobs (inbound agentmail as bus traffic, scheduled sends) is still open product/engineering work.

### 6. Roadmap sub-projects not about hook parity but part of “replace Pepper”

From **`docs/ROADMAP.md`** (summarized):

| ID | Sub-project | Status (as of ROADMAP narrative) |
|----|-------------|----------------------------------|
| **C** | Smart init/update for generated agent runtime | Not started |
| **D** | Native backup subsystem | Not started |
| **F** | Skills consolidation (which skills survive, where they live) | Not started |
| **G** | Dashboard / control plane | Not started |
| **H** | Claude plugin packaging | Not started |
| **—** | Multi-agent lifecycle CLI (`agent install/start/stop/list`) | Future |

### 7. Channel bus Phase 2+ and backlog

Security hooks, extra endpoints, and deferred items — see **`BACKLOG.md`** and bus specs (Phase 1 merged).

### 8. Operational: `agent-core-channel` on Windows

**`uv tool install`** for the channel package can **fail on Windows** because **pywin32** post-install scripts may not run; practical workaround is pointing `.mcp.json` at the **workspace venv** `agent-core-channel.exe`. Document in `packages/agent-core-channel/README.md` (or fix packaging) so new installs are not blocked.

### 9. Lint / type discipline (cross-cutting)

**Plan:** `docs/superpowers/plans/2026-04-30-lint-and-type-discipline.md` — use when tightening CI and typing across packages.

### Explicitly out of scope here

- **`docs/requirements/pepper-ios-watch-vision.md`** — vision inventory for native Apple apps; not the next `agent_core` implementation unless you start a new product track.
- **`docs/superpowers/plans/2026-04-13-sentient-foundation.md`** — separate “sentient” monorepo idea, not Pepper-in-agent_core parity.

---

## Suggested next implementation pick

If the goal is **closest match to `pepper-requirements.md` build order**: **`SessionEndWriter`** (Tool 3) is implemented; next gap is **identity size limit** (#1 here) and **live handoff validation** (#2).

If the goal is **subjective continuity quality** before more tools: tackle **identity size limit** (#1) and **live handoff validation** (#2) in parallel with spec tests.

---

## Maintenance

When a sub-project ships or a requirement is satisfied, update **this file** and **`docs/ROADMAP.md`** so the next session does not re-plan finished work.
