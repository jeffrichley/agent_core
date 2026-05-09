# Issue #70 — Inline-content wake via relay-side prefetch

**Status:** Design approved 2026-05-09. Ready for implementation plan.
**Issue:** [#70](https://github.com/jeffrichley/agent_core/issues/70)
**Predecessors:** #54 (auto-clear routine acks, merged), #67 (consume + reply, merged), #69 (publish/register race fix, merged), #37 (tools/list_changed on deferred-mounter drain, merged).

## Goal

Drop the per-Discord-round-trip floor from 2 tool calls to 1 by making the inbound envelope's content available to the agent inline with the wake notification, eliminating the `consume()` fetch round.

The behavior the agent perceives:

| Stage | calls/round-trip | tokens (~500K cached) |
|---|---|---|
| Pre-#54 | 5 | ~250K |
| Post-#54 | 3 | ~150K |
| Post-#67 | 2 | ~100K |
| **Post-#70** | **1** (single inbound + reply) or 2 (cross-channel `send`) | ~50K |

The architectural shift: stop making the agent fetch what the bus already has. The wake stops being "go look" and becomes "here's what arrived."

## Architecture overview

**Implementation layer: the relay (`agent-core-channel`), not the bus.** The bus daemon's wake notification stays exactly today's shape — `INBOX: pending (<endpoint>)` plus minimal meta. **No bus protocol change.**

The relay (`agent_core_channel`) grows MCP-client capability: on every wake, it calls `consume(auto_ack=False, batch_window_seconds=0)` against the bus, applies per-kind rendering with safe encoding, applies a circuit breaker for pathological bursts, and emits a richer `notifications/claude/channel` notification to the agent's Claude Code session.

This is the cleanest answer to "the bus disappears as protocol the agent has to think about." The protocol stays small; the substrate gets smarter.

```
┌──────────────┐  wake (bare)   ┌──────────────────┐  consume()    ┌─────────────┐
│ Bus daemon   │───────────────▶│ Relay            │──────────────▶│ Bus daemon  │
│              │  via SSE       │ (this design)    │  via MCP HTTP │ MCP host    │
│ /notify/X    │                │                  │               │ /mcp/X      │
└──────────────┘                │  ┌────────────┐  │               └─────────────┘
                                │  │ render +   │  │
                                │  │ encode +   │  │
                                │  │ truncate   │  │
                                │  └────────────┘  │
                                └────────┬─────────┘
                                         │ richer notification
                                         │ via stdio MCP
                                         ▼
                                ┌──────────────────┐
                                │ Claude Code      │
                                │ session          │
                                └──────────────────┘
```

### Key invariants

- **Bus protocol unchanged.** Today's wake shape stays.
- **No auto-ack on prefetch.** Relay calls `consume(auto_ack=False)`. Acks happen via the agent's subsequent `reply()` or `handle()` call. Session crash between push and reply leaves the envelope in the queue — no silent drops.
- **Atomicity weakens (acceptable).** The relay's consume call and its emit are not transactionally tied to the wake-fire moment. Late-arriving envelopes belong to the next debounce window — same outcome as today's `list_pending` snapshot, just enforced at the relay rather than the bus core.
- **Encoding safety is mandatory.** Arbitrary user content (Discord text, code, unbalanced characters) cannot break the agent's parse. Verified by the contract test below.
- **Per-kind rendering ships day-one defaults.** Plugin-provided kinds get a JSON fallback. Per-kind hooks are a separable future ticket.

## Components

The work splits into six phases in one PR. Each phase is a reviewable commit (or small commit cluster) with its own tests.

### Phase 1 — `peek(envelope_id)` tool

New MCP tool on `ClaudeCodeMCPEndpoint`. Pure read; no side effects.

**Signature:**

```python
@self._mcp.tool()
async def peek(envelope_id: str) -> dict:
    """Return one specific envelope from the pickup queue without acking it.

    Used by the agent to hydrate a truncated inline preview into full payload
    when the relay's circuit breaker elided content. Also useful for power-use
    cases (manual triage of a specific envelope without disturbing other
    queue state).

    Raises if envelope_id is not in the queue.
    """
```

**Behavior:**

- Looks up the envelope in `self._pending`. Returns the envelope dict (same shape as `_envelope_to_dict`).
- Raises `ValueError` if `envelope_id` not in `_pending`.
- Does NOT consult `_recent_inbounds` (the #67 routing cache holds metadata only, not full payload).
- Does NOT call `_handle.ack` or modify `_pending`.
- Idempotent — multiple calls return the same data.

**File:** `packages/core/src/agent_core/endpoints/claude_code_mcp.py`. Same place as `consume`/`reply`/`handle`.

**Tests:** new file `packages/core/tests/test_claude_code_mcp_peek.py`. Coverage: returns envelope on hit, raises on miss, no `_pending` mutation, no `_handle.ack` call, multiple calls idempotent.

### Phase 2 — Rendering pipeline (pure functions)

New module `agent_core_channel.rendering` containing:

**Per-kind renderers** — dispatch dict mapping `kind → render_fn(env: dict) -> str`:

| Kind | Renderer output |
|---|---|
| `TextMessage` | `<inbox from='X' urgency='Y' envelope_id='Z' kind='TextMessage'>\n{escaped payload.text}\n</inbox>` |
| `Acknowledgment` (non-routine: yellow/red, or `note` prefixed `error:`) | `<inbox kind='Acknowledgment' urgency='X' in_reply_to='Y' envelope_id='Z'>\n{escaped note or stringified payload}\n</inbox>` |
| `Event` | `<inbox kind='Event' subtype='X' envelope_id='Z'>\n{compact JSON of payload}\n</inbox>` |
| `BriefRequest`, `ToolInvocation`, `Progress`, `ComposeBrief`, plugin-defined kinds | `<inbox kind='<kind>' envelope_id='Z'>\n{compact JSON of payload}\n</inbox>` |
| Unknown kind / renderer error | `<inbox kind='<kind>' envelope_id='Z' render='fallback'>\n{repr(payload), capped}\n</inbox>` |
| Total render failure | bare-wake fallback (handled in Phase 4 wiring) |

Attribute values (`from`, `urgency`, `envelope_id`, `kind`) are bounded enums or hex IDs — no user content, no escaping needed.

**Body-encoding function** — HTML escape applied to body content uniformly across all renderers:

```python
def encode_body(text: str) -> str:
    """Escape body content for safe inclusion in <inbox> tag.

    Applies HTML escape: & → &amp;, < → &lt;, > → &gt;, ' → &apos;, " → &quot;.
    Idempotent. Reversible by the LLM during reading (Claude Code understands
    HTML-escaped text natively).
    """
```

**Circuit-breaker function:**

```python
def apply_circuit_breaker(
    envelopes: list[dict],
    *,
    max_envelopes: int,
    max_total_bytes: int,
    max_per_envelope_bytes: int,
) -> CircuitBreakerResult:
    """Decide what to inline vs. what to elide vs. fallback.

    Returns one of:
      - InlineAll(rendered: list[str]) — all envelopes inline, possibly with
        per-envelope truncation markers for oversized individual ones.
      - FallbackToBare(reason: str) — too many envelopes or total too big;
        emit today's bare wake shape, agent calls consume() manually.
    """
```

Decision logic, in order:

1. If `len(envelopes) > max_envelopes` → `FallbackToBare("envelope count")`.
2. Render each envelope. If a single rendered envelope's body exceeds `max_per_envelope_bytes`, replace its body with the truncation marker:  
   `[content elided; envelope_id='<id>'; call peek('<id>') for full payload]`  
   (Failure-class envelopes — yellow/red urgency or `error:` notes — still apply this per-envelope cap for safety, but bypass the batch cap below.)
3. Sum all rendered body sizes. If `total > max_total_bytes` AND no failure envelopes are present → `FallbackToBare("total bytes")`. Failure envelopes always inline (loudness invariant).
4. Otherwise → `InlineAll(rendered)`.

**Sort ordering:** envelopes are pre-sorted by urgency descending (red → yellow → green), FIFO within tier. Same as `list_pending`.

**Batch collapsing:** the bus daemon already implements collapsing via `consume(batch_window_seconds=N)` — the relay reuses it by passing `batch_window_seconds=30` (matches `consume`'s tool default). Items in the response can be either flat envelope dicts (no batching at this window) or `{"type": "single", "envelope": {...}}` / `{"type": "batch", "from": ..., "kind": ..., "envelopes": [...], ...}` shapes. The renderers handle all three:

- Flat envelope dict → single `<inbox>` block.
- `{"type": "single", "envelope": E}` → single `<inbox>` block (same as flat, just unwrapped).
- `{"type": "batch", "envelopes": [E1, E2, ...]}` → N `<inbox>` blocks with a small batch-prefix marker (e.g. `[BATCH 1/3]`) on each, so the agent sees the full content of each underlying envelope and can ack/reply each individually using its own `envelope_id`.

**Redelivery markers:** if an envelope id was previously seen in a wake (tracked in a small in-memory LRU cache, e.g. last 200 IDs), inject a `resend_count='N'` attribute into the rendered `<inbox>` tag (where N is the sighting number — 2 on second sighting, 3 on third, etc.). Attribute form composes cleanly with the existing tag attributes and survives XML round-trip.

**File:** new `packages/agent-core-channel/src/agent_core_channel/rendering.py`.

**Tests:** new `packages/agent-core-channel/tests/test_rendering.py`. Coverage:

- Per-kind renderer output shape for each kind in the table.
- HTML-escape correctness (idempotent; covers `&`, `<`, `>`, `'`, `"`).
- **Encoding contract test** (Pepper's verification target): feed `<script>alert(1)</script>`, `2 < 3 & 5 > 4`, `</inbox>`, multiline, emoji-laden text — assert resulting `<inbox>` block is parseable (LLM-agnostic: a plain XML/HTML parser succeeds and content round-trips).
- Circuit-breaker boundary cases: exactly N envelopes summing to exactly M bytes; oversized single envelope triggers truncation; failures bypass batch cap; multiple failures still respect per-envelope cap.
- Truncation marker shape (exactly the documented format, references `peek()`).
- Redelivery marker on second sighting.

### Phase 3 — Relay MCP-client wiring

The relay grows the ability to talk to the bus's MCP HTTP host as an MCP client.

**Connection pattern:** persistent. One MCP session opened on relay startup, reused for every wake. Reconnects on connection loss with exponential backoff (matches `iter_notify_events`'s pattern: 2s → 4s → 8s, cap 30s, reset on success).

**File:** new `packages/agent-core-channel/src/agent_core_channel/bus_client.py`. Exposes:

```python
class BusClient:
    """Persistent MCP client connection to the bus daemon.

    On connection loss, reconnects in the background with exponential backoff.
    Callers see await-able tool calls; reconnect is transparent unless backoff
    exhausted.
    """

    async def __aenter__(self) -> "BusClient": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def consume_no_ack(
        self, *, batch_window_seconds: int = 0
    ) -> dict: ...
```

Connects to `{daemon_url}/mcp/{agent}` using `streamable_http_client` (same path Pepper's Claude Code session uses; multiple sessions per path are already supported).

**This phase only adds plumbing.** The SSE pump still does today's identity passthrough — wake events are emitted unchanged. The `BusClient` is constructed and connected, but nothing calls it yet.

**Tests:** new `packages/agent-core-channel/tests/test_bus_client.py`. Use an in-process `ClaudeCodeMCPEndpoint` as a fake bus to verify:

- Successful connection and `consume_no_ack` call against a populated queue.
- Reconnect on simulated connection drop.
- Backoff respects test-injected timing.

### Phase 4 — Wire it up (the behavior change)

Replace the SSE pump's identity passthrough with the render pipeline.

**Modified file:** `packages/agent-core-channel/src/agent_core_channel/stdio_server.py`. The `_sse_pump` function changes from:

```python
async for summary in iter_notify_events(...):
    await emit_channel_notification(write_stream, summary)
```

to roughly:

```python
async for wake_summary in iter_notify_events(...):
    wake_id = uuid.uuid4().hex  # relay-generated, unique per wake
    if config.inline_mode == "disabled":
        await emit_channel_notification(write_stream, wake_summary)
        wake_audit.write(wake_id, fallback="disabled_mode")
        continue
    try:
        snapshot = await bus_client.consume_no_ack(batch_window_seconds=30)
        items = snapshot["items"]
        if not items:
            # Phantom wake — suppress agent-facing notification but still log
            # so we have visibility if it becomes frequent.
            wake_audit.write(wake_id, fallback="empty_queue")
            continue
        result = apply_circuit_breaker(
            items,
            max_envelopes=config.max_envelopes,
            max_total_bytes=config.max_bytes,
            max_per_envelope_bytes=config.per_envelope_bytes,
            mode=config.inline_mode,  # "full" or "preview"
        )
        if isinstance(result, FallbackToBare):
            await emit_channel_notification(write_stream, wake_summary)
            wake_audit.write(wake_id, fallback=result.reason)
            continue
        rendered_content = "\n\n".join(result.rendered)
        rich_summary = {
            "content": rendered_content,
            "meta": {
                **wake_summary.get("meta", {}),
                "wake_id": wake_id,
                "queue_total_count": str(snapshot["meta"]["count"]),
                "envelopes_inlined": ",".join(result.inlined_ids),
            },
        }
        await emit_channel_notification(write_stream, rich_summary)
        wake_audit.write(wake_id, envelopes_inlined=result.inlined_envelopes_summary)
    except Exception as exc:
        log.warning("relay: rendering failed; falling back to bare wake: %s", exc)
        await emit_channel_notification(write_stream, wake_summary)
        wake_audit.write(wake_id, fallback="render_error", error=str(exc))
```

**Behavior outcomes for each branch:**

- **`inline_mode == "disabled"`** — pure passthrough, today's behavior. Escape hatch.
- **Empty queue (phantom wake)** — suppress notification entirely. Agent doesn't see a wake at all (matches Pepper's "the shape never exists" requirement).
- **Circuit breaker tripped** — emit today's bare wake; agent calls `consume()` manually.
- **Render exception (any reason)** — fall back to bare wake; log the error. Inline is the fast path; the slow path always works.
- **Normal case** — emit the rich summary with content inline.

**Updated `_RELAY_INSTRUCTIONS`** describes both the inline shape (when `inline_mode != disabled`) and the fallback bare-wake shape (when circuit breaker trips or rendering fails). The instructions guide the agent: "if you see content inline, it's authoritative; if you see bare wake, fetch via `consume()`."

**Wake-audit JSONL writer** also lands in this phase (Phase 5's analyzer builds on it). New file `packages/agent-core-channel/src/agent_core_channel/wake_audit.py` — mirrors `mcp_audit/writer.py`'s pattern (append-only JSONL, atomic line writes, file rotation on size). Schema:

```json
{
  "ts": "2026-05-09T03:29:22Z",
  "agent": "pepper",
  "wake_id": "w-abc123",
  "mode": "full",
  "envelopes_inlined": [
    {"id": "e-001", "kind": "TextMessage", "from": "discord-pepper", "urgency": "green", "bytes": 87}
  ],
  "queue_total_count": 1,
  "fallback": null
}
```

When `fallback` is set (`"envelope_count"`, `"total_bytes"`, `"render_error"`, `"empty_queue"`, `"disabled_mode"`, `"bus_unreachable"`), `envelopes_inlined` may be empty.

`wake_id` is a relay-generated UUID per wake (always unique, even across relay restarts).

**Tests:** integration in `packages/agent-core-channel/tests/test_stdio_server.py`. Use an in-process bus with a populated queue. Verify:

- Single-envelope wake produces inline rendered notification.
- Multi-envelope batch produces correctly-ordered inline rendering.
- Circuit-breaker trip falls back to bare wake.
- Phantom wake (empty queue) suppresses notification.
- Render exception falls back to bare wake.
- `inline_mode=disabled` is pure passthrough.

### Phase 5 — Analyzer CLI

`agent-core wake-stats` subcommand that joins the relay's wake-audit JSONL (written in Phase 4) with the bus's existing `mcp_audit` JSONL to compute per-wake outcomes. The rollout gate's data source.

**Inputs:**

- Relay's wake-audit at `~/.agent-core/wake_audit/<agent>.jsonl` (produced in Phase 4).
- Bus's MCP audit at `~/.agent-core/mcp_audit/<agent>.jsonl` (already written by the existing audit middleware — no bus-side changes).

**Join logic:** for each wake, find the next bus-audit entry from the same agent within a configurable window (default 5 min). Classify the outcome:

| Classification | Meaning |
|---|---|
| `replied` | next call was `reply(in_reply_to=X)` for an inlined `X` |
| `handled` | next call was `handle(envelope_id=X)` for an inlined `X` |
| `engaged-with-fetch` | next call was `peek(X)` then `reply`/`handle` |
| `side-action` | next call was unrelated (e.g., `send` to a different recipient — Pepper's cross-channel 2-call case) |
| `ignored` | no call within window |

**Output:** rolling rates over the last N wakes. The 30% rollout gate compares (`ignored` + `side-action`) vs. total.

**Files:**

- New `packages/core/src/agent_core/wake_stats.py` — analyzer logic.
- Wire CLI subcommand into `packages/core/src/agent_core/cli.py`.

**Tests:** unit tests on the analyzer using sample wake-audit + mcp-audit fixtures, verifying classification output for each category and edge cases (wake with no following call within window, wake followed by multiple calls, wake whose follow-up references an envelope not in the inlined set).

### Phase 6 — Rollout-gate config

The four-knob configuration with layered precedence (CLI > env > YAML > defaults).

**Config dataclass:**

```python
@dataclass(frozen=True)
class RelayConfig:
    inline_mode: Literal["full", "preview", "disabled"] = "full"
    max_envelopes: int = 5
    max_bytes: int = 8192
    per_envelope_bytes: int = 4096
```

**Resolver:**

```python
def load_config(
    *,
    agent: str,
    config_path: Path | None,  # default: ~/.agent-core/agent_core.yaml
    cli_args: argparse.Namespace,
    env: Mapping[str, str],
) -> RelayConfig:
    """Resolve config with precedence: CLI > env > YAML > defaults."""
```

**YAML schema** (extends each `claude_code_mcp` endpoint optionally):

```yaml
endpoints:
  - type: builtin.claude_code_mcp
    name: pepper
    params:
      mount: /mcp/pepper
      ...existing params...
      channel_relay:
        inline_mode: full
        max_envelopes: 5
        max_bytes: 8192
        per_envelope_bytes: 4096
```

If the `channel_relay` block is absent, hardcoded defaults apply. Existing endpoints don't need YAML changes.

**CLI flags** added to relay's `__main__.py`:

```
--config-path PATH                  # default: ~/.agent-core/agent_core.yaml
--inline-mode {full,preview,disabled}
--inline-max-envelopes INT
--inline-max-bytes INT
--inline-per-envelope-bytes INT
```

**Env vars** with `AGENT_CORE_CHANNEL_` prefix: `INLINE_MODE`, `INLINE_MAX_ENVELOPES`, `INLINE_MAX_BYTES`, `INLINE_PER_ENVELOPE_BYTES`.

**Preview mode (the gate fallback):** identical to full mode except every envelope's body content is replaced with the truncation marker, with up to 200 leading characters of the body kept as a preview before the marker. Concretely, a TextMessage in preview mode renders as:

```
<inbox from='X' urgency='Y' envelope_id='Z' kind='TextMessage' preview='true'>
hey can you check on the deploy quickly when you get a chance — the build…
[content elided; envelope_id='Z'; call peek('Z') for full payload]
</inbox>
```

If the body is shorter than 200 chars, the entire body is the preview and the truncation marker is appended (still pointing at `peek()` in case the agent wants to grab the whole envelope dict for routing or metadata reasons). One branch in the renderer; same code path as the per-envelope-cap truncation in full mode.

**File:** new `packages/agent-core-channel/src/agent_core_channel/config.py`.

**Tests:** unit tests for each precedence layer (CLI > env > YAML > defaults), plus a "missing YAML file is fine" test, plus a "preview mode emits truncated output" test.

## Data flow (single-envelope happy path)

1. Discord adapter receives a message → publishes `TextMessage` envelope to bus.
2. Bus's `ClaudeCodeMCPEndpoint.deliver()` runs:
   - `_is_routine_green_ack(envelope)` returns False (it's a TextMessage, not an ack).
   - Envelope queued via `queue_for_pickup` → `_pending`.
   - `_notify_mail_arrived(urgency="green")` schedules debounced wake.
3. After debounce (1s for green), `_fire_after_debounce`:
   - Builds today's bare wake summary: `{"content": "INBOX: pending (pepper)", "meta": {"endpoint": "pepper", "fired_at": "..."}}`.
   - Publishes via `notify_broker.publish` (SSE) and pushes to attached HTTP MCP sessions.
4. Relay's `iter_notify_events` yields the bare wake summary.
5. Relay's `_sse_pump` (Phase 4) does:
   - Calls `bus_client.consume_no_ack(batch_window_seconds=0)` against `/mcp/pepper`.
   - Bus's `consume()` returns `{"meta": {...}, "items": [envelope_dict]}` (no ack — `auto_ack=False`).
   - Renders the envelope via the dispatch dict → `<inbox from='discord-pepper' ...>...</inbox>`.
   - Applies HTML escape to body.
   - Circuit-breaker: 1 envelope, ~100 bytes — passes.
   - Builds rich summary: `content` is the rendered block; `meta` carries `wake_id`, `envelopes_inlined: ["envelope-id"]`, `queue_total_count: 1`.
   - Calls `emit_channel_notification(write_stream, rich_summary)`.
   - Writes wake-audit JSONL line.
6. Pepper's Claude Code session receives the `notifications/claude/channel` notification with content inline.
7. Pepper reads, calls `reply(in_reply_to="envelope-id", payload=...)`.
8. Bus's `reply()` (per #67): registers outbound, publishes, acks the inbound atomically. Inbound removed from `_pending`.
9. Bus audit middleware logs the `reply` call.
10. (Later) `agent-core wake-stats` joins wake-audit + bus-audit, classifies wake as `replied`.

**1 tool call. Round-trip closed.**

## Error handling

| Failure | Relay behavior | Agent perception |
|---|---|---|
| Bus unreachable on `consume()` (network / daemon down) | Retry once, then fall back to bare wake. Log error. | Sees bare wake `INBOX: pending (...)`; calls `consume()` manually. |
| Bus reachable but `consume()` raises (schema mismatch, etc.) | Catch, fall back to bare wake. Log error. | Same as above. |
| Empty queue (phantom wake — shouldn't happen post-#69, but possible) | Suppress notification entirely. Log at INFO. | Doesn't see a wake. |
| Render exception for one envelope (kind without renderer + repr fails) | That envelope renders as fallback `<inbox kind='X' render='fallback'>repr...</inbox>`. Other envelopes in batch render normally. | Sees fallback marker for that envelope, others normal. |
| All envelopes fail to render | Fall back to bare wake. Log error. | Sees bare wake; calls `consume()`. |
| Encoding error (bug in escaper — shouldn't happen) | Fall back to bare wake. Log error with stack trace. | Sees bare wake. |
| Circuit breaker trips | Fall back to bare wake. Log at INFO with reason. | Sees bare wake; calls `consume()`. |
| Bus-client connection lost mid-wake | Reconnect attempt; if too slow, fall back to bare wake for this wake. Subsequent wakes see fresh connection. | Sees bare wake for the affected wake. |

The principle: **inline is the fast path; the slow path always works.** Any failure mode degrades gracefully to today's behavior.

## Testing strategy

**Phase 1 (peek):** unit tests on `claude_code_mcp.py` peek tool. Coverage: hit, miss, idempotency, no side effects.

**Phase 2 (rendering):** unit tests on each renderer + the encoder + the circuit breaker. Includes the encoding contract test (`<script>alert(1)</script>` and friends round-trip parseably).

**Phase 3 (bus client):** integration tests with an in-process bus. Cover successful call, reconnect, backoff timing.

**Phase 4 (wire-up + JSONL writer):** integration tests with the in-process bus producing wakes. Cover all error-handling branches. Plus unit tests on the JSONL writer (rotation, atomic writes, schema correctness, fallback markers).

**Phase 5 (analyzer):** unit tests on the analyzer using sample wake-audit + mcp-audit fixtures, classification correctness for each outcome category.

**Phase 6 (config):** unit tests for each precedence layer. Plus a "missing YAML is fine" test, plus a "preview mode emits truncated output" test.

**Cross-cutting:** mypy + ruff clean across all touched files. Existing tests in `packages/core/tests/` and `packages/agent-core-channel/tests/` continue to pass.

## Acceptance criteria (Pepper's verification target)

After merge + daemon restart + Pepper session relaunch, verified live in `#pepper-upgrade`:

- Per-Discord-round-trip tool call count drops from 2 to 1 in the single-inbound-with-reply case.
- Multi-inbound batch: all envelopes inlined in one wake; agent can `reply`/`handle` each individually without an intermediate `consume()`. Circuit breaker triggers cleanly on contrived large bursts (e.g., 10-envelope flood falls back to bare wake).
- Failure ack content (yellow/red urgency, or `error:` notes) is inlined verbatim and visible without a fetch round.
- Encoding safety: send a Discord message containing `<script>alert(1)</script>` and various unbalanced characters; verify Pepper's parse is unaffected.
- No regression on #33 atomic snapshot contract: contrived race of "envelope arrives during dispatch" never produces an inline notification with stale `queue_total_count` relative to envelopes inlined.
- Cross-restart: envelopes persist across daemon restart; on restart, the next wake snapshot includes them and Pepper sees them inlined.
- After ~1 week of `agent-core wake-stats` data, no-engagement rate is below 30% (rollout gate stays at `inline_mode=full`); if above 30%, switch Pepper's relay to `--inline-mode=preview`.

## Out of scope

- Per-kind plugin rendering hooks (separable future ticket; day-one defaults are the JSON fallback for unknown kinds).
- Auto-ack-on-push for the no-reply case (explicitly rejected by Pepper — risks silent drops on session crash).
- A dedicated `dismiss(envelope_id)` tool (today's `handle()` covers it).
- Two-channel split (separate notification method) — current design keeps the existing `notifications/claude/channel`.
- Bus-side wake event log (relay-side JSONL is the chosen surface; bus daemon untouched).
- Real-time alarms on the rollout-gate metric (weekly judgment call by Jeff is the trigger).

## Configuration knobs (for ops reference)

All configurable via CLI > env > YAML > hardcoded defaults:

| Knob | Type | Default | Meaning |
|---|---|---|---|
| `inline_mode` | enum | `full` | `full` = full content inline (today's plan); `preview` = 200-char preview + peek; `disabled` = bare wake only (escape hatch) |
| `max_envelopes` | int | 5 | If a snapshot has more than this, fall back to bare wake |
| `max_bytes` | int | 8192 | If total rendered body bytes exceed this, fall back to bare wake (failure envelopes bypass this cap but respect per-envelope cap) |
| `per_envelope_bytes` | int | 4096 | If a single envelope's body exceeds this, replace its body with the truncation marker pointing at `peek()` |

## Related design docs

- `docs/superpowers/specs/2026-04-29-channel-relay-design.md` — original relay design.
- `docs/superpowers/specs/2026-04-27-channel-bus-design.md` — bus architecture.
- `docs/superpowers/specs/2026-04-28-bus-daemon-design.md` — daemon lifecycle.

## Open questions

None — all resolved during brainstorming on 2026-05-09. The design is ready for implementation.
