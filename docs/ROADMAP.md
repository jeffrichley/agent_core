# agent_core Roadmap

Strategic direction for agent_core and its surrounding ecosystem. Living
document — update as decisions land. For tactical items deferred from
approved designs, see `BACKLOG.md`. For approved per-feature designs,
see `superpowers/specs/`.

> **Last updated:** 2026-04-28

---

## Vision

Build a reusable foundation for AI agents (Pepper, future agents, possibly
others) so each new agent doesn't re-invent message routing, scheduling,
hooks, credentials, and lifecycle management. Pepper is the first consumer
and the source of most requirements; agent_core's job is to extract what
generalizes and let Pepper keep what's Pepper-specific.

---

## Pepper inventory (source material)

Survey of `E:\workspaces\ai\pepper` as of 2026-04-28. ~4,700 lines of Python
across these areas:

| Area | Files | Lines | What it does |
|---|---|---|---|
| CLI / process | `cli.py`, `process.py` | 262 | `pepper init/start/stop/status`, PID-file lifecycle, kill-process-tree |
| Runtime generator | `init/generator.py` + 6 Jinja2 templates | ~250 | Generates `~/.pepper/` workspace (CLAUDE.md, settings.json, mcp.json, .env). `--migrate` pulls existing `Memory/` vault |
| Channel server | `channel/server.py`, `channel/router.py` | 681 | Low-level MCP server (custom `notifications/claude/channel`) + uvicorn HTTP (`/message`, `/events` SSE, `/register`, `/health`) + chat-id→source routing with TTL |
| Pipeline hooks | `pipeline/` (model, runner, transcript hook) | 165 | Inbound/outbound transform chain. Today only writes JSONL transcripts to `Memory/daily/raw/` |
| Scheduler | `scheduler/` + `jobs.yaml` | 555 | APScheduler MCP server. SQLite-persisted jobs. Two job types: prompt (POSTs to channel) + function. Seed jobs: heartbeat 30min, morning brief 7am, nightly reflection 3am, vault backup 4am |
| Discord integration | `integrations/discord/` (8 files) | ~2,200 | discord.py client. 16 MCP tools (send/edit/react, fetch, list channels, polls, scheduled events, threads, briefings, attachments, slash commands `/brief`, `/tasks`, `/focus`, `/status`). DM policy + channel allowlists. Smart chunking |
| Credentials | `credentials/` | 296 | PyKeePass-wrapped AES-256 vault. CLI: `creds set/get/list/delete` |
| Attachments | `attachments.py` | 142 | Download Discord URLs → `~/.pepper/attachments/<chat_id>/`, 7-day cleanup |
| Backup | `backup.py` | 92 | tar.gz `Memory/` → Google Drive via `gog` CLI. (Pepper also uses a git repo for vault backup) |
| Skills (md) | `skills/` (5 dirs) | — | `coding`, `create-second-brain-prd`, `creds`, `google` (gog), `scheduler` |

**Already migrated to agent_core:** Pepper's runtime template
(`init/templates/settings.json.j2`) already points hooks at
`uv run agent-core hooks run`. The old per-event hook scripts
(`session_start_context.py`, `session_end_flush.py`,
`pre_compact_flush.py`) are gone — replaced by agent_core's TimeInjector
+ HandoffWriter pipeline.

---

## Categorization

### Move into agent_core (or its ecosystem)

| Pepper component | New home | Status | Notes |
|---|---|---|---|
| Channel server | agent_core Channel Bus + MCP/SSE adapter endpoints | Bus core ✅ Phase 1 merged. Adapters not started. | Replaces hand-rolled MCP-notifications-via-low-level-server-API + cross-thread HTTP→MCP queue with the bus's single-loop fan-out |
| Pipeline hooks (transcript) | `BusHook` in `pre_deliver` stage | Not started | Transcript writer becomes a generic bus hook |
| Scheduler | `agent_core.scheduler` (or its own pkg) | Not started | Generic. Job types stay (prompt/function); "POST to channel" becomes "publish to bus" |
| Credentials | `agent_core.credentials` (or its own pkg) | Not started | Generic |
| Discord adapter | Bus `Endpoint` (location TBD — see sub-project A) | Not started | The 16 Discord tools + access-control + chunking. **Attachment download is part of Discord, not generic** |

### Keep Pepper-specific

| Pepper component | Why it stays | Notes |
|---|---|---|
| `pepper init/start/stop/status` CLI | Orchestrates Pepper's runtime layout | Will be replaced by a smarter agent_core-aware lifecycle CLI (sub-project B) but the wrapper for Pepper specifically can stay |
| Runtime generator + templates | Pepper-specific identity/config | Will get smart-update behavior (sub-project C) |
| Backup workflow | Specific to Jeff's `gog` + git setup | Becomes a scheduled function-job; the deterministic backup *infrastructure* lives in agent_core (sub-project D) |
| Skills (some) | Markdown content tied to Pepper's runtime | Most go away. Keep: `creds`, `google` (gog), `scheduler`. Drop: `coding` (ambient via system prompts), `create-second-brain-prd` (Cole Medin starter, not runtime) |

### Throw away

- The hand-rolled MCP-notifications-via-low-level-server-API gymnastics in `channel/server.py` — bus + thin MCP adapter does this cleaner.
- The thread-bridged HTTP→MCP notification queue (`_enqueue_notification`, `_notification_pump`, `call_soon_threadsafe`) — bus is single-loop.
- `Router` (in-memory chat_id→source) — replaced by bus envelopes carrying `to`/`from` directly.

---

## Sub-projects

Each sub-project will get its own brainstorm → spec → plan cycle (in
`superpowers/specs/` and `superpowers/plans/`). This roadmap tracks status
across them.

| # | Sub-project | Status | Depends on | Notes |
|---|---|---|---|---|
| **A** | **Repo / extension strategy** | 🟡 Spec written — [`2026-04-28-monorepo-workspace-design.md`](superpowers/specs/2026-04-28-monorepo-workspace-design.md) | — | Foundational. Workspace monorepo with multiple PyPI packages. Drives where every other piece lives. |
| B | Lifecycle CLI (install / start / stop / status) | 🔴 Not started | A | Replaces Pepper's `pepper start` model. Should be more intelligent and adapter-aware. |
| C | Smart init/update | 🔴 Not started | A | Non-destructive merge instead of wholesale Jinja overwrite. Jinja is OK for first-time install; not for updates. |
| D | Native backup subsystem | 🔴 Not started | — (mostly independent) | Deterministic, not agent-driven. Includes git-repo backup as a first-class mode alongside tar.gz/cloud. |
| E | Discord adapter + native attachments | 🔴 Not started | A | Discord becomes a bus `Endpoint`. Attachment download lives **with** the Discord adapter (not as a generic feature). |
| F | Skills consolidation | 🔴 Not started | A (where the kept ones live) | Keep: creds, google (gog), scheduler. Drop the rest. |
| G | Dashboard | 🔴 Not started | A, B, the bus | Manage agent instance — start/stop, view mailboxes, view scheduler, view logs. |
| H | Claude plugin packaging | 🔴 Not started — far horizon | A | Bundle as a Claude plugin. Not mandatory near-term. |

### Cross-cutting concerns to remember

- **Channel Bus Phase 1 is merged** (PR #2). Phase 2+ items (security hooks, additional endpoints) live in `BACKLOG.md`.
- **Hook tools already exist** in agent_core (TimeInjector, HandoffWriter, IdentityInjector planned). These are orthogonal to bus endpoints — they fire on Claude Code lifecycle events.
- **Email CLI** (`agent_core.email`) already shipped — not yet integrated with bus or scheduler.

---

## Open architectural questions

Things we know we need to decide but haven't yet. Each will be resolved in
the relevant sub-project's brainstorm.

- **Sub-project A:** monorepo (one package) vs monorepo (uv workspace, multiple packages) vs separate repos? Naming?
- **Sub-project B:** does the lifecycle CLI live in `agent_core` itself or in a separate orchestrator? How does it discover installed adapters?
- **Sub-project C:** what's the diff/merge strategy for runtime configs? User-edits-aware?
- **Sub-project D:** git-as-backup vs tar.gz-as-backup vs both? Where does the backup destination credential live?
- **Sub-project E:** does the Discord adapter ship as `agent-core-discord` (separate package) or live inside `agent-core[discord]` (extras)?
- **Sub-project G:** web UI vs TUI vs both? Read-only vs control plane?

---

## How to update this doc

- When a sub-project's brainstorm produces a spec, link the spec from its row.
- When a sub-project ships, mark it 🟢 and link the merged PR.
- When new sub-projects emerge (new Pepper requirements, new agents joining
  the ecosystem), add them with status 🔴.
- When a decision is made on an Open Architectural Question, move it into
  the relevant sub-project's row as a resolved note, then delete it from
  the open list.
