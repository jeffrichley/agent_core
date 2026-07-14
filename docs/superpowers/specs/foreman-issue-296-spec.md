# Spec: offload boundary — move VoiceEndpoint construction off-loop and add slow-deliver watchdog (issue #296)

## Goal

Fix the one active construction-time blocking defect (`VoiceEndpoint.__init__` builds `QwenTTSBackend` on the event loop) and add a slow-`deliver()` watchdog to the dispatch path. The architecture is approved at `docs/superpowers/specs/2026-07-14-offload-boundary-design.md`; this spec translates it into a concrete, file-level implementation plan for the Worker. See issue #296.

## Acceptance criteria

- `VoiceEndpoint.__init__` MUST NOT construct `QwenTTSBackend` or call `prepare_voice`. A spy-factory test asserts neither is called until `start()`.
- `VoiceEndpoint.start()` builds the backend off the event loop via `asyncio.to_thread(QwenTTSBackend, ...)` and warms voices the same way; a concurrent coroutine makes measurable progress during a blocking constructor, proving the loop is not stalled.
- `VoiceEndpoint.deliver()` raises `EndpointUnavailable` when `_backend is None` (before `start()` completes).
- `VoiceEndpoint.for_test(backend=fake, ...)` sets `_backend` at construction, so `start()` skips backend construction (but still warms voices); no model is loaded.
- `Bus._dispatch` times every `await endpoint.deliver()` call; when elapsed exceeds `BusConfig.slow_deliver_warn_seconds` (default 5.0, `> 0` = enabled, `<= 0` = disabled), a `WARNING`-level log line beginning with `"SlowDeliverWarning"` is emitted containing the endpoint name, envelope id, and elapsed seconds.
- Fast `deliver()` (completes under threshold) emits no `SlowDeliverWarning` log.
- `slow_deliver_warn_seconds <= 0` disables the watchdog entirely (no log, even for slow endpoints).
- `BusConfig.slow_deliver_warn_seconds` is read from the YAML key `bus.slow_deliver_warn_seconds` in the runner.
- `SlowDeliverWarning` is exported from `bus/protocol.py` as a frozen dataclass with fields `endpoint: str`, `envelope_id: str`, `elapsed_seconds: float`.
- `Endpoint.deliver()` docstring upgraded from advisory to **MUST** language matching the approved design.
- Existing `VoiceEndpoint.deliver()` tests and bus dispatch/ack tests pass unchanged.
- Three existing `test_endpoint.py` tests that assert `__init__`-era behaviour are updated to call `start()` first (they assert the same property, just after the correct lifecycle point).
- All new tests pass `just test-fast`; only the off-thread-proof test is marked `@pytest.mark.slow` (it uses `time.sleep` in a thread).

## Approach

No GoF pattern is needed here; this is a straightforward **SRP** migration: construction belongs in `start()` (which is already `async` and awaited at boot), and observability belongs in the dispatch path. Both are mechanical changes.

### ① `bus/protocol.py` — add `SlowDeliverWarning`, strengthen `Endpoint` docstrings

Add a `SlowDeliverWarning` frozen dataclass below `EndpointUnavailable`:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class SlowDeliverWarning:
    """Emitted (via structured log) when deliver() exceeds slow_deliver_warn_seconds.

    Warn-only — no delivery semantics are altered. Provides observability
    for Theme E and future T4 escalation.
    """
    endpoint: str
    envelope_id: str
    elapsed_seconds: float
```

Strengthen the `Endpoint` protocol docstring to add:

```
Implementation contract (MUST):
- ``__init__`` MUST be cheap — no model loads, blocking I/O, or network.
  Heavy or slow setup belongs in ``start()``, which is async and awaited
  during boot.
- ``deliver()`` MUST return promptly. Long work MUST be offloaded to a
  tracked background task via ``bus.spawn(coro, name=...)``.  Blocking the
  event loop stalls delivery to every other endpoint; the watchdog in
  ``Bus._dispatch`` emits ``SlowDeliverWarning`` when the threshold is
  exceeded.
```

Add `SlowDeliverWarning` to `bus/protocol.py`'s `__all__` (or the module-level import list in `bus/__init__.py` if one exists — check first).

### ② `bus/core.py` — add watchdog config field + timing in `_dispatch`

**Add to imports:**
```python
import time
```

**Add to `bus/protocol.py` import at the top of `core.py`** (line 26 area, already reads `BusHook, Endpoint`):
```python
from agent_core.bus.protocol import BusHook, Endpoint, SlowDeliverWarning
```

**Add field to `BusConfig`:**
```python
@dataclass
class BusConfig:
    ...
    # Watchdog: warn when deliver() takes longer than this many seconds.
    # Non-positive value disables the watchdog entirely.
    slow_deliver_warn_seconds: float = 5.0
```

**Modify `Bus._dispatch`** — wrap the `await endpoint.deliver(envelope)` with a timer and `finally` clause (lines 390–433 in the read above):

```python
import time as _time  # already imported at module level after ① above

t0 = _time.monotonic()
try:
    await endpoint.deliver(envelope)
except Exception as exc:
    # ... existing EndpointUnavailable / terminal handling (unchanged) ...
finally:
    elapsed = _time.monotonic() - t0
    warn_s = self.config.slow_deliver_warn_seconds
    if warn_s > 0 and elapsed >= warn_s:
        warning = SlowDeliverWarning(
            endpoint=envelope.to,
            envelope_id=envelope.id,
            elapsed_seconds=elapsed,
        )
        log.warning("%r threshold=%.1fs", warning, warn_s)
```

Note: instantiating `SlowDeliverWarning` in the log call is required so that the import is actually used.  A hardcoded format string with no reference to the class would trigger ruff F401 (unused import) and fail `just check`.

The `finally` block runs whether `deliver()` succeeds or raises, giving the watchdog full coverage. The existing exception handling inside `except` is **unchanged**.

### ③ `bus/runner.py` — plumb `slow_deliver_warn_seconds` from YAML + env

In `build_bus_from_config`, where `BusConfig(...)` is constructed (line 85 area), add:

```python
import os  # add at the top of runner.py if not already present

cfg = BusConfig(
    storage_path=storage_path,
    redelivery_timeout_seconds=bus_cfg_raw.get("redelivery_timeout_seconds", 300),
    ...
    slow_deliver_warn_seconds=float(
        os.environ.get(
            "BUS_SLOW_DELIVER_WARN_SECONDS",
            bus_cfg_raw.get("slow_deliver_warn_seconds", 5.0),
        )
    ),
    supervisor=supervisor,
)
```

This makes the knob available both in YAML (`bus.slow_deliver_warn_seconds`) and via the `BUS_SLOW_DELIVER_WARN_SECONDS` environment variable, matching the approved design's "env-overridable per the config-provenance chain" requirement. The env var takes precedence over YAML when set; YAML takes precedence over the default `5.0` when set.

### ④ `VoiceEndpoint` — defer construction to `start()`, guard `deliver()`

**`__init__`** changes:

1. Remove the `QwenTTSBackend(...)` construction block (lines 122–128).
2. Remove the `prepare_voice` warming loop (lines 149–151).
3. Store production params as instance attributes for lazy use in `start()`:
   - `self._model_path = model_path`
   - `self._device = device`
   - `self._attn_implementation = attn_implementation`
4. Keep the `ValueError` guard for `backend is None and model_path is None` in `__init__` — fail-fast on misconfiguration stays at construction time.
5. Set `self._backend: TTSBackend | None = backend` (may be `None` for production path).
6. Voice normalization (`self._voices`), `self._output_dir.mkdir(...)`, `self._audit` — all stay in `__init__` (unchanged).

After changes, `__init__` ends without ever touching a backend.

**`start(hook)`** changes:

Replace the current stub (`self._handle = bus; log.info(...)`) with:

```python
async def start(self, bus: BusHandle) -> None:
    self._handle = bus
    if self._backend is None:
        # Production path: build the GPU/CPU backend off the event loop thread.
        # asyncio.to_thread keeps the loop responsive during the (potentially
        # multi-second) model load.
        from madrigal.engine import QwenTTSBackend
        self._backend = await asyncio.to_thread(
            QwenTTSBackend,
            model_path=self._model_path,
            device=self._device,
            attn_implementation=self._attn_implementation,
        )
    # Warm all configured voices (no-op cost for fake backends; off-thread
    # for real backends so WAV encoding doesn't block the loop).
    for voice_id, info in self._voices.items():
        await asyncio.to_thread(
            self._backend.prepare_voice, voice_id, Path(info.ref_wav), info.ref_text
        )
        log.info("voice %r prepared (ref_wav=%s)", voice_id, info.ref_wav)
    log.info("VoiceEndpoint(name=%s) started; output_dir=%s", self._name, self._output_dir)
```

**`deliver()`** — add defensive guard at the very top, before any other logic:

```python
async def deliver(self, envelope: Envelope) -> None:
    if self._backend is None:
        # start() has not yet completed. This should not happen in production
        # (bus awaits start() before draining mail), but fail safe rather than crash.
        raise EndpointUnavailable(
            f"VoiceEndpoint(name={self._name!r}): backend not ready — start() not yet complete"
        )
    # ... rest of existing deliver() code unchanged ...
```

`EndpointUnavailable` is **not** currently imported in `endpoint.py`; sub-request 4 must add
`from agent_core.bus.protocol import EndpointUnavailable` to the top-level imports of that file.

**`for_test` class method**: No change needed. It passes `backend=...` to `__init__`, which sets `self._backend = backend` (non-None). When `start()` runs, `_backend is not None` skips construction and proceeds directly to voice warming. Tests stay fast.

## Sub-requests (topologically sorted)

1. **`bus/protocol.py`**: Add `SlowDeliverWarning` frozen dataclass (fields: `endpoint: str`, `envelope_id: str`, `elapsed_seconds: float`). Strengthen `Endpoint` docstring with MUST language for `__init__` (cheap) and `deliver()` (prompt, off-thread via `spawn()`). No signature changes.

2. **`bus/core.py`**: Add `import time` (stdlib). Add `SlowDeliverWarning` to the protocol import. Add `slow_deliver_warn_seconds: float = 5.0` field to `BusConfig`. Modify `Bus._dispatch` to wrap `await endpoint.deliver(envelope)` in a `t0 = time.monotonic()` / `try` / `finally` pattern; in the `finally` block, when the threshold is exceeded, instantiate `SlowDeliverWarning(endpoint=envelope.to, envelope_id=envelope.id, elapsed_seconds=elapsed)` and emit it via `log.warning("%r threshold=%.1fs", warning, warn_s)`. The `SlowDeliverWarning` import MUST be referenced via instantiation (not just a hardcoded string) to avoid ruff F401.

3. **`bus/runner.py`**: Add `import os` at the top if not already present. Add `slow_deliver_warn_seconds=float(os.environ.get("BUS_SLOW_DELIVER_WARN_SECONDS", bus_cfg_raw.get("slow_deliver_warn_seconds", 5.0)))` to `BusConfig(...)` constructor call. Env var `BUS_SLOW_DELIVER_WARN_SECONDS` overrides YAML; YAML overrides the default `5.0`.

4. **`agent-core-voice/endpoint.py`**: Add `from agent_core.bus.protocol import EndpointUnavailable` to the top-level imports (it is not currently present). Refactor `VoiceEndpoint.__init__` to store `_model_path`, `_device`, `_attn_implementation`, set `self._backend = backend` (not construct), remove `prepare_voice` loop. Expand `start()` to build backend off-thread and warm voices off-thread. Add `EndpointUnavailable` guard at top of `deliver()`.

5. **`packages/core/tests/bus/test_core_dispatch.py`**: Add three watchdog tests:
   - `test_slow_deliver_emits_warning`: endpoint with `asyncio.sleep(0.1)` in `deliver()`, threshold `0.001`; assert `caplog` contains a record with `"SlowDeliverWarning"` in message.
   - `test_fast_deliver_no_warning`: endpoint with immediate `ack` in `deliver()`, threshold `10.0`; assert no `"SlowDeliverWarning"` record.
   - `test_disabled_watchdog_no_warning`: endpoint with `asyncio.sleep(0.1)`, threshold `0.0` (disabled); assert no `"SlowDeliverWarning"` record.

6. **`packages/agent-core-voice/tests/test_endpoint.py`**: Update **seven** existing tests — three that tested `__init__`-era construction behaviour, plus four synthesis/dispatch tests that will break because `FakeTTSBackend.synthesize()` raises `VoiceNotPreparedError` for any voice not in `self._prepared`, and `prepare_voice` is now called only in `start()`. Each of these tests must call `await ep.start(_minimal_handle())` (or equivalent) before invoking synthesis or dispatch.

   **`_minimal_handle()` helper — define this at the top of `test_endpoint.py`**: Copy `_TrackingHandle` from `packages/agent-core-voice/tests/test_synthesis_task_lifecycle.py` (lines 27–60) and add a `_minimal_handle()` factory that returns a new instance. `_TrackingHandle` already supports the full `spawn`/`publish`/`ack`/`nack` interface required by `start()` and `deliver()`, and it is already used with `await ep.start(handle)` at line 84 of that file. This avoids re-inventing the stub. The factory signature is:

   ```python
   def _minimal_handle() -> _TrackingHandle:
       return _TrackingHandle()
   ```

   If copying `_TrackingHandle` feels like duplication, extract it to a shared `conftest.py` or a `_helpers.py` module in `packages/agent-core-voice/tests/` — either is acceptable. What is NOT acceptable is leaving `_minimal_handle()` undefined or having it return `None`, since `start()` stores the handle and `deliver()` calls `self._handle.spawn(...)`.

   Tests that tested `__init__`-era construction behaviour (rename + lifecycle fix):
   - `test_init_prepares_every_voice` → rename to `test_start_prepares_every_voice`, add `@pytest.mark.asyncio`, call `await ep.start(_minimal_handle())` before asserting `call_log`.
   - `test_init_missing_ref_wav_raises` → rename to `test_start_missing_ref_wav_raises`, add `@pytest.mark.asyncio`, call `with pytest.raises(FileNotFoundError): await ep.start(_minimal_handle())` (construction itself no longer raises; `prepare_voice` raises in `start()`).
   - `test_production_wiring_constructs_madrigal_qwen_backend` → convert to `async def`, add `@pytest.mark.asyncio`, then call `await ep.start(_minimal_handle())` before asserting `construct_calls`.

   Synthesis/dispatch tests that will fail after the refactor because voices are no longer prepared at construction time (add `await ep.start(_minimal_handle())` before the synthesis or dispatch call under test):
   - `test_synthesize_safe_happy_path` — add `await ep.start(_minimal_handle())` before calling `synthesize_safe`; without it, "alice" is not prepared and `synthesize_safe` returns `SynthesisFailed` instead of `SynthesisSuccess`.
   - `test_synthesize_output_path_layout` — same fix; add `await ep.start(_minimal_handle())` before the synthesis call.
   - `test_handle_synthesis_request_ogg_writes_ogg_file` — add `await ep.start(_minimal_handle())` before calling `_handle_synthesis_request`; without it, "alice" is not prepared and `SynthesisFailed` is published instead of `SynthesisReady`.
   - `test_handle_synthesis_request_transcode_failure_publishes_failed` — add `await ep.start(_minimal_handle())` before calling `_handle_synthesis_request`; without it, the failure reason is `VOICE_NOT_PREPARED` instead of `INTERNAL_ERROR` (the monkeypatch on `transcode_audio` is never reached).

7. **`packages/agent-core-voice/tests/test_endpoint.py`**: Add three new tests:
   - `test_init_does_not_construct_backend`: spy-factory for `QwenTTSBackend`; asserts `construct_calls == []` after `VoiceEndpoint(model_path=..., ...)` with no `start()` call.
   - `test_start_builds_backend_off_thread` (`@pytest.mark.slow`): blocking `time.sleep(0.2)` factory, concurrent `asyncio` progress-ticker; asserts progress ticker ran during `start()`, proving loop was not frozen.
   - `test_for_test_backend_skips_construction`: spy-factory patched onto `madrigal.engine.QwenTTSBackend`; calls `VoiceEndpoint.for_test(backend=stub, ...)` + `await ep.start(handle)`; asserts spy factory was never called.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/protocol.py` | Add `SlowDeliverWarning` frozen dataclass; strengthen `Endpoint` docstring with MUST language |
| `packages/core/src/agent_core/bus/core.py` | Add `import time`; add `SlowDeliverWarning` to protocol import; add `slow_deliver_warn_seconds: float = 5.0` to `BusConfig`; add timing + `log.warning` in `Bus._dispatch` |
| `packages/core/src/agent_core/bus/runner.py` | Add `slow_deliver_warn_seconds` kwarg to `BusConfig(...)` from env var `BUS_SLOW_DELIVER_WARN_SECONDS` (falling back to `bus_cfg_raw`, then `5.0`) |
| `packages/agent-core-voice/src/agent_core_voice/endpoint.py` | Remove backend construction + `prepare_voice` from `__init__`; store `_model_path/_device/_attn_implementation`; move construction off-thread into `start()`; add `EndpointUnavailable` guard at top of `deliver()` |
| `packages/core/tests/bus/test_core_dispatch.py` | Add 3 watchdog tests (`slow_deliver_emits_warning`, `fast_deliver_no_warning`, `disabled_watchdog_no_warning`) |
| `packages/agent-core-voice/tests/test_endpoint.py` | Update 7 existing tests to call `start()` first (3 init-era tests + 4 synthesis/dispatch tests); add 3 new tests (`init_does_not_construct_backend`, `start_builds_backend_off_thread`, `for_test_backend_skips_construction`) |

No other files change. `bus/handle.py`, `bus/supervisor.py`, `bus/persistence.py`, and all other endpoint packages are untouched.

## Alternatives considered

1. **Concurrent per-endpoint dispatch (dispatch fan-out)**: deliver to all endpoints simultaneously rather than serially, so one slow endpoint cannot stall others. Ruled out explicitly in the approved design — it discards the existing serial-ordering + backpressure guarantee, is only justified if the prompt-`deliver()` contract is systematically violated (not the case today), and is explicitly deferred to its own evidence-gated ticket.

2. **Per-`deliver()` timeout + cancel**: wrap `await endpoint.deliver()` with `asyncio.wait_for(timeout=...)` and cancel slow endpoints. Ruled out for the same reason — an architectural change requiring the bus to compensate for contract-violating endpoints, not yet justified by evidence. The watchdog emits the signal; a future ticket acts on it.

3. **Quarantine VoiceEndpoint boot on exception rather than deferring construction**: keep `__init__` building the backend, rely on T4 (#273) quarantine to isolate a crash at boot. Ruled out — quarantine isolates a crash, but the current code freezes the loop *before* any exception; the freeze itself is the defect, not the eventual failure. Off-threading the construction in `start()` fixes the freeze and also makes a failed load T4-quarantinable.

4. **Move `prepare_voice` into `deliver()` lazily**: warm each voice on first synthesis request. Ruled out — breaks the "warm before first request → predictable low first-synthesis latency" property, and complicates the `deliver()` fast-return contract.

## Open questions

None. Every file path and line number referenced above was read directly from the live repo. The approved design resolves all architecture questions.

## Out of scope

- Concurrent per-endpoint dispatch — deferred, evidence-gated.
- Per-`deliver()` timeout / cancel — deferred, evidence-gated.
- Re-planning #297 (async redelivery sweep) and #300 (graceful drain) — the design doc says to re-plan both after #296 merges; that is the PM's responsibility.
- OS-level process supervisor (#265) — separate work.
- `ack`-vs-`nack` fixes, delivery retry backoff changes — T5/T6 tickets.
- Env-var override for fields other than `slow_deliver_warn_seconds` — the cross-cutting config-provenance chain is not being introduced here; only this specific field gains env-override because the issue and approved design explicitly require it (`BUS_SLOW_DELIVER_WARN_SECONDS`).
