# Spec: structured JSON logging + correlation-id contextvar + JSON/pretty toggle (issue #385)

## Goal

Add a JSON log formatter and a `correlation_id` contextvar to `agent_core.logging` so that every log line emitted during envelope dispatch carries the envelope's correlation id as a queryable structured field. A config toggle selects the JSON handler (prod) or the existing human-readable handler (dev). No new dependencies are required; implementation uses stdlib `logging`, `contextvars`, and `json`. See the parent design spec `docs/superpowers/specs/2026-07-15-observability-design.md` §3 and issue #385.

---

## Acceptance criteria

- With the JSON handler enabled (`logging.format: json` in `agent_core.yaml`), every log record emitted during an envelope's `_dispatch()` is valid JSON on stdout/stderr carrying a `correlation_id` field equal to the envelope's `correlation_id`.
- The `correlation_id` contextvar is reset between handlings: a second dispatch sees only its own id, not a leftover from a prior dispatch.
- With the pretty handler enabled (`logging.format: pretty`, the default), log output is the same human-readable `%(asctime)s %(levelname)s %(name)s: %(message)s` text it is today.
- All existing call sites (`log.info(...)`, `log.warning(...)`, etc.) in `bus/core.py`, `bus/runner.py`, `bus/handle.py`, and other modules keep working unchanged.
- A new `LoggingConfig` Pydantic model in `bus/config.py` validates the `logging.format` field; an unknown value raises a `pydantic.ValidationError` at boot, not a silent fallback.
- `DaemonConfig` accepts the new optional `logging:` section; omitting it produces the `pretty` default (backward-compatible with all existing `agent_core.yaml` files).
- `just test-fast` passes after all changes; patch coverage for the new `agent_core/logging.py` module and the modified lines in `bus/config.py`, `bus/cli.py`, and `bus/core.py` meets the 80% gate.

---

## Approach

No GoF pattern fits cleanly here — this is straightforward stdlib wiring. The three moving parts are:

1. **`logging.Filter` injects the contextvar**: Python's `logging.Filter.filter()` receives a `LogRecord` before it reaches the formatter. A `CorrelationIdFilter` reads `correlation_id.get()` and stamps `record.correlation_id` on every record that passes through. This is the canonical Python idiom for injecting per-request context into logs without modifying call sites.

2. **`logging.Formatter` serialises to JSON**: A `JsonFormatter` subclass overrides `format()` to build a `dict` from the record's standard fields plus `record.correlation_id`, then calls `json.dumps()`. No third-party JSON logging library is needed; `json.dumps` is fast enough for this use-case and keeps the dependency count flat.

3. **`contextvars.ContextVar` propagates across awaits**: Python's `asyncio` copies the current `Context` when spawning tasks, so a contextvar set on the dispatch coroutine is visible to all awaited callees (endpoint's `deliver()`, pre_deliver hooks) without being passed explicitly. The `_dispatch()` method in `bus/core.py` sets the contextvar at the top and resets it via `Token` in a `finally` block, ensuring isolation between consecutive dispatches running in the same event-loop task (e.g., the redelivery sweep dispatching N envelopes sequentially).

**Config wiring**: A new `LoggingConfig` model with `format: Literal["json", "pretty"] = "pretty"` is added to `bus/config.py` and wired into `DaemonConfig`. In `bus/cli.py:run()`, before `asyncio.run(_run_bus(...))`, the YAML file is read with a lightweight `yaml.safe_load` + `.get()` to extract only the `logging.format` key (no full Pydantic validation yet). The extracted value is passed to `configure_logging()`. Full validation still happens inside `build_bus_from_config`, so an invalid `format` value produces a clean `pydantic.ValidationError` at boot. This early read ensures all log output — including lines from `build_bus_from_config` itself — uses the correct format.

**Backward compatibility**: `logging.basicConfig(...)` is removed from `run()` and replaced by `configure_logging(mode)`. The pretty format string is identical to today's `basicConfig` format, so existing `daemon.log` parsers see no change when `format: pretty` (or when the section is absent, which defaults to pretty).

---

## Sub-requests (topologically sorted)

1. **Create `packages/core/src/agent_core/logging.py`** — new module with:
   - `correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")`
   - `bind_correlation_id(value: str) -> Token[str]` — thin wrapper that calls `correlation_id.set(value)`
   - `CorrelationIdFilter(logging.Filter)` — `filter()` stamps `record.correlation_id = correlation_id.get()` and returns `True`
   - `JsonFormatter(logging.Formatter)` — `format()` builds `{"timestamp", "level", "logger", "message", "correlation_id"}` dict and returns `json.dumps(...)`. When `record.exc_info` is truthy, adds `"exc_info": self.formatException(record.exc_info)`.
   - `configure_logging(mode: Literal["json", "pretty"]) -> None` — clears all existing root-logger handlers, adds a `logging.StreamHandler` with `CorrelationIdFilter` and either `JsonFormatter` (json) or `logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")` (pretty), then sets root level to `INFO`.

2. **Add `LoggingConfig` to `packages/core/src/agent_core/bus/config.py`** — new Pydantic model:
   ```python
   class LoggingConfig(BaseModel):
       model_config = ConfigDict(extra="forbid")
       format: Literal["json", "pretty"] = "pretty"
   ```
   Add `from typing import Literal` to imports (already present via pydantic but make explicit). Add field to `DaemonConfig`:
   ```python
   logging: LoggingConfig = Field(default_factory=LoggingConfig)
   ```

3. **Update `packages/core/src/agent_core/bus/cli.py:run()`** — replace the `logging.basicConfig(...)` call with a lightweight YAML read + `configure_logging()`:
   ```python
   import yaml as _yaml
   from agent_core.logging import configure_logging
   
   @app.command()
   def run(config: Path = _RUN_CONFIG_OPTION) -> None:
       """Start the bus and all configured endpoints. Runs until SIGINT/SIGTERM."""
       try:
           _raw = _yaml.safe_load(config.read_text(encoding="utf-8")) or {}
       except Exception:
           _raw = {}
       _log_format = (_raw.get("logging") or {}).get("format", "pretty")
       if _log_format not in ("json", "pretty"):
           _log_format = "pretty"
       configure_logging(_log_format)
       try:
           asyncio.run(_run_bus(config))
       except BusBootError as exc:
           console.print(f"[red]boot error:[/red] {exc}")
           raise typer.Exit(code=1) from exc
   ```
   Remove the existing `import logging` at module level (it can stay if used elsewhere — `log = logging.getLogger(__name__)` at line 26 still needs it; keep it).

4. **Update `packages/core/src/agent_core/bus/core.py:Bus._dispatch()`** — bind the contextvar at the top of `_dispatch()`, reset in `finally`:
   ```python
   from agent_core.logging import bind_correlation_id, correlation_id as _correlation_id_var
   
   async def _dispatch(self, envelope: Envelope) -> None:
       _tok = bind_correlation_id(envelope.correlation_id)
       try:
           # ... existing dispatch body unchanged ...
       finally:
           _correlation_id_var.reset(_tok)
   ```
   The `try/finally` wraps the entire existing body of `_dispatch()`. No changes to any `log.*()` call sites.

5. **Create `packages/core/tests/test_structured_logging.py`** — tests covering:
   - `JsonFormatter` outputs valid JSON with all five required fields.
   - `JsonFormatter` includes `exc_info` key when `record.exc_info` is set.
   - `CorrelationIdFilter` stamps `record.correlation_id` from the active contextvar.
   - `CorrelationIdFilter` stamps empty string when no id is bound.
   - `bind_correlation_id` + `Token.reset()` restores the previous value (isolation across handlings).
   - `configure_logging("json")` installs `JsonFormatter` on the root logger.
   - `configure_logging("pretty")` installs a plain `logging.Formatter` (not `JsonFormatter`).
   - `CorrelationIdFilter` is attached to the handler in both modes.
   - End-to-end: a log emitted inside a `_dispatch()`-like wrapper (set id, emit, reset) appears in captured records with the correct `correlation_id` field when the JSON handler is active.
   - `LoggingConfig(format="unknown")` raises `pydantic.ValidationError` (not a silent fallback).
   - `DaemonConfig` instantiated without a `logging:` key yields `logging.format == "pretty"` (backward-compatible default).

---

## File-level changes

| File | Action | What changes |
|---|---|---|
| `packages/core/src/agent_core/logging.py` | **Create** | New module: `correlation_id` contextvar, `CorrelationIdFilter`, `JsonFormatter`, `configure_logging`, `bind_correlation_id` |
| `packages/core/src/agent_core/bus/config.py` | **Modify** | Add `LoggingConfig` model; add `logging: LoggingConfig` field to `DaemonConfig` |
| `packages/core/src/agent_core/bus/cli.py` | **Modify** | Replace `logging.basicConfig(...)` in `run()` with lightweight YAML read + `configure_logging()` |
| `packages/core/src/agent_core/bus/core.py` | **Modify** | Import `bind_correlation_id` / `correlation_id`; wrap `_dispatch()` body with contextvar set/reset |
| `packages/core/tests/test_structured_logging.py` | **Create** | Unit tests for all new logging module behaviour + integration with `_dispatch()` wrapper |

---

## Alternatives considered

1. **Third-party JSON logging library (`structlog`, `python-json-logger`)** — structlog in particular offers lazy rendering, processors, and a richer API. Ruled out: the issue explicitly states "no new dep"; stdlib `json.dumps` of a dict is sufficient for queryable log fields, and the codebase doesn't have structlog anywhere. Adding it would be the first third-party logging dep, which has upgrade / conflict implications for the prod venv.

2. **Inject `correlation_id` via a `logging.LoggerAdapter` rather than a `logging.Filter`** — `LoggerAdapter` wraps a specific logger and adds `extra` kwargs. Works at individual logger granularity. Ruled out: every `log = logging.getLogger(__name__)` across 27 files would need to be replaced with `LoggerAdapter`; the Filter approach requires zero changes to call sites, which is the explicit requirement ("Existing call sites keep working").

3. **Bind `correlation_id` in `_enqueue()` (publish path) rather than `_dispatch()` (deliver path)** — would give correlation context to pre_publish hook log lines too. Ruled out: the design doc specifies "bound at the start of envelope handling" and the test criterion is specifically about logs "during envelope handling" which is the dispatch/deliver path. `_enqueue()` is fan-out; the id would need to be reset after each recipient's `_dispatch()` anyway, so binding in `_dispatch()` is the correct granularity.

---

## Open questions

None. The stdlib APIs (`ContextVar`, `logging.Filter`, `logging.Formatter`) are stable and well-documented; the codebase conventions are clear from reading the existing modules.

---

## Out of scope

- Adding structured fields beyond `correlation_id` (e.g., `endpoint_name`, `envelope_id` as top-level JSON fields): the issue asks for correlation id propagation; additional fields can be added incrementally.
- Log rotation (Eα-4): a separate ticket per the design doc.
- `/healthz` and `/metrics` routes (Eα-1, Eα-2): separate tickets.
- Modifying `config_template.py` to emit a `logging:` section in the default scaffold: the default is `pretty`, which is backward-compatible; the scaffold can add it when a user explicitly sets JSON mode. Omitting the section from the template keeps the scaffold minimal.
- Changing the log format on any handler other than the bus `run` command's root handler: memory-compiler scripts, hook pipelines, and other CLI commands retain their own logging setup or inherit Python defaults.
