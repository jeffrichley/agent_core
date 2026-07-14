# Spec: ack-vs-nack fixes for inbound and discord endpoints (issue #275)

## Goal

Fix two endpoints that currently ack transient failures, silently dropping recoverable mail instead of nacking so the bus can requeue and retry. Specifically: (1) `InboundEndpoint.deliver()` must dead-letter unexpected envelopes rather than acking them; (2) `DiscordEndpoint.deliver()` must raise `EndpointUnavailable` on 429 and 5xx responses rather than acking. Issue: https://github.com/jeffrichley/agent_core/issues/275. Design spec: `docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md` (§ ack-vs-nack fix, slice T6).

## Acceptance criteria

- **Inbound transient path (already correct, test only)**: calling `deliver()` on an `InboundEndpoint` whose `_handle is None` raises `EndpointUnavailable`; no ack is issued.
- **Inbound terminal path (fix)**: calling `deliver()` on an `InboundEndpoint` whose `_handle is not None` with an unexpected envelope raises a non-`EndpointUnavailable` exception (so the bus dead-letters it); `_handle.ack()` is never called.
- **Discord 429 path (fix)**: when `channel.send` raises an exception with `.status == 429` (after `channel_send_with_retries` exhausts its internal attempts), `deliver()` raises `EndpointUnavailable`; no ack is issued, no error `Acknowledgment` envelope is published.
- **Discord 5xx path (fix)**: same as 429 — transient HTTP errors in the 500–599 range raise `EndpointUnavailable`.
- **Discord success path (unchanged)**: a successful `deliver()` call acks and publishes an `Acknowledgment` envelope.
- **Discord terminal 4xx (unchanged)**: a non-429 4xx from the Discord API (e.g., 403 Forbidden) is treated as a terminal error — deliver() acks and publishes an error `Acknowledgment` envelope (agent-visible).
- **Discord `_ToolError` path (unchanged)**: bad arguments / unknown tool still ack with an error `Acknowledgment` envelope.
- Tests use strict fakes that carry the real duck-typed shape of `discord.HTTPException` (`.status`, `.retry_after`) and that refuse argument shapes the real library refuses.

## Approach

No GoF pattern applies. The two changes are minimal, targeted corrections to two existing `deliver()` methods; no new abstractions are needed. The relevant principle is "make the right thing easy": the bus's `_dispatch` already knows how to route `EndpointUnavailable` to `store.requeue` vs. any other exception to `store.mark_dead_letter` (`packages/core/src/agent_core/bus/core.py` lines 282–303). The endpoints just need to cooperate with that contract.

### Inbound fix (`endpoint.py` lines 131–147)

The `deliver()` body has exactly two branches. The `_handle is None` branch already raises `EndpointUnavailable` — no change. The `_handle is not None` branch currently warns and calls `self._handle.ack(envelope.id)`. The docstring says "Warn + ack so the bus doesn't dead-letter and retry forever." This is wrong: acking an unexpected envelope falsely marks it as "successfully delivered" and gives no observability. The fix replaces the `ack` call with `raise RuntimeError(...)`. The bus core catches that, logs "dead-lettering", and calls `store.mark_dead_letter`, which is the right outcome for a misrouted envelope. The updated docstring must say "raise RuntimeError so the bus dead-letters the misrouted envelope."

There is no "genuine success" path for inbound's `deliver()` (the endpoint is push-only; it should never receive envelopes).

### Discord fix (`endpoint.py` lines 771–809)

The `deliver()` method has two parallel blocks for `TextMessage` and `ToolInvocation`. Both catch `Exception` and always ack. The fix adds a check before acking:

```python
except Exception as exc:
    if is_retryable_discord_send_error(exc):
        raise EndpointUnavailable(f"discord '{self.name}': transient error: {exc}") from exc
    log.exception(...)
    await self._reply(envelope, f"error: {exc}", urgency="yellow")
```

`is_retryable_discord_send_error` is already defined in `packages/agent-core-discord/src/agent_core_discord/send_retry.py` and covers exactly the cases the issue names (429, 5xx, 408, `TimeoutError`, OS network errors). It must be imported in `endpoint.py`.

When `EndpointUnavailable` is raised, no `_reply` call is made and no `ack` is issued. The bus requeues the envelope. When T5 (delivery retry backoff) lands, requeued envelopes get exponential backoff automatically. Without T5, the requeue is immediate, which is still correct behaviour — the envelope is not dropped.

`_ToolError` exceptions are agent-facing errors (bad args, missing channel, unknown tool). They must still ack with an error `Acknowledgment` so the agent knows what went wrong. Only unhandled `Exception` subclasses that satisfy `is_retryable_discord_send_error` become `EndpointUnavailable`.

### Strict fakes

The issue requires "a fake that refuses argument shapes the real lib refuses." The existing `_HTTP429Like` class in `test_endpoint_outbound.py` carries `.status = 429` and `.retry_after` — exactly what `is_retryable_discord_send_error` reads via `_http_status`. The new tests must use the same pattern (or import `_HTTP429Like` from that module) and add a parallel `_HTTP5xxLike` for 5xx errors. Both must carry `.status` (not a subclass attribute — the `_http_status` helper reads it via `getattr`). A `_HTTP403Like` (`.status = 403`, no `.retry_after`) is needed for the terminal-4xx test.

## Sub-requests (topologically sorted)

1. **Fix `InboundEndpoint.deliver()`**: in `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py`, replace `await self._handle.ack(envelope.id)` with `raise RuntimeError(...)` in the `_handle is not None` branch; update the docstring.
2. **Import `is_retryable_discord_send_error` in discord endpoint**: add `from agent_core_discord.send_retry import is_retryable_discord_send_error` to `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (top-level import alongside existing `send_retry` import).
3. **Fix `DiscordEndpoint.deliver()` TextMessage path**: in the `except Exception as exc:` block that follows the `except _ToolError` for TextMessage, add the `is_retryable_discord_send_error` guard and raise `EndpointUnavailable` before falling through to `ack`.
4. **Fix `DiscordEndpoint.deliver()` ToolInvocation path**: same change in the parallel `except Exception as exc:` block for ToolInvocation dispatch.
5. **Write inbound tests** — new `packages/agent-core-inbound/tests/test_endpoint_deliver.py` covering the transient (`_handle is None`) and terminal (`_handle is not None`) paths.
6. **Write discord nack tests** — new `packages/agent-core-discord/tests/test_endpoint_nack.py` covering 429 → EndpointUnavailable, 5xx → EndpointUnavailable, terminal 4xx → acks, success → acks.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py` | Replace `await self._handle.ack(envelope.id)` with `raise RuntimeError(...)` in the non-None `deliver()` branch; update docstring from "Warn + ack" to "Warn + dead-letter" |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Add top-level import of `is_retryable_discord_send_error` from `send_retry`; in both `except Exception` blocks in `deliver()` (TextMessage path ~line 783 and ToolInvocation path ~line 806), add the transient-error guard that raises `EndpointUnavailable` before falling through to reply+ack |
| `packages/agent-core-inbound/tests/test_endpoint_deliver.py` | **New file**: two tests — `test_deliver_before_start_raises_endpoint_unavailable` and `test_deliver_after_start_unexpected_envelope_dead_letters` |
| `packages/agent-core-discord/tests/test_endpoint_nack.py` | **New file**: four tests — 429 → EndpointUnavailable, 5xx → EndpointUnavailable, 403 → acks with error, success → acks; includes `_HTTP429Like`, `_HTTP5xxLike`, `_HTTP403Like` strict fakes |

## Alternatives considered

1. **Raise `EndpointUnavailable` for unexpected inbound envelopes (requeue instead of dead-letter)** — would cause the bus to retry 5 times with backoff before dead-lettering. Ruled out: the envelope is permanently misrouted; retrying the same endpoint can never fix that. Immediate dead-letter gives earlier visibility in the DLQ and avoids burning delivery attempts.
2. **Keep acking unexpected inbound envelopes (no change to inbound)** — the current behavior. Ruled out: acking falsely says "delivered"; misrouted envelopes are invisibly consumed with no DLQ record, which is exactly the "silently dropped" failure mode the issue names.
3. **Add the transient check in `_send`/`_dispatch` helpers rather than in `deliver()`** — would mean `_send` raises `EndpointUnavailable` directly, converting the internal contract to mix bus-protocol concerns into the tool layer. Ruled out: `EndpointUnavailable` is a bus-boundary signal; it belongs at the `deliver()` boundary where the bus catches it. The helpers correctly raise `_ToolError` for agent-visible errors and raw exceptions for everything else.

## Open questions

None. The files, functions, and lines to change are all confirmed against the actual code. The `is_retryable_discord_send_error` function already classifies exactly the error kinds the issue names (429, 5xx).

## Out of scope

- T5 delivery retry backoff (`next_attempt_at` column + sweep): a separate ticket. T6 correctness does not depend on T5; the nack causes an immediate requeue without delay until T5 lands.
- `BusHandle.spawn()` tracked-task API (T2): the `asyncio.create_task` leaks in `_bus_publish_adapter` and the discord typing tasks are a separate issue.
- `EndpointSupervisor` circuit-breaker (T3): no changes to `Bus` or supervisor machinery here.
- Degraded boot (T4): `Bus.start()` is not touched.
- Any change to `max_delivery_attempts` or redelivery sweep logic.
- The inbound endpoint's `_serve_task` / uvicorn lifecycle: `start()` is not touched.
- Discord 408 / `TimeoutError` / OS network error paths: `is_retryable_discord_send_error` already handles them; the test suite must not duplicate all branches — one 5xx test is sufficient to exercise the guard.
