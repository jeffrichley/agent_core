  Handoff: agent-core "Responsive Inbox" + Channel Relay PR                                                                                                                                                                                                                                                    
                                                                                                                                                                                                                                                                                                                 Context — what we are building                   
                                                                                                                                                                                                                                                                                                                 You are continuing work on agent_core, a local AI-agent daemon. The current branch feat/responsive-inbox bundles two sub-projects that ship together as one PR:
                                                                                                                                                                                                                                                                                                                 1. Sub-Project I — Responsive Inbox (Tasks 1–9): make agents wake autonomously when mail arrives on the bus, with urgency tiers, same-sender batching, and persistence across restarts.                                                                                                                        2. Channel Relay (CR Tasks 1–8b): a stdio MCP subprocess (agent-core-channel) that bridges the daemon's /notify/<agent> SSE stream to Claude Code's notifications/claude/channel mechanism. This was discovered to be necessary because plain Claude Code's standard ClientSession validates against a strict   ServerNotification union and silently drops anything else — so daemon-side push alone can't wake a vanilla Claude Code session.                                                                                                                                                                             

  Spec docs (read these first if you need architectural depth):                                                                                                                                                                                                                                                
  - docs/superpowers/specs/2026-04-29-responsive-inbox-design.md                                                                                                                                                                                                                                               
  - docs/superpowers/specs/2026-04-29-channel-relay-design.md

  Plan docs (granular task lists, the contract for each commit):
  - docs/superpowers/plans/2026-04-29-responsive-inbox.md
  - docs/superpowers/plans/2026-04-29-channel-relay.md

  Repo + branch state

  - Repo: E:\workspaces\ai\agents\agent_core (uv workspace; multi-package monorepo).
  - Branch: feat/responsive-inbox (continues on top of merged main work).
  - Packages: packages/core (daemon), packages/notify, packages/credentials, packages/agent-core-discord, packages/agent-core-channel (new — the stdio relay).
  - Python 3.12, ruff line-length 100, pytest. Conventions in CLAUDE.md. Conventional Commits, NO Co-Authored-By trailer.

  Latest 8 commits on the branch:

  50902ac fix(claude-mcp): dedup queue_for_pickup by envelope id
  e6644bc fix(channel): coerce meta to Record<string,string> + add Server instructions
  f23e524 test(channel): drop session workaround from e2e relay test
  2f7a4ce fix(claude-mcp): publish to broker on deliver() even when no session
  3ba31cb test(channel): real-bus end-to-end relay integration
  e3dd4e6 feat(channel): stdio MCP server with claude/channel capability + run_relay
  5487332 feat(channel): SSE client with reconnect/backoff
  06434f2 feat(channel): scaffold agent-core-channel package + Typer CLI

  Test state: 353 passed, 2 skipped across packages/core/tests/, packages/agent-core-channel/tests/, packages/credentials/tests/. Run uv run pytest packages/core/tests/ packages/agent-core-channel/tests/ -q to verify.

  What is DONE

  Daemon side (Sub-Project I, Tasks 1–8): urgency field on Envelope, ALTER TABLE migration, list_pending sort/batch, SessionRegistry middleware (replaced _SessionTracker), real push pipeline via _fire_after_debounce, Discord urgency-red regex rule, real-MCP integration test for push delivery. All      
  shipped earlier in feat/responsive-inbox.

  Channel relay (CR Tasks 1–8b):
  - NotificationBroker per-agent fan-out (packages/core/src/agent_core/bus/notify_broker.py).
  - Bus.snapshot_for_agent() for initial-wake-on-connect.
  - /notify/<agent> SSE route on HTTPHost (packages/core/src/agent_core/bus/http_host.py).
  - _fire_after_debounce hook publishing to broker; Runner wiring threads the broker into ClaudeCodeMCPEndpoint post-construction.
  - agent-core-channel package: pyproject + Typer CLI + workspace registration + SSE client with reconnect/backoff + stdio MCP server using low-level mcp.server.lowlevel.server.Server API.
  - Real-bus end-to-end integration test (packages/agent-core-channel/tests/test_end_to_end_relay.py).
  - CR Task 8b — deliver() now publishes to broker regardless of _session_active (it was previously gated, breaking the relay's whole purpose). The EndpointUnavailable raise is preserved for bus retry semantics on the no-session branch.

  Live validation (Task 9 STEP 1) — PASS: Wake latency ~56ms confirmed end-to-end. Send → bus → deliver() → broker fan-out → /notify/agent-testbot SSE → relay _sse_pump → emit_channel_notification → Claude Code receives <channel source="agent-core-channel" ...>INBOX: N pending — ...</channel> → LLM    
  autonomously calls list_pending.

  Two production-bug fixes that came out of live debugging today:
  - e6644bc — meta is coerced to Record<string, string> per the channels spec, plus Server constructor declares version="0.1.0" and an instructions block. Without these, Claude Code silently dropped notifications.
  - 50902ac — queue_for_pickup is now idempotent on envelope id. The bus retries deliver() on EndpointUnavailable and was duplicating queued envelopes (live testbot saw 5× copies of pre-relay envelopes).

  What we DISCOVERED (read this before touching anything)

  1. Claude Code's channels spec is documented — follow it strictly

  - https://code.claude.com/docs/en/channels
  - https://code.claude.com/docs/en/channels-reference

  Critical contract (we already implemented but if you change anything, do not violate):
  - Notification method: notifications/claude/channel (exact).
  - params.content: string. Becomes the <channel> tag body.
  - params.meta: Record<string, string> only. Each entry becomes an XML attribute on the <channel> tag. Keys not matching [A-Za-z0-9_]+ are silently dropped. Non-string values are silently dropped (this caused our bug — fixed in e6644bc).
  - Capability: experimental.claude/channel = {}.
  - Server instructions field is functional, not just decorative — it goes into Claude's system prompt and tells the LLM how to react to channel events.
  - The flag is claude --dangerously-load-development-channels server:<NAME> where <NAME> matches the key in .mcp.json's mcpServers (for us, agent-core-channel).

  Pepper's reference implementation lives at e:\workspaces\ai\pepper\src\pepper\channel\server.py. Compare with it if anything looks off.

  2. uv tool install + pywin32 is broken on Windows (worked around, not fixed)

  uv tool install --from packages/agent-core-channel agent-core-channel ships pywin32 wheels but does not run the post-install scripts that stage _win32sysloader.pyd. The MCP SDK's mcp.os.win32.utilities imports pywintypes which transitively needs that DLL stub, so the installed binary crashes at      
  startup.

  Workaround currently in use: ~/.testbot/.mcp.json points command at the workspace venv binary directly:
  "agent-core-channel": {
    "command": "E:\\workspaces\\ai\\agents\\agent_core\\.venv\\Scripts\\agent-core-channel.exe",
    "args": ["--agent", "agent-testbot"]
  }
  The workspace venv has the full pywin32 because uv sync runs the post-install. Do NOT switch back to uv tool install until the pywin32 packaging issue is resolved (likely needs either pipx, a wrapper script, or upstream fix). This is worth a follow-up ticket but is not blocking the PR.

  3. The 11-envelope inbox state on the live testbot

  Live testbot accumulated 11 envelopes during debugging — the first 8+ are duplicates from before 50902ac (5× of envelope a1f4bbe8..., 5× of envelope f42cac9f..., plus the third successful self-ping). These are in-memory in ClaudeCodeMCPEndpoint._pending; the SQLite-persisted bus envelopes are        
  separate. A daemon restart clears them (the daemon is currently stopped). When you next daemon start, the inbox will be clean and Task 9 STEPs 2–5 can run from a fresh state.

  What is LEFT to do

  1. Live testbot Task 9 STEPs 2–5 (manual, Jeff drives testbot)

  The plan is in docs/superpowers/plans/2026-04-29-channel-relay.md lines 1856–1953. Verbatim prompts to paste into testbot:

  STEP 2 — Burst coalescing:
  STEP 2 — Burst arrivals coalesce into one notification:
  Send 5 envelopes back-to-back via mcp__agent-core__send with
  payload {"kind":"TextMessage","text":"burst-N"} for N=0..4.

  You should see ONE autonomous turn fire (debounced) covering all 5.
  list_pending should return 5 envelopes.

  PASS:
  - Exactly one autonomous turn fired (one wake).
  - list_pending returns 5 envelopes.

  Report PASS/FAIL.

  STEP 3 — Urgency ordering:
  STEP 3 — list_pending sorts by urgency tier:
  Send three envelopes (in this order) via mcp__agent-core__send to yourself:
    1. urgency="green", text="green-msg"
    2. urgency="yellow", text="yellow-msg"
    3. urgency="red", text="red-msg"

  Then call list_pending. Expected order: red → yellow → green.

  PASS:
  - Three envelopes returned.
  - First is red-msg, second yellow-msg, third green-msg.

  Report PASS/FAIL.

  STEP 4 — Same-sender batching:
  STEP 4 — list_pending(batch_window_seconds=30) groups same-sender bursts:
  Send three envelopes from yourself in quick succession.

  Then call list_pending(batch_window_seconds=30). Expected: 1 entry of
  type="batch" containing 3 envelopes.

  Then call list_pending() (default). Expected: 3 flat entries.

  PASS:
  - Batched call returns 1 batch group with 3 envelopes.
  - Default call returns 3 flat entries.

  Report PASS/FAIL.

  STEP 5 — Disconnect/reconnect (Jeff drives the disconnect):
  STEP 5 — Mailbox catches up on reconnect:
  a) Tell me when ready. I'll close your Claude Code session.
  b) While you're disconnected, I'll send a TextMessage envelope to
     agent-testbot (from a stub publish on the daemon side).
  c) I'll restart your Claude Code session.
  d) On reconnect, you should either get an autonomous wake (initial-wake-on-
     connect snapshot fires) OR you can call list_pending immediately and
     confirm the envelope sent during your absence is present.

  PASS:
  - list_pending after reconnect shows the envelope sent while you were offline.
  - No data loss.

  Report PASS/FAIL when I tell you the cycle is complete.

  After STEPS 1–5 all PASS, write the final validation report at plan line 1936:

  Sub-project I (Responsive Inbox) — Final Validation Report

  STEP 1 (autonomous push-wake): PASS  (already confirmed: ~56ms, commit e6644bc validated it live)
  STEP 2 (burst coalescing): PASS / FAIL
  STEP 3 (urgency ordering): PASS / FAIL
  STEP 4 (same-sender batching): PASS / FAIL
  STEP 5 (mailbox-authoritative on reconnect): PASS / FAIL

  Daemon log: no ALTER TABLE errors, no unhandled exceptions in the
  notification path.

  Ship: YES / NO

  2. Two non-blocking quality follow-ups from CR Task 7's code-quality review

  These were explicitly approved as "ship it; address in follow-up." Address only if there's bandwidth before merging, otherwise file as separate issues:

  a. _sse_pump's outer try/except scope is too narrow (packages/agent-core-channel/src/agent_core_channel/stdio_server.py). Today only emit_channel_notification is inside the try. If iter_notify_events ever re-raises, the pump crashes and cancels server.run with no log trail. Wrap the whole async for  
  body and add log.error(..., exc_info=True) before propagating.

  b. Redundant Server instantiation — already partially addressed by the _build_server() helper in e6644bc. Both run_relay and build_initialization_options now use it, so no further work needed unless someone wants to tighten it more.

  3. PR-ready checklist before merging

  - All Task 9 STEPs PASS.
  - uv run pytest -q green across all packages (currently 353 passed).
  - uv run ruff check packages/core/ packages/agent-core-channel/ clean (note: pre-existing ruff errors elsewhere in packages/core/ — not regressions, do not block on them).
  - Update CHANGELOG.md / towncrier fragments as the repo convention requires (check git log for prior PR pattern).
  - PR description: cross-reference both spec docs and the plan docs; call out the channel-relay carve-out (started as part of responsive-inbox brainstorming, ended up its own sub-project after live validation revealed Claude Code's strict notification union dropped daemon-side pushes).

  4. Document the uv tool install + pywin32 packaging issue

  Either:
  - Open a follow-up issue describing the workaround (current .mcp.json points at workspace venv binary).
  - Or add a README note in packages/agent-core-channel/ explaining the install path.

  This isn't blocking the PR but the .mcp.json example in the plan still references agent-core-channel (PATH-resolved) which doesn't work with uv tool install on Windows. Decide before publishing.

  Operational context for live validation

  - Daemon command: uv run agent-core daemon start (start), uv run agent-core daemon stop (stop). PID file at ~/.agent-core/daemon.pid. Log at ~/.agent-core/daemon.log.
  - HTTP MCP: http://127.0.0.1:8788/mcp/agent-testbot (testbot's tool-call channel).
  - SSE notify: http://127.0.0.1:8788/notify/agent-testbot (long-lived; curl -sN --max-time 2 ... to verify; HTTPHost does NOT log GETs to daemon.log).
  - testbot config: C:\Users\jeffr\.testbot\.mcp.json — currently configured with both agent-core (HTTP) and agent-core-channel (stdio, pointing at workspace venv binary).
  - Launch testbot: cd ~/.testbot && claude --dangerously-load-development-channels server:agent-core-channel --continue (use --continue if the user wants to resume the existing session; otherwise omit).
  - Verify channel server connected: inside testbot, /mcp should show agent-core-channel · ✔ connected.

  Recommended first actions

  1. Read this entire prompt before doing anything.
  2. git log --oneline -10 to confirm branch state matches the commit list above.
  3. uv run pytest packages/core/tests/ packages/agent-core-channel/tests/ -q — sanity check tests still green (should be 339+ passed).
  4. Skim the spec docs (channel-relay-design + responsive-inbox-design) — ~10 min.
  5. Skim the plan docs' Task 9 / Step 5+ section.
  6. Ask Jeff: "Ready to start daemon and run Task 9 STEP 2?" Then uv run agent-core daemon start, ask Jeff to relaunch testbot, paste STEP 2 prompt.
  7. Treat each STEP as its own validation cycle: Jeff drives testbot; you watch the daemon log + diagnose if something fails.

  Auto-memory pointers (relevant)

  The auto-memory system (C:\Users\jeffr\.claude\projects\E--workspaces-ai-agents-agent-core\memory\) holds these guidance entries that apply to this work:

  - feedback_no_dead_code_in_ports.md — port + rewire, never carry code targeting soon-to-be-removed infrastructure.
  - project_pepper_hands_off_until_proven.md — Pepper itself is hands-off until validated on a fresh test agent (testbot is that test agent).
  - feedback_dispatch_with_context7.md — for non-trivial library research, use the context7 MCP server, don't guess from training data. Particularly relevant for any further MCP SDK work.
  - feedback_test_fakes_mirror_real_strictly.md — fakes for third-party libs must refuse argument shapes the real lib would refuse, otherwise green tests ship production bugs.

  What NOT to touch

  - The daemon-side persistence model. The migration in Task 2 is in production via PR #3 already.
  - The Pepper repo (e:\workspaces\ai\pepper\). It's read-only reference; do not edit or migrate it.
  - The completed Sub-Project I commits. They're locked in, reviewed (where applicable), and shipped.
  - The uv.lock unless you intentionally change a dependency.

  End of handoff.
