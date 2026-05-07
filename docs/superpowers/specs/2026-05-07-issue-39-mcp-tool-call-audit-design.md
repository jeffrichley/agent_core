# Issue #39 — Bus HTTP MCP host: log every `tools/call` invocation (Design)

> **Status:** Approved 2026-05-07. Ready for implementation plan.
>
> **Issue:** [#39](https://github.com/jeffrichley/agent_core/issues/39) — Bus HTTP MCP host: log every `tools/call` invocation to a daemon-wide audit JSONL.
>
> **Roadmap:** Phase 2 of `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md` (observability foundation). #16 (read-only bus tail / audit feed) builds on this.

## Problem

When an agent invokes an MCP tool exposed by the bus (e.g., `mcp__pepper__send_discord_message`, `mcp__pepper__compose_brief`, scheduler tools, webcam tools), the call is invisible to operators. Nothing logs which tool was called, when, by which session, with what arg shape, or whether it succeeded.

The bus's existing `daily_raw_jsonl` hook (`packages/core/src/agent_core/bus_hooks/daily_raw_jsonl.py`) captures bus envelopes only. MCP `tools/call` flows through a separate path — FastMCP middleware on each `ClaudeCodeMCPEndpoint._mcp` server, mounted on the shared HTTP host (`packages/core/src/agent_core/bus/http_host.py`). Tool calls never reach the envelope pipeline.

Surfaced 2026-05-06 during the Pepper webcam endpoint design. Webcam ships its own per-endpoint audit log because the privacy concern was high enough to not wait, but the broader gap applies to every MCP tool. Any feature that wants audit visibility today has to build its own logger; doing so produces schema drift across endpoints.

## Out of scope

- **Logging tool *responses*** — payload (text content of a brief, PNG bytes of a frame) ballooning the audit log defeats its purpose. The audit logs THAT something happened and at what shape, not WHAT the result was.
- **Real-time streaming of audit events** to a SIEM. JSONL on disk; operators tail it.
- **Per-agent ACL on who can read the audit log** — file-system permissions are sufficient for v1.
- **Tool-call sampling / rate-limiting based on audit data** — instrumentation only; policy comes later.
- **Migrating webcam's local audit log onto this mechanism.** Webcam audit is a feature unto itself, with finer-grained domain data (saved file path, filesize, camera name) that the generic schema does not carry. Both logs coexist.
- **Per-tool bespoke `args_summary` shapes.** v1 ships a default-only summarizer that emits `{arg_keys: [...], arg_count: N}`. Adding per-tool summarizers is a follow-up if a specific tool surfaces a need.

## Design

### Architecture

```
┌────────────────────────────────────────────────────┐
│ runner.py                                          │
│   reads `mcp_audit:` from agent_core.yaml          │
│   if enabled: constructs MCPAuditWriter            │
│   threads via RunnerServices.mcp_audit_writer      │
└────────────────────────────────────────────────────┘
                       │
                       ▼ during configure_endpoint_instance
┌────────────────────────────────────────────────────┐
│ ClaudeCodeMCPEndpoint                              │
│   if services.mcp_audit_writer is not None:       │
│     self._mcp.add_middleware(                      │
│       MCPAuditMiddleware(                          │
│         endpoint_name=self.name,                   │
│         writer=services.mcp_audit_writer,          │
│         skip_tools=services.mcp_audit_skip_tools)) │
└────────────────────────────────────────────────────┘
                       │
                       ▼ on every tools/call
┌────────────────────────────────────────────────────┐
│ MCPAuditMiddleware.on_call_tool                    │
│   start = perf_counter()                           │
│   try: result = await call_next(context)           │
│       result_status = "ok"                         │
│   except Exception as exc:                         │
│       result_status, error = "error", exc          │
│       raise                                        │
│   finally:                                         │
│       writer.write(AuditLine(...))                 │
└────────────────────────────────────────────────────┘
                       │
                       ▼ asyncio.to_thread, asyncio.Lock
┌────────────────────────────────────────────────────┐
│ MCPAuditWriter (singleton)                         │
│   path = log_root / f"{local_date}.jsonl"          │
│   one JSON line, append-only                       │
│   swallows OSError + Exception, logs at WARNING    │
└────────────────────────────────────────────────────┘
```

### New files

- `packages/core/src/agent_core/mcp_audit/__init__.py` — public surface (`AuditLine`, `MCPAuditMiddleware`, `MCPAuditWriter`).
- `packages/core/src/agent_core/mcp_audit/writer.py` — `MCPAuditWriter` + `AuditLine` dataclass + `daily_path()` helper.
- `packages/core/src/agent_core/mcp_audit/middleware.py` — `MCPAuditMiddleware`.

### Modified files

- `packages/core/src/agent_core/plugins/specs.py` — extend `RunnerServices` with `mcp_audit_writer: MCPAuditWriter | None` and `mcp_audit_skip_tools: frozenset[str]`.
- `packages/core/src/agent_core/bus/runner.py` — read `mcp_audit:` block; construct writer when enabled; populate services.
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — accept the writer post-construction via the existing `configure_endpoint_instance` plugin hook (no constructor signature change). Mirrors how `notify_broker` is already attached via `attach_notify_broker`. The endpoint exposes a small `attach_audit_writer(writer, skip_tools)` method that registers `MCPAuditMiddleware` on `self._mcp`.

Why post-construction attach instead of a constructor kwarg: keeps the ctor signature stable so tests that construct `ClaudeCodeMCPEndpoint` directly (without going through the runner) continue to work without audit. Matches the existing `notify_broker` pattern.

### Schema (one JSONL line per `tools/call`)

```jsonl
{"timestamp": "2026-05-07T14:23:07.481-04:00", "endpoint": "pepper", "session_id": "abc...", "request_id": "42", "tool": "send_discord_message", "args_summary": {"arg_keys": ["channel_id", "files", "text"], "arg_count": 3}, "duration_ms": 87, "result": "ok"}
{"timestamp": "2026-05-07T14:24:01.220-04:00", "endpoint": "pepper", "session_id": "abc...", "request_id": "44", "tool": "capture_webcam_frame", "args_summary": {"arg_keys": ["camera_index", "save"], "arg_count": 2}, "duration_ms": 312, "result": "error", "error": {"type": "CameraBusyError", "message": "camera busy"}}
```

Field provenance:

| Field | Source |
|---|---|
| `timestamp` | `datetime.now(UTC).astimezone(ZoneInfo(timezone))` — ISO 8601 with offset. |
| `endpoint` | `ClaudeCodeMCPEndpoint.name`, baked into the middleware at attach time. |
| `session_id` | `context.fastmcp_context.session_id` (`mcp-session-id` HTTP header). `null` for in-memory transports. |
| `request_id` | JSON-RPC request id, via `context.fastmcp_context.request_context.request_id`. |
| `tool` | `context.message.name` (`CallToolRequestParams.name`). |
| `args_summary` | Default summarizer over `context.message.arguments`: `{arg_keys: sorted(args.keys()), arg_count: len(args)}`. Sorted keys produce deterministic output for `grep | sort | uniq -c` analyses. No values, ever. |
| `duration_ms` | `int((perf_counter_end - perf_counter_start) * 1000)`. |
| `result` | `"ok"` on normal return; `"error"` if `call_next` raised. |
| `error` | Present only when `result == "error"`: `{type: type(exc).__name__, message: str(exc)}`. |

### Configuration

Top-level `mcp_audit:` block in `agent_core.yaml`:

```yaml
mcp_audit:
  enabled: true                          # default: true
  log_root: "~/.agent-core/bus/mcp-audit"  # default
  timezone: "US/Eastern"                 # default; matches daily_raw_jsonl
  skip_tools: []                         # tool names to never audit (e.g., "list_pending")
```

Defaults & precedence:

- Block missing entirely → audit enabled with all defaults. (Symmetric with the existing convention: missing config means "run with defaults", not "off".)
- `enabled: false` → runner constructs no writer, `services.mcp_audit_writer` stays `None`, no middleware ever attached. Zero per-call overhead.
- `skip_tools` is a list of bare tool names; middleware checks `context.message.name in skip_set` and short-circuits to `await call_next(context)` without any timing or write.

Built-in bus tools (`send`, `list_endpoints`, `describe_endpoint`, `list_pending`, `handle`, `ack`, `nack`, `show_my_day`) are audited by default. The issue title is literally "log every `tools/call` invocation"; defaulting to skip several of them silently would violate the documented expectation. Operators who find `list_pending` polling noisy can add it to `skip_tools` in seconds.

### Storage

- Path: `<log_root>/<YYYY-MM-DD>.jsonl`. Co-located with `~/.agent-core/bus/raw/` (envelope log). `~/.agent-core/bus/` becomes the canonical home for daemon-owned daily JSONLs of different views — envelope log and MCP audit are siblings.
- Date rollover: local midnight in the configured `timezone`. A 23:50 ET call lands in today's file; 00:10 ET goes to tomorrow's. Same convention as `daily_raw_jsonl` so operators carry one mental model.
- Append-only, never rewritten. No size-based rotation in v1 — daily rotation is the only rotation.
- Writer creates the directory on first call (`path.parent.mkdir(parents=True, exist_ok=True)`).
- Sync write inside `asyncio.to_thread`, guarded by an `asyncio.Lock`. The lock guards file-handle ordering so two concurrent calls don't interleave bytes; the actual disk I/O happens off-loop.

### Error handling

**Audit-write failures.** `MCPAuditWriter.write()` swallows `OSError` and any unexpected `Exception`, logs at WARNING via `logging.getLogger(__name__)`. Never raises into the caller. Tool-call result is unaffected by audit failure. Same pattern as webcam audit and `daily_raw_jsonl`.

**Tool-call exceptions.** Middleware wraps `call_next(context)` in `try`/`except`/`finally`. The exception is bound, the audit line is written in `finally:` (whether the call succeeded or raised), and the exception is always re-raised. Middleware never silences a real tool error from reaching the client.

**`session_id` is None.** In-memory FastMCP transports (used in tests) do not have a session id. Field is serialized as JSON `null`.

**Disabled.** When `enabled: false`, no writer is constructed and no middleware is attached. Tests can assert the audit directory is never created.

### Coexistence with webcam local audit

Webcam's per-endpoint audit log (`~/.agent-core/webcam/<endpoint>/audit.jsonl`) is unchanged. Both logs coexist:

- Daemon audit records THAT `capture_webcam_frame` was called, with structural args and timing.
- Webcam audit records the rich domain detail — saved file path, filesize, camera name — that the generic schema cannot carry.

Two writes per capture is cheap. The schemas serve different operators (security audit vs domain forensics). Domain-specific endpoints (briefs, scheduler) may follow the same pattern in the future without affecting the daemon-wide audit.

## Tests

**From the issue (verbatim):**

1. `test_audit_writes_one_jsonl_line_per_tool_call` — happy path, assert one line per call with all baseline fields.
2. `test_audit_writes_error_line_with_structured_error_type_and_message` — exception path produces `result: "error"` plus `error.type` and `error.message`.
3. `test_audit_default_summary_emits_arg_keys_and_arg_count` — args_summary contains `arg_keys` (sorted) and `arg_count`; no arg values appear.
4. `test_audit_skip_tools_excludes_named_tools` — tool in `skip_tools` produces no audit line.
5. `test_audit_disabled_emits_nothing` — `enabled: false` → no writer constructed, audit dir never created.
6. `test_audit_daily_rotation_respects_timezone` — pre-midnight ET writes to today; post-midnight ET writes to tomorrow.

**Added by this design's specific decisions:**

7. `test_audit_includes_request_id_and_session_id` — both fields are populated when available.
8. `test_audit_re_raises_tool_exception_after_writing` — exception propagates; audit line is written before re-raise.
9. `test_audit_swallows_write_failure_does_not_break_tool_call` — `OSError` from disk does not surface to caller.
10. `test_audit_writer_is_singleton_across_endpoints` — one writer, two endpoints, both write to same daily file.
11. `test_audit_lines_under_concurrent_calls_are_atomic` — N concurrent calls produce N intact lines (no interleaved bytes).
12. `test_runner_attaches_writer_when_mcp_audit_enabled` — config-to-services plumbing.
13. `test_runner_skips_writer_when_mcp_audit_disabled` — `enabled: false` end-to-end.

Test style matches existing `test_http_host.py` and `test_claude_code_mcp.py`: `tmp_path` for log_root, in-memory FastMCP transport for middleware tests, real `MCPAuditWriter` instances (not mocked) for end-to-end behavior.

## Branch

`feat/issue-39-mcp-tool-call-audit`

## Related

- Issue #16 — read-only bus tail / audit feed for cross-endpoint debugging. Builds on this issue's persistence patterns.
- `packages/core/src/agent_core/bus_hooks/daily_raw_jsonl.py` — envelope-side audit; this issue mirrors its rotation/timezone conventions.
- `packages/core/src/agent_core/bus/http_host.py` — the HTTP host this audit instruments (via FastMCP middleware on each mounted endpoint's `_mcp` server, not via the host itself).
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — the endpoint class that gets the audit middleware attached.
- `packages/agent-core-webcam/src/agent_core_webcam/audit.py` — the per-endpoint audit log that motivated the daemon-wide pattern. Coexists; not migrated.
