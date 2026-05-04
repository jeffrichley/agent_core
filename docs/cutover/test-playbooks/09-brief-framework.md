# Cutover #09 — Brief framework v1 (test playbook)

**Spec:** [`docs/superpowers/specs/2026-05-04-brief-framework-design.md`](../../superpowers/specs/2026-05-04-brief-framework-design.md)
**Design:** [`docs/superpowers/specs/2026-05-04-brief-framework-design.md`](../../superpowers/specs/2026-05-04-brief-framework-design.md) (single combined spec/design doc)
**Plan:** [`docs/superpowers/plans/2026-05-04-brief-framework.md`](../../superpowers/plans/2026-05-04-brief-framework.md)
**Implementation commits:**
- `bdbd369` feat(briefs): package scaffold + core protocols
- `3d2bdae` fix(briefs): apply Task 1 code-quality review feedback
- `a5776ab` feat(briefs): ${var} substitution + path expansion at config-load time
- `9ca29e0` fix(briefs): apply Task 2 code-quality review feedback
- `770dac3` feat(briefs): filesystem-discovered loader for fetchers/destinations/extensions
- `b5a6583` fix(briefs): apply Task 3 code-quality review feedback
- `af40994` feat(briefs): async-concurrent gather engine with per-fetcher timeouts
- `c7895fe` fix(briefs): apply Task 4 code-quality review feedback
- `201da6a` feat(briefs): filesystem_read built-in fetcher
- `dccc90e` fix(briefs): apply Task 5 code-quality review feedback
- `3fcd9c5` feat(briefs): cli built-in fetcher (subprocess + parse)
- `29a73e5` docs(briefs): document CliFetcher subprocess semantics + env_passthrough PATH note
- `88d8181` feat(briefs): playbook parser (YAML-in-MD with simpleeval expressions)
- `1b23549` fix(briefs): apply Task 7 code-quality review feedback
- `5fdc35d` feat(scheduler): fire Event envelopes with structured payloads (cutover #09 prep)
- `d1bc2fc` fix(scheduler): apply Task 8 code-quality review feedback
- `94d9ff4` feat(briefs): orchestrator endpoint receives BriefRequest, publishes ComposeBrief
- `b285c1d` fix(briefs): apply Task 9 code-quality review feedback
- `1b3bc39` feat(briefs): SessionRegistry + agent-tool surface (T10)
- `b8cb4a9` fix(briefs): apply Task 10 code-quality review feedback
- `a278c68` feat(briefs): DiscordEmbedDestination + bus-mediated embed delivery (T11)
- `f19c977` fix(briefs): apply Task 11 code-quality review feedback
- `278f814` feat(briefs): MarkdownFileDestination — canonical fallback destination (T12)
- `e5b42a7` fix(briefs): apply Task 12 code-quality review feedback
- `799bf87` feat(briefs): T13 submit handler — atomic validate + format + send + audit
- `1e5739a` fix(briefs): apply Task 13 code-quality review feedback
- `6761227` feat(briefs): T14 — compose_brief MCP self-launch tool + tool_mounters seam
- `67a63f4` fix(briefs): apply Task 14 code-quality review feedback
- `e3886a6` feat(briefs): agent-core briefs CLI subapp (compose, fetchers list/test)
- `3991a13` fix(briefs): apply Task 15 code-quality review feedback
- `b67473c` feat(briefs): plugin wiring + Pepper example yaml + morning_brief playbook
- `5301478` test(briefs): cover Task 16 orchestrator constructor branches
- `de1b708` test(briefs): end-to-end morning_brief flow with stub agent
- `a852284` fix(briefs): get_section_spec + validate_section cover conditional sections

## What was implemented

A structured-composition framework for agent-produced briefs (morning brief, evening brief, weekly recap, project/travel/meeting prep). The framework formalizes the seam between deterministic plumbing and LLM judgment: **gather → wake → compose → submit**. Code does the boring work (fetching, schema validation, transport); the agent does the one thing it's irreplaceable at (synthesis under judgment); MD/YAML in agent memory carries the playbook section specs and gather config.

The architecture is the deterministic-LLM-deterministic seam end-to-end. Cron (or `compose_brief` self-launch) publishes a `BriefRequest` Event onto the bus. The brief orchestrator endpoint subscribes, runs the async-concurrent gather engine against the playbook's `gather_config`, opens a session, and publishes a `ComposeBrief` event to the target agent with the gathered context inline. The agent drives the compose loop via 7 MCP tools and finishes with `submit_brief` — a single atomic call that validates, formats, fans out to all destinations, and writes the audit record. There is no "validated but not sent" or "sent but not validated" intermediate state observable to the agent; submit is the seam-closer.

**Agent tool surface (mounted on `ClaudeCodeMCPEndpoint` by the briefs plugin):** `compose_brief` (self-launch entry — runs gather inline and returns context + playbook ref + session token), `list_sections`, `get_section_spec`, `validate_section`, `compress_sections`, `add_extension_section`, `submit_brief`. The bug fix in `a852284` extended `get_section_spec`, `validate_section`, and `compress_sections` to walk `session.conditional_sections` (already pre-filtered to the active subset by the orchestrator), closing a real gap discovered during the T17 e2e: conditional-active sections were invisible to the introspection tools, forcing the test to bypass `get_section_spec` for them. `add_extension_section`'s collision check was widened to include conditional ids so an extension cannot shadow an active conditional.

**Filesystem-discovered fetchers + destinations.** No PR gate, no pluggy hookspec for the catalog itself — the orchestrator is configured with paths, and `discover_implementations` imports each module and registers the `_TYPE`-decorated classes by their declared `_TYPE` string. Built-in fetchers shipped with the package: `filesystem_read` (file/glob → string or list-of-string payload, with optional max-bytes truncation) and `cli` (subprocess shell-out → parse JSON/YAML/text). Built-in destinations: `discord_embed` (publishes a `DiscordEmbedSend` envelope onto the bus, mediated by the discord endpoint) and `markdown_file` (canonical local fallback, writes under a destination-configured root with `{{when.date}}` template substitution). A `fake_calendar` fetcher under `tests/fixtures/` is test-only and never imported by production.

**Scheduler extension (T8).** `JobDef` now supports an `Event` envelope kind alongside the existing `TextMessage` kind. A cron entry can publish a structured `BriefRequest{brief_type=morning_brief, when=...}` event instead of a free-form text message — the cleaner trigger shape for the deterministic-machinery cutover.

**CLI subapp.** `agent-core briefs` exposes `compose --type <brief_type> --agent <agent>` (drives an end-to-end run from the CLI for operator debugging), `fetchers list --fetcher-path <path>` (catalog inspection), and `fetchers test --type <fetcher_type> --config <yaml-or-json> --fetcher-path <path>` (single-fetcher probe — useful for verifying API credentials and parsing without booting the full bus).

**Pluggy plugin entry-point.** The `agent_core_briefs` package exposes two hooks: `register_endpoint_types` (returns the orchestrator endpoint type so it can be referenced from `agent_core.yaml` as `builtin.briefs_orchestrator`) and `register_cli_subapps` (mounts the briefs subapp under `agent-core`). The Pepper example yaml (`docs/examples/pepper-agent-core.yaml`) gains a `briefs.orchestrator` endpoint with `playbooks_path`, `fetcher_paths` (list), `default_target_agent`, and a `vars: { agent_root: ... }` block; `${agent_root}` substitution at config-load time keeps every path a single line away from "Pepper's home moved." The `test_pepper_example_yaml.py` tripwire grew a `TestPepperExampleYamlBriefs` class (6 tests) that catches removal or misconfiguration of the briefs endpoint.

**Audit log at `~/.agent-core/briefs/audit.jsonl`.** Append-only, one line per significant event in the chain — `request_received`, `gather_started`, `gather_completed` (per-fetcher status, duration, context size), `wake_published`, `submit_attempted`, `delivery_completed` (per-destination status), `session_consumed` (total wall time), `session_expired` (TTL exhaustion). Same shape as the bus log audit pattern from cutover #04.

**End-to-end integration test (T17 + the T17.5 fix).** `test_e2e_morning_brief.py` drives the full chain in-process with a stub agent: `BriefRequest` → orchestrator → gather (against fixture fetchers) → `ComposeBrief` → tool surface (the stub agent walks the same path Pepper will: `list_sections`, `get_section_spec` for required *and* conditional sections, `validate_section`, `submit_brief`) → both destinations land their payloads → audit log records the chain. The test covers the bug discovered in `a852284`: conditional sections must be reachable through `get_section_spec`, not bypassed.

## Acceptance criteria (from spec §"Done looks like")

> - `agent-core briefs compose --type morning_brief --agent pepper-stub` runs end-to-end against a stub playbook with stub fetchers, produces a Discord embed (caught by a fake DiscordEndpoint) and a markdown file, both shape-validated.
> - The same flow fires from cron via the extended `SchedulerEndpoint` — a `BriefRequest` Event lands in the stub agent's mailbox, the agent's session calls the tool surface, submits, framework delivers.
> - `~/.agent-core/briefs/audit.jsonl` captures the full request → delivery chain for the test run.
> - An example playbook lives at `docs/examples/playbooks/morning-brief.md` with a tripwire test that asserts its structure.
> - One Pepper-specific gather config lives at `docs/examples/playbooks/morning-gather.yaml` showing the YAML shape.
> - Test playbook for cutover #09 lands at `docs/cutover/test-playbooks/09-brief-framework.md`.
> - Ledger updates: `pepper-pre-cutover-must-haves.md` adds row #09, `pepper-cutover-agent-playbook.md` adds the per-ticket entry.

## Verification steps (end-of-cutover)

### Step 1 — Automated unit + integration tests

```powershell
cd E:\workspaces\ai\agents\agent_core
uv run pytest packages/agent-core-briefs/tests `
              packages/core/tests/test_pepper_example_yaml.py `
              packages/core/tests/test_scheduler_endpoint.py -q
```

Expected: all green (~219 tests in the briefs package + the pepper-yaml tripwire class + the scheduler endpoint tests, which include the `Event`-envelope JobDef shape from T8). Confirms gather engine (concurrency, per-fetcher timeouts, `_errors` capture), playbook parser (YAML-in-MD + simpleeval expressions for conditionals), config-load-time `${var}` substitution, filesystem-loaded fetcher/destination discovery, both built-in fetchers (`filesystem_read`, `cli`), both built-in destinations (`discord_embed` bus path, `markdown_file` direct write), session registry (one-submit-per-token, TTL expiry), all 7 agent tools (including the conditional-section coverage from `a852284`), atomic submit (validate-then-format-then-send-then-audit), CLI surface (compose / fetchers list / fetchers test), scheduler `Event` JobDef shape, and the example yaml wiring tripwires.

### Step 2 — End-to-end morning_brief harness

```powershell
uv run pytest packages/agent-core-briefs/tests/test_e2e_morning_brief.py -v
```

Expected: a single test green. Demonstrates the full `BriefRequest → orchestrator → ComposeBrief → tool surface → submit_brief → both destinations + audit` chain in-process with a stub agent. The stub walks the same code path Pepper's session will: `list_sections`, then `get_section_spec` for each required *and* conditional section id (no bypass), then `validate_section` mid-loop, then `submit_brief`. The test asserts the Discord embed payload shape, the markdown file content + path, and the full audit chain.

### Step 3 — Operator CLI smoke

```powershell
uv run agent-core briefs --help
```

Expected: lists `compose` and `fetchers` subcommands.

```powershell
uv run agent-core briefs fetchers list `
  --fetcher-path packages/agent-core-briefs/src/agent_core_briefs/fetchers
```

Expected: JSON listing the two built-in fetchers (`cli`, `filesystem_read`) with their declared config schemas.

```powershell
# fetchers test takes --config as a yaml file path (not inline JSON).
@"
path: README.md
format: text
"@ | Out-File -Encoding utf8 verify-cfg.yaml

uv run agent-core briefs fetchers test `
  --type filesystem_read `
  --config verify-cfg.yaml `
  --fetcher-path packages/agent-core-briefs/src/agent_core_briefs/fetchers `
  --namespace fs

Remove-Item verify-cfg.yaml
```

Expected: a JSON object on stdout shaped `{"fs": {"content": "<README.md text>", "path": "README.md"}}` — gather_context wraps the fetcher payload under the `--namespace` you pass (filesystem_read's class-level namespace is empty, so always pass one). Fetcher errors land in `_errors.<type_id>` inside the same JSON output (exit code stays 0 — see the existing `test_fetchers_test_surfaces_fetcher_error_in_errors_namespace` test). CLI errors (unknown type, missing config file, bad yaml) print to stderr with a non-zero exit.

### Step 4 — Example playbook parses

```powershell
uv run python -c "from pathlib import Path; from agent_core_briefs.playbook import parse_playbook; pb = parse_playbook(Path('docs/examples/playbooks/morning-brief.md'), vars_map={'agent_root': 'C:/test'}); print('sections:', len(pb.sections)); print('conditional:', len(pb.conditional_sections)); print('destinations:', len(pb.destinations)); print('voice:', pb.voice)"
```

Expected output:

```
sections: 8
conditional: 2
destinations: 2
voice: pepper
```

Confirms the example playbook is well-formed and the parser still accepts everything the example exercises (YAML-in-MD, simpleeval conditional expressions, voice declaration). `vars_map={'agent_root': ...}` provides a stand-in for the `${agent_root}` substitution; any string is fine for parse-shape verification.

### Step 5 — Pepper example yaml tripwires

```powershell
uv run pytest packages/core/tests/test_pepper_example_yaml.py::TestPepperExampleYamlBriefs -v
```

Expected: 6 green. Catches removal of the `briefs.orchestrator` endpoint, misconfiguration of `${agent_root}`, missing playbook/fetcher path declarations, and other shapes the spec calls non-negotiable for Pepper post-cutover.

### Step 6 — Real Pepper morning_brief (cutover-gate blocker)

Once Pepper's live runtime is on agent-core, schedule a 7am cron entry firing a `BriefRequest{brief_type=morning_brief}` event onto her bus and verify three observables:

1. A Discord embed lands in her morning channel with the playbook's section structure.
2. A markdown file lands at `${agent_root}/Memory/daily/briefs/<date>-morning.md` with the same content.
3. `~/.agent-core/briefs/audit.jsonl` records the full chain for that session.

This step gates cutover. The two prerequisites listed in **Cutover-gate-blocking follow-ups** below must land before this step can pass.

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | All briefs tests + pepper-yaml briefs tripwire + scheduler envelope-shape tests green. |
| Step 2 | `test_e2e_morning_brief` green; full chain reaches both destinations + audit. |
| Step 3 | `briefs --help` lists `compose` and `fetchers`; `fetchers list` returns two built-ins; `fetchers test` returns a namespaced payload from `filesystem_read`. |
| Step 4 | Example playbook parses to 8 sections + 2 conditional + 2 destinations + 'pepper' voice. |
| Step 5 | `TestPepperExampleYamlBriefs` — 6 green. |
| Step 6 | Cron-fired BriefRequest produces Discord embed + markdown file + full audit chain on Pepper's live agent-core runtime. Gates cutover. Requires both follow-ups below to land first. |

## Cutover-gate-blocking follow-ups

The framework code shipped in #09, but Pepper cannot actually compose a brief on the new substrate without these two pieces. Both are part of the cutover gate, not post-cutover polish.

- **Cross-endpoint MCP tool mounting** — *In progress (Claude).* The briefs MCP tools are not yet auto-mounted onto Pepper's `ClaudeCodeMCPEndpoint`. T14's `register_briefs_tools(mcp, orchestrator, bus_handle, audit_log, destination_factories)` exists and works; the e2e harness drives the tools directly. What's missing is the runner-time wiring that, once both the `briefs.orchestrator` and `ClaudeCodeMCPEndpoint` instances are constructed and started, finds the orchestrator by name, captures its `bus_handle`, and calls `register_briefs_tools` on the MCP endpoint's FastMCP server. Without this, Pepper's session has zero briefs tools available — `compose_brief` is unreachable from inside her running session. The pluggy hookspec for cross-endpoint coordination is the natural seam.
- **Briefs usage skill at `~/.claude/skills/briefs-author/`** — *Owner: Jeff.* Pepper-facing skill documenting how to author a playbook (YAML-in-MD format, simpleeval expression language, conditional sections, `${var}` substitution) and the gather config shape (fetchers list, namespace declarations, per-fetcher timeouts, `_errors` capture). Without this Pepper can call the framework but cannot extend it — every new brief type would require Jeff to author the playbook by hand. Authoring a skill in Pepper's voice is tone-judgment work, intentionally not automated.

## Known limitations (recorded; not blocking #09 done OR the cutover gate)

- **No backfill for historical briefs.** The brief framework is forward-only; existing `Memory/daily/briefs/*.md` files from Pepper's prior runtime aren't migrated. Acceptable — the WAR skill reads from `Memory/daily/summaries/` (which #04 covers), not from briefs.
- **Audit log is unbounded.** Same shape as #04's daily JSONL: append-only, no rotation, manual cleanup. KB/day in practice; revisit when operationally painful.
- **Single-machine.** Audit log + bus log live on the daemon machine. Cross-machine deployment requires HTTP export or file sync — same constraint as #04.
- **No retry on destination failure.** `submit_brief` is best-effort fan-out: each destination gets one try; failures are recorded in the `SubmitResult` and the audit log but no automatic retry. Operator inspects the audit log and republishes manually if needed.
- **No backpressure on the gather pipeline.** Each fetcher has a per-fetcher timeout enforced at the gather engine. A pathological fetcher that ignores `await` can still block its own slot for the full timeout (other fetchers continue concurrently — the gather is async-concurrent).
- **Fetcher discovery is not cached.** `discover_implementations` re-imports modules on every orchestrator construction. Fine for current load (a handful of fetchers per agent); revisit if the catalog grows large.
- **Extensions protocol punted.** Pepper proposed a rich extension shape (`provide_context`, `declare_sections`, `gate`, `compose_override`, `post_validate`); v1 implements none of these. Sections in playbooks can reference extension hooks and the framework fails loudly when one isn't found, but no extension implementations ship in v1.
- **Watchers deferred.** `meeting_prep` and `travel_prep` need calendar/trip watcher endpoints to fire automatically. v1 ships without them — those briefs can be triggered manually via MCP/CLI but won't fire automatically on event.
- **No cross-section coherence guarantees.** Per-section composition by the host agent means section 5 might say "as I noted above" when section 1 didn't actually note that. Validation is shape-only, not semantic. Mitigation possible via a v2+ coherence pass.
- **Trust model: filesystem-loaded code, no gate.** Pepper writes Python and the framework runs it with the agent's privileges. Mitigations (process isolation, resource budgets, network egress allowlist) deferred until needed.
