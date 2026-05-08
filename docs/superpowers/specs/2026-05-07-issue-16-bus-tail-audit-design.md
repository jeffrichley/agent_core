# Issue #16 — Read-only bus tail / audit feed (Design)

> **Status:** Drafted 2026-05-07. Awaiting Jeff's review.
>
> **Issue:** [#16](https://github.com/jeffrichley/agent_core/issues/16) — Observability: read-only bus tail / audit feed for debugging.
>
> **Roadmap:** Phase 2 of `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md`. Phase 2 is half-complete: #39 closed (PR #48). This is the second half.

## Problem

From outside any single endpoint, the bus is a black box. Every endpoint can `list_pending` only its own queue; nobody can answer "the user message went to the bridge — did the bridge actually publish the inbound to the agent?" without grepping logs across multiple files and instrumenting per-endpoint.

The bus's SQLite envelopes table already retains every envelope across every state transition (`pending` → `in_flight` → `acked` / `dead_letter` / `expired`), so the data is there. The gap is a read-only surface that reads it.

## Out of scope

- **Streaming MCP responses.** Polling with `since=<last_created_at>` covers live-tail. SSE-over-MCP would require async-generator tool support; not needed for v1.
- **Admin vs non-admin scope tiers.** Localhost-only binding + explicit-opt-in endpoint type is the trust boundary. No second tier.
- **Tunable yaml params.** Defaults are hardcoded; promote to yaml when a real need surfaces.
- **Deletion / purge / write tools.** Read-only endpoint by construction.
- **Auto-attach to agents on the bus.** This is operator tooling, not agent tooling — Pepper and the briefs framework should never see these tools on their MCP surface.

## Design

### One source of truth: the envelopes table

No new database, no parallel store, no separate JSONL. The bus's existing `Persistence` (SQLite, WAL mode) retains every envelope across every state transition until DLQ purge — that *is* the audit log. We add read-only query methods to `Persistence` and expose them as MCP tools.

This is fundamentally different from #39 (MCP tool-call audit JSONL), which logs an external call surface that the bus itself doesn't see. Bus envelopes are already persisted; we don't double-write.

### A separate MCP endpoint type, not auto-attached

A new endpoint type **`builtin.bus_tail_mcp`** that operators wire up explicitly in yaml. It's a separate FastMCP server (its own MCP tool surface, distinct from any agent's), but it mounts onto the bus's existing shared `HTTPHost` at its own path — e.g. `/mcp/bus-tail/`. Same uvicorn process, same loopback bind (`127.0.0.1:8788`), distinct path prefix. The MCP client connects to `http://127.0.0.1:8788/mcp/bus-tail/`.

Why this over a standalone uvicorn on a separate port:

- Same process, same lifecycle — no extra port-binding config, no second daemon-supervisor concern.
- Loopback-only enforcement is already centralized in `bus/runner.py` (`bind_host="127.0.0.1"`). Riding it gives that for free.
- Path-level isolation is just as strong as port-level for "won't accidentally end up on Pepper's tool surface" — each MCP client connects to its own mount and doesn't see other mounts.
- Matches how every `claude_code_mcp` endpoint coexists today.

Why an explicit endpoint type and not a per-agent flag:

- This is operator tooling, not agent tooling. The mental model "wire up a separate MCP server when I want to debug" matches reality better than "add a flag to one of my agents."
- Eliminates the failure mode "I accidentally enabled tail on Pepper and now she has visibility into her own bus traffic."
- The endpoint can be wired up only when needed (a dev console session) and torn down by removing the yaml entry.

### yaml configuration

```yaml
endpoints:
  - name: bus-tail
    type: builtin.bus_tail_mcp
    params:
      mount: /mcp/bus-tail   # default if omitted
```

That's the entire config surface. No port, no auth, no tunable limits in v1. The endpoint inherits the HTTPHost's loopback bind. Defaults are hardcoded:

| Default | Value | Rationale |
|---|---|---|
| `tail` default `limit` | 50 | Reasonable page size for human eyeballing. |
| `tail` max `limit` | 1000 | Cap response size; clamps oversized requests. |
| `metrics` window | last 24h | Most useful for "what's been happening today." |
| `trace_correlation` cap | 1000 envelopes | Defensive; correlation chains are normally short. |

### Persistence wiring (via BusHandle, on endpoint start)

`Persistence` is owned by the `Bus` itself (created lazily in `Bus.start()` from the configured `storage_path`). It's not a runtime-injected service like the audit writer — it's bus-internal state. Going through `RunnerServices` would be awkward; the cleaner pattern is the existing endpoint lifecycle.

- **New `BusHandle.persistence()` accessor** in `bus/handle.py` — returns `Bus._store`. The store is guaranteed non-None at endpoint-start time because `Bus.start()` creates it *before* calling `endpoint.start(handle)` for each endpoint.
- **`BusTailMCPEndpoint.start(bus: BusHandle)`** grabs the store via `bus.persistence()` and constructs its `PersistenceReader` once. Tool implementations use the reader.

No `Protocol`, no `RunnerServices` field, no plugin-manager hook — persistence flows through the channel that already exists for endpoints. This contrasts with #39's audit-writer pattern (which *was* runtime-injected, hence Protocol + plugin manager), but matches the actual ownership: persistence is bus-internal.

Endpoint construction works without persistence (cheap unit tests construct the endpoint directly and inject a fake reader). Calls to tool implementations before `start()` raise a clear "not started" error.

### Tool surface (four read-only tools)

```python
# tail() — recent envelope listing with metadata + schema-summary previews.
# Polling model: callers poll with since=<last_created_at> for "live tail."
tail(
    limit: int = 50,
    since: datetime | None = None,            # created_at >= since
    before: datetime | None = None,           # created_at < before (back-pagination)
    from_endpoint: str | None = None,
    to_endpoint: str | None = None,
    kind: str | None = None,
    urgency: Literal["green","yellow","red"] | None = None,
    state: Literal["pending","in_flight","acked","dead_letter","expired"] | None = None,
) -> list[EnvelopeSummary]   # newest first

# get_envelope() — full payload, full row.
get_envelope(id: str) -> EnvelopeFull | None

# trace_correlation() — full chain by correlation_id, oldest first.
trace_correlation(correlation_id: str) -> list[EnvelopeFull]

# metrics() — aggregates over last 24h (hardcoded window for v1).
metrics() -> MetricsSnapshot
```

`limit` is clamped to `[1, 1000]`. `since` and `before` are inclusive/exclusive respectively to make pagination semantics unambiguous (poll with `since=last_seen_created_at + 1us`, or simpler: track the largest `created_at` you've seen and pass it as `since` next call — duplicates are fine because envelope ids are unique).

### Return shapes

`EnvelopeSummary` (returned by `tail`):

```python
{
  "id": str,
  "correlation_id": str,
  "in_reply_to": str | None,
  "from": str,
  "to": str,
  "kind": str,
  "urgency": "green" | "yellow" | "red",
  "state": "pending" | "in_flight" | "acked" | "dead_letter" | "expired",
  "created_at": str,           # ISO-8601 UTC
  "expires_at": str | None,
  "delivery_count": int,
  "last_attempted": str | None,
  "payload_summary": dict,     # kind-aware, value-free shape
  "metadata_keys": list[str],  # keys only, no values
}
```

`EnvelopeFull` (returned by `get_envelope` and `trace_correlation`): same as `EnvelopeSummary` but adds `payload: dict` (full payload, deserialized) and `metadata: dict` (full metadata, not just keys).

`MetricsSnapshot` (returned by `metrics`):

```python
{
  "window": "last_24h",
  "total_envelopes": int,
  "counts_by_kind": {"TextMessage": 87, "Event": 42, ...},
  "counts_by_state": {"pending": 5, "acked": 121, "dead_letter": 2, ...},
  "queue_depth_by_endpoint": {"pepper": 0, "discord-bridge": 3, ...},  # pending only
  "ack_latency_ms": {"p50": 120, "p95": 880, "p99": 2400} | None,
}
```

`ack_latency_ms` is `null` when fewer than 10 acked envelopes exist in the window — percentiles aren't meaningful below that threshold. Latency is computed as `(last_attempted_when_acked - created_at)`, available via the existing schema.

`queue_depth_by_endpoint` counts only `state='pending'` rows — these are the ones actually waiting to be picked up.

### Schema-summary registry

One module `bus_tail/summaries.py`. One function per envelope kind, returning a value-free shape:

```python
def summarize_text_message(p: TextMessagePayload) -> dict:
    return {"text_length": len(p.text), "attachment_count": len(p.attachments)}

def summarize_tool_invocation(p: ToolInvocationPayload) -> dict:
    return {"tool": p.tool, "arg_count": len(p.args), "arg_keys": sorted(p.args.keys())}

def summarize_event(p: EventPayload) -> dict:
    return {"type": p.type, "schema_version": p.schema_version,
            "data_keys": sorted(p.data.keys())}

def summarize_cancellation(p: CancellationPayload) -> dict:
    return {"has_reason": p.reason is not None}

def summarize_progress(p: ProgressPayload) -> dict:
    return {"status": p.status, "has_note": p.note is not None,
            "has_percent": p.percent is not None}

def summarize_acknowledgment(p: AcknowledgmentPayload) -> dict:
    return {"of": p.of, "has_note": p.note is not None}

SUMMARIZERS: dict[str, Callable[[Any], dict]] = {
    "TextMessage": summarize_text_message,
    "Event": summarize_event,
    "ToolInvocation": summarize_tool_invocation,
    "Cancellation": summarize_cancellation,
    "Progress": summarize_progress,
    "Acknowledgment": summarize_acknowledgment,
}
```

The `tool` name in `ToolInvocation` and the `of` reference in `Acknowledgment` are structural identifiers (not user content), so they're safe to surface verbatim. Same call for `type` and `status` — closed enums or namespaces, not free-form text.

If a future kind ships without a registered summarizer, `tail()` returns `{"warning": "no summarizer for kind=X"}` instead of either crashing or leaking the full payload.

### Persistence reader

A new module `bus_tail/reader.py` defines `PersistenceReader`, a read-only wrapper around the existing `Persistence`. The reader holds a reference to the same `Persistence` instance — no second connection, no parallel cache, no writes:

```python
class PersistenceReader:
    """Read-only query layer for the audit/tail surface."""
    def __init__(self, persistence: Persistence): ...
    async def tail(self, *, limit, since, before, ...) -> list[Envelope]: ...
    async def get_envelope(self, id_: str) -> Envelope | None: ...      # delegates to persistence.get
    async def list_by_correlation(self, cid: str) -> list[Envelope]: ...  # delegates to existing method
    async def metrics_snapshot(self, *, window_hours: int = 24) -> dict: ...
```

Why a separate module instead of adding methods directly to `Persistence`: the bus's hot-path `Persistence` API is small and focused (insert + state transitions + the queries the bus itself needs). Tail-specific queries are a different audience (debugging) and a different shape (broad filters, aggregations). Keeping them in a separate reader keeps both modules legible.

`tail`'s SQL builds dynamically from non-`None` filter args; the existing indices on `(to_endpoint, state, created_at)` and `correlation_id` cover the hot paths.

### Files touched

- `packages/core/src/agent_core/bus/handle.py` — add public `persistence()` accessor.
- `packages/core/src/agent_core/bus/runner.py` — register `builtin.bus_tail_mcp` endpoint type via a new built-in plugin entry (mirroring how `claude_code_mcp` is registered).
- `packages/core/src/agent_core/bus_tail/__init__.py` — package init, re-exports.
- `packages/core/src/agent_core/bus_tail/endpoint.py` — `BusTailMCPEndpoint` with FastMCP server, `mount`, `asgi_app()`, `start`, `deliver` (no-op since nothing is addressed to it), `stop`, four tool registrations.
- `packages/core/src/agent_core/bus_tail/summaries.py` — six summarizers + `SUMMARIZERS` registry.
- `packages/core/src/agent_core/bus_tail/reader.py` — `PersistenceReader` wrapper around `Persistence`.
- `packages/core/tests/test_bus_tail_summaries.py` — per-kind summary correctness, no value leakage.
- `packages/core/tests/test_persistence_reader.py` — filter dimensions, ordering, limit clamping, metrics window correctness.
- `packages/core/tests/test_bus_tail_endpoint.py` — construction, BusHandle-driven persistence wiring, mount path attribute, ASGI app contract.
- `packages/core/tests/test_bus_tail_mcp.py` — each tool returns expected shape, missing-id returns `None`, value-free summaries.
- `packages/core/tests/test_bus_tail_runner.py` — runner constructs from yaml, registers, mounts, end-to-end via FastMCP test client.

No schema changes. No new dependencies. No yaml-format changes for existing endpoints.

## Tests

### Schema summaries (`test_bus_tail_summaries.py`)

- `test_summarize_text_message_returns_shape_only` — assert keys `{text_length, attachment_count}`; no `text` value present.
- `test_summarize_tool_invocation_includes_tool_name_and_keys` — `tool` and `arg_keys` present; values absent.
- `test_summarize_event_includes_type_version_keys` — `type`, `schema_version`, `data_keys` present; data values absent.
- `test_summarize_cancellation_progress_acknowledgment_shapes` — bundled in one test; assert each shape's keys.
- `test_unknown_kind_returns_warning_not_payload` — when registry lookup fails, returns `{"warning": ...}`.

### Persistence reader (`test_persistence_reader.py`)

- `test_tail_returns_newest_first` — insert 5 envelopes with different `created_at`, assert ordering.
- `test_tail_filter_by_each_dimension` — one assert per filter (from, to, kind, urgency, state).
- `test_tail_since_is_inclusive` — envelope with `created_at == since` is included.
- `test_tail_before_is_exclusive` — envelope with `created_at == before` is excluded.
- `test_tail_limit_clamps_to_max` — request `limit=5000`, get 1000.
- `test_tail_limit_clamps_to_min` — request `limit=0`, get 1.
- `test_get_envelope_returns_full_envelope_or_none` — happy path + missing-id.
- `test_metrics_snapshot_counts_by_kind_and_state` — known-shape DB → known-shape metrics dict.
- `test_metrics_window_excludes_old_envelopes` — envelope older than 24h is excluded from counts and queue depth.
- `test_metrics_ack_latency_null_below_sample_threshold` — fewer than 10 acked → `ack_latency_ms is None`.
- `test_metrics_ack_latency_percentiles_above_sample_threshold` — 50 acked envelopes with known latencies → p50/p95/p99 within tolerance.

### Endpoint construction (`test_bus_tail_endpoint.py`)

- `test_endpoint_constructs_without_persistence` — `BusTailMCPEndpoint(name=..., mount=...)` succeeds; calling tools before `start` raises a clear "not started" error.
- `test_start_attaches_reader_via_bus_handle` — after `start(bus_handle)`, tools work end-to-end (reader resolved from `bus.persistence()`).
- `test_deliver_is_noop` — calling `deliver(envelope)` doesn't crash and acks immediately (nothing should be addressed to bus-tail in normal operation, but Protocol requires the method).
- `test_mount_attribute_exposed` — `endpoint.mount` returns the configured path.
- `test_asgi_app_returns_fastmcp_http_app` — sanity that the MCPHostable contract works.

### Tool surface (`test_bus_tail_mcp.py`)

- `test_tail_tool_returns_envelope_summaries` — full round-trip via FastMCP in-memory client.
- `test_tail_tool_summaries_are_value_free` — assert payload values absent from responses for each kind.
- `test_get_envelope_tool_returns_full_payload` — `EnvelopeFull` shape.
- `test_get_envelope_tool_returns_null_for_missing_id` — non-existent id → `None`.
- `test_trace_correlation_orders_oldest_first` — three envelopes in a chain → returned in `created_at` ascending order.
- `test_trace_correlation_handles_unknown_correlation_id` — returns `[]`.
- `test_metrics_tool_shape_and_window` — top-level keys, window field is `"last_24h"`.

### Runner integration (`test_bus_tail_runner.py`)

- `test_runner_registers_bus_tail_mcp_from_yaml` — yaml entry → endpoint constructed and registered on the bus.
- `test_runner_mounts_bus_tail_on_http_host` — after boot, HTTPHost has the bus-tail mount.
- `test_runner_default_yaml_omits_bus_tail` — sanity: no entry in yaml means no endpoint registered.
- `test_bus_handle_persistence_returns_store_after_bus_start` — sanity that the wiring channel works end-to-end (start bus → endpoint receives BusHandle → `bus.persistence()` returns the live store).

## Acceptance criteria

1. yaml `endpoints` block with `type: builtin.bus_tail_mcp` constructs a working endpoint, mounted at `/mcp/bus-tail/` (or custom path) on the bus's HTTPHost.
2. Pepper's MCP and the briefs framework's MCP do **not** see `tail`, `get_envelope`, `trace_correlation`, or `metrics` on their tool surface (only the dedicated bus-tail mount sees them).
3. `tail()` filters work for every dimension, returns newest-first, clamps `limit` to `[1, 1000]`.
4. `tail()` payload summaries leak no values for any of the six envelope kinds.
5. `get_envelope(id)` returns full payload + metadata; missing id returns `None`.
6. `trace_correlation(cid)` returns the chain in oldest-first order.
7. `metrics()` returns counts, queue depth, and ack-latency percentiles when the sample threshold is met (`null` otherwise).
8. All existing bus and HTTPHost tests pass; the new test files all pass.
9. The existing `Persistence` connection is reused — no second SQLite connection opened.
10. Removing the yaml entry cleanly tears down the endpoint at next daemon restart (no orphaned mounts, no errors).

## Branch

`feat/issue-16-bus-tail-audit-feed`

## Risks

- **Querying the live SQLite under load.** The bus's hot path is on the same connection. Tail queries with broad filters could compete with envelope inserts. Mitigation: WAL mode is already in use (readers don't block writers); indices on `(to_endpoint, state, created_at)` and `correlation_id` cover the hot paths; `limit` is clamped. Worst case is a slow tail query, not a stalled bus.
- **Schema-summary drift.** When a new envelope `kind` is added, summarizers must be added too. Mitigation: the registry returns a `{"warning": ...}` shape for unknown kinds, and a unit test asserts that every `EnvelopePayload` discriminator value has a registered summarizer (caught at test time, not runtime).
- **Trace-chain explosion.** A pathological correlation chain (thousands of envelopes) would return a large response. Mitigation: hard cap at 1000 envelopes per `trace_correlation` call.
- **`BusHandle` API surface growth.** Adding `persistence()` to `BusHandle` exposes the bus's internal store to every endpoint, not just bus-tail. Mitigation: it's a getter, not a setter; misuse looks like an endpoint mutating envelopes outside the BusHandle's identity-stamping flow, which is already discoverable in code review. Worth the simplicity over a Protocol-plus-RunnerServices alternative for what amounts to "bus-tail wants to read its own bus's store."
- **Path-collision with existing mounts.** If a user names a `claude_code_mcp` endpoint `bus-tail`, both want `/mcp/bus-tail/`. Mitigation: the HTTPHost already errors on duplicate mounts, so the failure is loud at boot, not silent at runtime.
