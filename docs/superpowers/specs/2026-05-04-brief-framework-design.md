# Brief framework — design (cutover #09)

**Status:** Proposed (pending user review)
**Author:** Claude (drafted from brainstorm with Jeff Richley, 2026-05-04)
**Related:**
- Cutover ledger: `docs/requirements/pepper-pre-cutover-must-haves.md`
- Cutover #03 (Discord verb parity) — provides the verbs (`send_briefing`, embed posting, etc.) this framework consumes
- Cutover #04 (Daily JSONL pipeline) — the `BriefRequest` and `ComposeBrief` envelopes flow through the bus and land in the daily log automatically
- `packages/core/src/agent_core/endpoints/scheduler.py` — the cron trigger source, requires a small extension
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — the agent endpoint where compose-time tools mount

---

## Context

Pepper produces daily artifacts that have a known structure: morning brief, evening brief, weekly recap, project briefs, travel prep, meeting prep. Today every one of these is composed ad-hoc by Pepper at runtime — there is no Python builder. Six instances make a pattern: gather data → hand to LLM → synthesize → format → deliver. Doing it ad-hoc means the structure drifts, the data she has to remember explodes, and the deterministic plumbing (data fetching, schema validation, transport) is tangled into the same step where her judgment is supposed to live.

The brief framework formalizes the seam. The deterministic outer layers do the boring work; the LLM does the one thing it's irreplaceable at — synthesis under judgment. Each layer in the format that fits its job:

- **Code:** fetcher + destination implementations, gather/submit engine, validators, transports
- **MD/YAML in agent memory:** playbook section specs + gather config (agent-authored solo)
- **LLM-composed prose:** the section content (agent at compose time)

This is **not** a general agent architecture. It applies to **output-shaped work** with known structure and bounded data — briefs, reports, summaries, drafts. Decision-shaped or exploratory work has no place here.

### Scope check

Six concrete use cases drove the design. Two natural clusters:

- **Cron-driven wide briefs** — morning brief, evening brief, weekly recap. Time-triggered, broad data gather, fixed audience, known cadence.
- **Event/on-demand narrow briefs** — project brief, travel prep, meeting prep. Triggered by an entity (project, trip, meeting), narrow data gather scoped to that entity, on-demand or pre-event.

Both clusters share the same engine: gather → compose-by-section → validate → deliver. The trigger and the "what scope is this brief about" are first-class concepts, not baked-in assumptions.

### Why now

This is part of the Pepper cutover. Cutover #03 ships the Discord verbs Pepper needs; #09 ships the structured-composition pattern that uses those verbs to produce her daily artifacts. Without #09, Pepper post-cutover has the verbs but no clean way to compose her morning brief. The framework is what makes #03 actually useful for her primary daily workflow.

---

## The pattern

```
deterministic gather  →  LLM synthesizes  →  deterministic format + deliver
        │                     │                          │
   Fetcher protocol      Agent compose loop       Destination protocol
   async-concurrent      drives via tools         fan-out, best-effort
   timeout-bounded       schema-validated         audit-logged
```

The agent **drives the compose loop**, not a callback inside the framework. The framework provides tools (`list_sections`, `validate_section`, `submit_brief`); the agent decides when to call them. This preserves voice and judgment: an agent can compress sections, add extensions, or skip optional pieces based on what the day calls for. The framework guarantees shape; the agent guarantees voice.

Each does the thing it's irreplaceable at.

---

## Architecture

### Package layout

A new package `packages/agent-core-briefs/`, peer to `packages/agent-core-discord/`. Independent install. Depends on `agent_core`. Contributes via existing extension surfaces (pluggy hookimpls, MCP tool registration, CLI Typer subapp).

```
packages/agent-core-briefs/
  pyproject.toml                          # entry point: agent_core_briefs.plugin
  src/agent_core_briefs/
    __init__.py                           # public surface
    protocol.py                           # Fetcher, Destination, BriefType, SectionSpec, PlaybookRef, DeliveryResult
    engine.py                             # gather_context, build_wake_message, submit_handler
    playbook.py                           # MD+YAML parser, color palette resolution, section spec validation
    fetchers/
      __init__.py
      filesystem_read.py                  # built-in: read a file/glob into context
      fake_calendar.py                    # built-in (test-only): synthetic calendar data
      loader.py                           # filesystem-discovered fetcher loading
    destinations/
      __init__.py
      discord_embed.py                    # post via DiscordEndpoint to a channel
      markdown_file.py                    # write rendered brief to disk
      loader.py                           # filesystem-discovered destination loading
    validators.py                         # shape checks, max_chars, required fields, embed count
    audit.py                              # ~/.agent-core/briefs/audit.jsonl
    mcp.py                                # tool surface mounted on ClaudeCodeMCPEndpoint
    cli.py                                # `agent-core briefs ...` Typer subapp
    plugin.py                             # pluggy hookimpls for entry-point discovery
  tests/
    ...
```

### Dependency direction

`agent_core_briefs → agent_core` only. Briefs is a **consumer** of agent_core; core never references briefs. Same as agent_core_discord. Briefs registers its plugins, contributes endpoint types, mounts MCP tools, ships CLI subcommands — all via existing extension points.

`agent_core` stays clean. Operators who don't want briefs don't pay for them.

### Configuration locations

Three tiers, all paths configurable in `agent_core.yaml` under the briefs plugin block. Path values support `${var}` substitution against a `vars:` map (config-load-time, not delivery-time — distinct from the `{{when.date}}` templating used in destination paths):

```yaml
# In agent_core.yaml
plugins:
  briefs:
    vars:
      agent_root: ~/.pepper                         # operator-defined; convention is "agent_root"
    playbook_paths:
      - ${agent_root}/Memory/playbooks/             # framework scans for *.md, brief_type from metadata
    fetcher_paths:
      - ${agent_root}/agent/fetchers/
    destination_paths:
      - ${agent_root}/agent/destinations/
    extension_paths:
      - ${agent_root}/agent/extensions/
```

| Tier | Location example | Authored by | Format |
|---|---|---|---|
| Per-brief playbook | `${agent_root}/Memory/playbooks/<brief_type>.md` | Agent, solo | YAML in MD |
| Gather config (per brief) | `${agent_root}/Memory/gather/<brief_type>.yaml` | Agent, solo | YAML |
| Custom fetchers / destinations / extensions | `${agent_root}/agent/{fetchers,destinations,extensions}/*.py` | Agent (no PR gate) | Python |

`vars:` is general-purpose (Pepper can define `external_data_root`, `secrets_root`, anything she needs) — `agent_root` is the documented-best-practice convention, not a special name. Substitution applies to every framework-read config (the briefs plugin block, playbooks, gather YAML), so a single var change at the top propagates through the whole tree.

Both `${var}` substitution and standard `~/` user-home expansion are applied at load time. `${agent_root}` defined as `~/.pepper` becomes `/home/jeffr/.pepper` (or `C:\Users\jeffr\.pepper` on Windows) once both passes run.

---

## Triggers

Three flavors, one mechanism: **anything that can deliver a `BriefRequest` event into the target agent's bus mailbox triggers the framework.** Triggers are external; the framework starts at "we have a request."

### Cron — uses extended `SchedulerEndpoint`

Existing scheduler fires `TextMessage` envelopes only. v1 extends it to also fire `Event` envelopes with structured payloads. Small change (~50 lines):

- `jobs[].publish.envelope_kind: Event` (default `TextMessage` for backward compat)
- `jobs[].publish.payload.type: BriefRequest` (or any Event type)
- `jobs[].publish.payload.data: {...}` (JSON dict)

```yaml
endpoints:
  - type: builtin.scheduler
    name: pepper-scheduler
    params:
      jobs:
        - cron: "0 7 * * *"
          target: pepper
          publish:
            envelope_kind: Event
            payload:
              type: BriefRequest
              data:
                brief_type: morning_brief
                scope: null
```

7am hits, scheduler publishes the Event to Pepper's mailbox.

### Event-driven — calendar/trip watchers (deferred to v2+)

Watcher endpoints subscribe to a calendar source and fire `BriefRequest` events at configured offsets (15min before a meeting, 24h before a trip). Generic in `agent_core_briefs` (anyone can mount one); calendar source is per-agent (Pepper hits Google Calendar with her credentials, another agent might use Outlook).

v2+ scope. v1 ships without watchers; meeting_prep and travel_prep wait.

### On-demand — MCP tool

Mounted on `ClaudeCodeMCPEndpoint` by the briefs plugin. When Jeff asks Pepper "brief me on gstack," she calls:

```python
await compose_brief(brief_type="project_brief", scope="gstack")
```

The tool runs gather inline, hands her the context dict, she composes in the same turn (no bus round-trip — she's already awake), submits.

### CLI — `agent-core briefs compose`

```bash
agent-core briefs compose --type project_brief --scope gstack --agent pepper
```

In-process or remote-publish depending on flags. Useful for ad-hoc, debugging, replays.

---

## Gather

### Fetcher protocol

```python
@runtime_checkable
class Fetcher(Protocol):
    type_id: str          # "filesystem_read", "pepper.daily_log_streak"
    namespace: str        # "tasks", "streaks.eod_log" — where in the context dict

    async def fetch(self, config: dict, when: datetime) -> dict:
        """Return a JSON-serializable dict to merge under namespace."""
```

Pepper writes any Python she wants in her fetcher class. Same shape as built-ins.

### Filesystem-discovered loading

The framework scans configured fetcher paths at gather time, imports each `.py` via `importlib.util.spec_from_file_location`, registers any class satisfying the `Fetcher` protocol by its `type_id`. Hot reload by default — Pepper saves a file, next brief picks it up.

Duplicate `type_id` across files raises `BriefBootError` — fail loud, surface conflicts.

```yaml
# ${agent_root}/Memory/gather/morning.yaml
# (note: fetcher_paths can also be configured globally in agent_core.yaml's
#  briefs plugin block; per-gather-config paths add to that set)
fetchers:
  - type: filesystem_read
    namespace: tasks
    config:
      path: ${agent_root}/Memory/TASKS.md

  - type: pepper.daily_log_streak
    namespace: streaks.eod_log
    timeout_seconds: 300
    config:
      log_path: ${agent_root}/Memory/health/eod-log/
      streak_threshold: 7

  - type: cli
    namespace: github.prs_awaiting_review
    config:
      command: ["gh", "pr", "list", "--state", "open", "--json", "number,title,url,author"]
      cwd: ${agent_root}/code/agent_core
      parse: json
      timeout_seconds: 60
```

### Async-concurrent gather

```python
async def gather_context(fetchers: list[FetcherConfig], when: datetime) -> dict:
    async def _run_one(fc):
        try:
            payload = await asyncio.wait_for(
                fc.fetcher.fetch(fc.config, when),
                timeout=fc.timeout_seconds,
            )
            return fc.namespace, payload
        except Exception as exc:
            return f"_errors.{fc.type_id}", {"error": str(exc), "type": type(exc).__name__}

    results = await asyncio.gather(*[_run_one(fc) for fc in fetchers])
    context = {}
    for namespace, payload in results:
        _merge_into_namespace(context, namespace, payload)
    return context
```

All fetchers fire concurrently. **5-minute default timeout per fetcher** (override per-fetcher in YAML via `timeout_seconds`). Failures land in `context._errors.<type_id>` so the agent sees what fell over in her wake message and the brief proceeds with partial data. One slow Notion API doesn't block the morning.

### Built-in fetchers (v1)

Two production fetchers, both fully agnostic. Pepper writes her own for everything beyond.

- **`filesystem_read`** — read a file or glob into the context. Useful for ledger-style data (`Memory/TASKS.md`, project state markdown, etc.). Config: `path` (glob ok), `format` (`text` | `json` | `yaml` | `lines`).
- **`cli`** — run a CLI command and parse its stdout. Wraps `asyncio.create_subprocess_exec`, captures stdout, parses per `parse` setting, surfaces stderr + non-zero exit code in `_errors`. Most of Pepper's external data sources already have CLI clients (`gh`, `gcalcli`, `gmail-cli`, etc.); a declarative wrapper covers them without Python wrappers.

  ```yaml
  - type: cli
    namespace: github.prs_awaiting_review
    config:
      command: ["gh", "pr", "list", "--state", "open", "--json", "number,title,url,author"]
      cwd: ~/code/agent_core              # optional
      parse: json                          # "json" | "yaml" | "lines" | "text"
      timeout_seconds: 60                  # cli-fetcher-specific timeout (within outer 5min cap)
      env_passthrough: [GH_TOKEN]          # explicit allowlist; the rest of env is dropped
  ```

  Trust note: same threat surface as filesystem-loaded Python fetchers. The CLI fetcher does not introduce new risk — Pepper can already write arbitrary Python; CLI invocation is a strict subset.

A test-only `fake_calendar` fixture lives under `packages/agent-core-briefs/tests/fixtures/` for end-to-end tests. **It is not part of the production fetcher set** — production code never imports it.

Pepper writes custom fetchers in her own repo for sources that need post-processing the CLI parsers can't handle (e.g., a fetcher that calls `gcalcli`, walks the result, and computes a `next_event_warning` derived field). Built-ins stay small and agnostic on purpose.

### Trust model

Filesystem-loaded with no PR gate. Maximum velocity for Pepper. Three lightweight inspection surfaces (none gate, all read-only):

1. `agent-core briefs fetchers list` — what's loaded, where it came from, when last modified
2. `agent-core briefs fetchers test --type pepper.daily_log_streak --config @sample.yaml` — runs one fetcher in isolation
3. Audit log — every fetcher invocation appended to `~/.agent-core/briefs/audit.jsonl`

Jeff has the off-ramp to audit when something feels off. Pepper has max velocity day-to-day.

---

## Wake message (`ComposeBrief` envelope)

The load-bearing artifact the agent receives. Envelope on the bus:

```json
{
  "kind": "Event",
  "from_": "agent-core-briefs",
  "to": "pepper",
  "payload": {
    "type": "ComposeBrief",
    "data": {
      "brief_type": "morning_brief",
      "scope": null,
      "when": "2026-05-04T07:00:00-04:00",
      "session_token": "br_4f2a1c8e9b...",
      "playbook": {
        "ref": "morning-brief.md",
        "sections_required": ["greeting", "calendar_today", "priorities_today"],
        "sections_optional": ["yesterday_recap", "project_status", "open_loops", "watch_list", "email_status"],
        "sections_conditional_active": ["weekly_digest"]
      },
      "context": {
        "now": { "iso": "...", "day_of_week": "Monday", "is_weekly_digest_day": true },
        "calendar": { ... },
        "tasks": { ... },
        "_errors": { "weather": { "error": "API timeout", "type": "TimeoutError" } }
      }
    }
  }
}
```

`session_token` correlates back to the original request. The submit handler uses it to:

- Enforce one-submit-per-token (no double-submission)
- Time out abandoned briefs (token expires after, e.g., 30 minutes)
- Audit-log the request → response chain

Inline context (not by-reference) for v1 because Pepper's brief contexts are KB-sized; we can switch to reference-based later if Vale's briefs ever balloon.

---

## Compose loop (agent-driven)

The agent is in the driver's seat. The framework is a toolkit she invokes.

### Agent tool surface

Mounted on `ClaudeCodeMCPEndpoint` by the briefs plugin. Available to any session that has a live `ComposeBrief` token (or any session that calls `compose_brief` to start one).

| Tool | Purpose |
|---|---|
| `compose_brief(brief_type, scope=None)` | Self-launch entry point. Runs gather inline and returns the context + playbook ref + session_token. The agent then proceeds with the rest of the loop in the same turn. |
| `list_sections(session_token)` | Returns playbook structure: required/optional/conditional section IDs in order, plus filled/empty state of the agent's current draft. |
| `get_section_spec(session_token, section_id)` | Full spec for one section: title, color name, fields, required flags, max_chars, guidance text. |
| `validate_section(session_token, section_id, content)` | Check before submit: returns `{valid: bool, errors: [...]}`. |
| `compress_sections(session_token, [ids])` | Mark a list of sections as compressed into the first one in the list. Allowed only for sections with `allow_compression: true`. |
| `add_extension_section(session_token, spec_id, content)` | Surface a section the playbook didn't predict. Spec must be defined in the agent's extensions. |
| `submit_brief(session_token, sections=[...])` | Atomic validate + format + send. Returns delivery result with per-destination status. |

### Loop semantics

1. Agent receives `ComposeBrief` (cron/watcher) OR calls `compose_brief` (self-launch).
2. Agent reads the wake message: brief_type, scope, context dict, playbook structure, `_errors` from gather.
3. Agent calls `list_sections` to see what the playbook expects.
4. For each section the agent decides to fill:
   - `get_section_spec(section_id)` to see the full spec including `guidance` text
   - Compose the section content using the relevant slice of context
   - Optionally `validate_section` to check her work mid-loop
5. Optional: `compress_sections([id_a, id_b])` if both are nearly empty.
6. Optional: `add_extension_section(spec_id, content)` to surface something playbook-unpredicted.
7. `submit_brief(session_token, sections=[...])`. Framework validates everything, formats per destination, fans out, returns delivery result.

If submit fails on validation, the agent gets granular per-section errors and iterates. If a destination fails after validation passes, the agent gets a partial-delivery result and decides whether to retry.

---

## Submit (atomic validate + format + send)

Single-call atomicity. Agent can't observe "validated but not sent" or "sent but not validated" states.

```python
async def submit_brief(session_token: str, sections: list[dict]) -> SubmitResult:
    """Atomic validate + format + send."""
    request = _resolve_session(session_token)  # raises if expired or already submitted
    playbook = _load_playbook(request.brief_type)

    # 1. Validate
    errors = _validate_all(sections, playbook, request.context)
    if errors:
        return SubmitResult(status="validation_failed", errors=errors)

    # 2. Format + deliver per destination (best-effort)
    results = []
    for dest in playbook.destinations:
        try:
            r = await asyncio.wait_for(
                dest.deliver(sections, playbook, request.scope, request.when, dest.config),
                timeout=dest.timeout_seconds,
            )
            results.append({"type": dest.type_id, "status": "ok", "ref": r.ref})
        except Exception as exc:
            results.append({"type": dest.type_id, "status": "failed", "error": str(exc)})

    # 3. Audit
    _audit_log(request, sections, results)

    # 4. Mark session consumed
    _consume_session(session_token)

    delivered = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]
    return SubmitResult(
        status="ok" if not failed else ("partial" if delivered else "all_failed"),
        delivered=delivered,
        failed=failed,
    )
```

### Validation checks

- All `required: true` sections present.
- All conditional sections evaluated; if `when` is true, treated as required.
- All required fields within each section present, within `max_chars`.
- Total embed count ≤ 10 (Discord hard limit). If sections + active conditionals + extensions exceed 10, returns error and agent compresses or drops.
- Color references resolve from the playbook's palette.
- No unknown section IDs.

### Best-effort delivery semantics

- Each destination has its own timeout (default 60s).
- One destination failing doesn't block others.
- "Delivered" if at least one destination succeeded.
- Per-destination outcomes captured in audit log and returned to the agent.

---

## Destinations

### Destination protocol

```python
@runtime_checkable
class Destination(Protocol):
    type_id: str          # "discord_embed", "markdown_file", "pepper.mobile_app"

    async def deliver(
        self,
        sections: list[dict],
        playbook: PlaybookRef,
        scope: str | None,
        when: datetime,
        config: dict,
    ) -> DeliveryResult:
        """Format sections to native shape and send. Returns ref + metadata."""
```

Same filesystem-discovered model as fetchers. Pepper writes custom destinations (mobile app, Notion database, SMS) in her own repo.

### Built-in destinations (v1)

- **`discord_embed`** — channel post via DiscordEndpoint. Renders section dicts as `discord.Embed` objects with colors resolved from the playbook palette. Posts as one message with all embeds (≤10).
- **`markdown_file`** — writes the brief to disk as markdown. Useful for archival, debugging, and as a fallback when Discord is down.

### Section data shape

For v1: section dicts are **embed-shaped** (`{section_id, fields: [{name, value, inline}]}`) because Discord is the primary destination and the shape is rich enough. Each non-Discord destination adapts:

- `discord_embed` — pass through, native fit
- `markdown_file` — render fields as a markdown definition list per section, with section title as `##` header

If a future destination needs richer semantics, we add an optional `semantic_payload` field to section dicts (`{headline, body, bullets, severity}`) and destinations choose. YAGNI for v1.

### Fan-out

Playbook declares one or more destinations. Same brief delivered to all. Each destination renders independently from the same section data.

---

## Playbook format

Single MD file at `<agent_memory>/playbooks/<brief_type>.md`. Markdown for narrative; fenced YAML blocks for the machine-parseable parts. Framework reloads on each gather — save and you're live.

A full morning_brief example with all 8 sections + conditionals lands as part of the implementation plan deliverables (`docs/examples/playbooks/morning-brief.md`). Format summary below:

### Required blocks

```yaml
# Metadata
brief_type: morning_brief
voice: pepper
schedule: { cron: "0 7 * * *", scheduler: "pepper-scheduler" }
gather_config: ${agent_root}/Memory/gather/morning.yaml
extensions: ${agent_root}/agent/extensions/

# Destinations
destinations:
  - type: discord_embed
    config: { channel_id: "..." }
  - type: markdown_file
    config: { path: "${agent_root}/Memory/daily/briefs/{{when.date}}-morning.md" }
                       #     ${var}: load-time              {{ var }}: delivery-time

# Color palette (named handles, not decimals)
colors:
  MORNING_GREETING: 5814783
  CALENDAR: 3447003
  ...
```

### Section blocks (one per section, in order)

```yaml
section_id: greeting
title: "🌅 Morning, Jeff"
color: MORNING_GREETING                     # static, references palette
required: true
required_context: [now, weather.summary]
allow_compression: false
fields:
  - name: "Today"
    required: true
    max_chars: 256
    guidance: "One-line frame for the day. Day-of-week + weather hint. ..."
```

Dynamic colors:

```yaml
color:
  dynamic: true
  expr: "len(email.urgent) > 0"
  if_true: EMAIL_URGENT
  if_false: EMAIL_OK
```

### Conditional sections

```yaml
section_id: weekly_digest
title: "📊 Week ahead"
color: PRIORITIES
when:
  expr: "now.is_weekly_digest_day"
required_when_active: true
fields:
  - name: "This week"
    required: true
    guidance: "..."
```

### Expression language

Small subset of Python evaluated against the gathered context dict. **Sandboxed via [`simpleeval`](https://pypi.org/project/simpleeval/)** — small, vetted, no transitive deps, exactly the right size for this. Supports:

- Attribute access (`now.day_of_week`)
- Bracket access (`email.urgent[0]`)
- Comparison operators
- Boolean operators (`and`, `or`, `not`)
- Length / membership (`len(x)`, `x in y`)
- No function calls except a small whitelisted set (`len`, `any`, `all`, `bool`)
- No imports, no attribute access into Python internals

---

## Audit log

Single append-only JSONL file at `~/.agent-core/briefs/audit.jsonl`. One line per significant event:

| Event | Captured |
|---|---|
| `request_received` | brief_type, scope, when, trigger_source, session_token |
| `gather_started` | session_token, fetchers list |
| `gather_completed` | session_token, duration_ms, per-fetcher status (ok/timeout/error), context size bytes |
| `wake_published` | session_token, target agent, envelope id |
| `submit_attempted` | session_token, section count, validation result |
| `delivery_completed` | session_token, per-destination status (ok/timeout/error), delivery refs |
| `session_consumed` | session_token, total wall time from request to delivery |
| `session_expired` | session_token, reason (no submit within TTL) |

Same shape as the bus log audit pattern from cutover #04. Operator can grep, `jq`, or build a dashboard later.

---

## v1 scope boundary

### What ships

- Package scaffold + dependency wiring + plugin entry point
- `Fetcher`, `Destination`, `BriefType`, `SectionSpec`, `PlaybookRef`, `DeliveryResult` protocols/types
- Gather engine (async-concurrent, filesystem-loaded fetchers, audit)
- `SchedulerEndpoint` extension to fire `Event` envelopes (~50 lines on the existing endpoint)
- `ComposeBrief` envelope shape + handler
- Submit handler (atomic validate + format + send)
- Built-in fetchers: `filesystem_read`, `cli` (production); `fake_calendar` lives under `tests/fixtures/` and is test-only
- Built-in destinations: `discord_embed`, `markdown_file`
- Playbook MD parser + color palette resolution + simple expression language
- Agent tool surface (7 tools listed above)
- MCP integration — tools mounted on `ClaudeCodeMCPEndpoint`
- CLI subapp (`agent-core briefs compose | fetchers list | fetchers test`)
- Audit log writer
- One end-to-end test driving a stub morning_brief through cron → gather → wake → compose (stub agent) → submit → both destinations → audit
- Tripwire test against an example morning_brief playbook (mirrors the cutover #04 yaml tripwire pattern)

### What defers to v2+

- Extensions protocol (`provide_context`, `declare_sections`, `gate`, `compose_override`, `post_validate`)
- Calendar/trip watcher endpoints
- The other 5 use cases (evening_brief, weekly_recap, project_brief, travel_prep, meeting_prep)
- Pepper's mobile-app destination
- Real Google Calendar / Gmail fetchers (Pepper writes those in her own repo using v1's protocols)
- `agent-core briefs fetchers test` advanced features (history, recorded fixtures)
- Reference-based context payload (instead of inline) when contexts grow
- Section-level rate limits and recovery hooks
- Cross-section coherence pass (a final-pass LLM call to check consistency between sections)

---

## Done looks like

- `agent-core briefs compose --type morning_brief --agent pepper-stub` runs end-to-end against a stub playbook with stub fetchers, produces a Discord embed (caught by a fake DiscordEndpoint) and a markdown file, both shape-validated.
- The same flow fires from cron via the extended `SchedulerEndpoint` — a `BriefRequest` Event lands in the stub agent's mailbox, the agent's session calls the tool surface, submits, framework delivers.
- `~/.agent-core/briefs/audit.jsonl` captures the full request → delivery chain for the test run.
- An example playbook lives at `docs/examples/playbooks/morning-brief.md` with a tripwire test that asserts its structure.
- One Pepper-specific gather config lives at `docs/examples/playbooks/morning-gather.yaml` showing the YAML shape.
- Test playbook for cutover #09 lands at `docs/cutover/test-playbooks/09-brief-framework.md`.
- Ledger updates: `pepper-pre-cutover-must-haves.md` adds row #09, `pepper-cutover-agent-playbook.md` adds the per-ticket entry.

---

## Known limitations

- **Single-agent live session required for cron-driven briefs.** If Pepper's session isn't available when 7am fires, the brief sits in her mailbox until she wakes. Acceptable today (her session is event-triggered by Discord/scheduler), but if multi-agent operation grows, may need a "compose without live session" fallback path.
- **No cross-section coherence guarantees.** Per-section LLM calls (or per-section composition by the host agent) means section 5 might say "as I noted above" when section 1 didn't actually note that. Validation is shape-only, not semantic. Mitigation possible via a v2+ coherence pass.
- **Extensions protocol punted.** Pepper proposed a rich extension shape (`provide_context`, `declare_sections`, `gate`, `compose_override`, `post_validate`). v1 implements none of these — the protocol surface is documented but not wired. Sections in playbooks can reference extension hooks, and the framework will fail loudly when one isn't found, but no extension implementations ship.
- **Watchers deferred.** `meeting_prep` and `travel_prep` need calendar/trip watcher endpoints to fire automatically. v1 ships without them — those briefs can be triggered manually via MCP/CLI but won't fire automatically on event.
- **No log rotation.** `audit.jsonl` grows unbounded. Same trade-off as the bus log; rotation is a follow-up if it becomes operationally painful.
- **Trust model: filesystem-loaded code, no gate.** Pepper writes Python and the framework runs it with the agent's privileges. Mitigations (process isolation, resource budgets, network egress allowlist) deferred until needed.

---

## Decided (during spec drafting and review)

- **Expression language:** `simpleeval`. Small, sandboxed, no transitive deps, exactly the right size.
- **`scope` substitution / path templating:** small custom regex+dot-attribute formatter (~20 lines), no library. Handles `{{when.date}}`, `{{scope.channel_id}}`, etc. — flat var substitution with dot-attribute access. If v2+ needs loops/conditionals/filters in templates, escalate to jinja2 then. YAGNI for v1.
- **Production fetchers v1:** `filesystem_read` and `cli` only. `fake_calendar` lives under `tests/fixtures/` and is never imported by production code.
- **Path-var substitution in briefs config (`${var}`).** The briefs plugin block in `agent_core.yaml` supports a `vars:` map; references like `${agent_root}/Memory/playbooks/` resolve before the framework consumes paths. Convention: define `agent_root` in `vars`, reference it everywhere a path lives under the agent's memory. Move the memory dir → change one line. Mechanism is general (any var name, not magical); convention is documented in the authoring skill. Implementation: ~10 lines walking the config tree, fail-loud on undefined refs. **Distinct from** the `{{when.date}}`-style templating that runs at *delivery* time on destination paths — `${var}` is config-load-time substitution.
- **Field-level `guidance` storage — both shapes supported from v1.** Inline string for short guidance (most fields), file reference for long guidance (voice-critical sections that need paragraphs, examples, counterexamples). The skill documents the rule of thumb: inline if it fits in a sentence or two; file-ref if it needs paragraphs or examples.

  ```yaml
  # Short — inline (most fields)
  fields:
    - name: "Top 3"
      guidance: "Highest-priority items for today. 3 max. Bullet format."

  # Long — file ref (voice-critical sections)
  fields:
    - name: "Today"
      guidance: { file: greeting-today-guidance.md }   # path relative to playbook
  ```

  Cost is trivial (~5 lines to resolve), benefit is preserving voice on the sections where guidance complexity matters most.

## Open decisions

(None blocking the implementation plan. The two formerly-open items above settled during spec review; field-level guidance storage and path resolution are both decided. Further decisions emerge during plan/implementation as concrete questions.)
