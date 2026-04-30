# Responsive Inbox Validation - Final Report

Date: 2026-04-30
Branch: `feat/responsive-inbox`

## Summary

Sub-project I is validated end to end with the daemon-side responsive inbox
and the `agent-core-channel` stdio relay loaded in Claude Code.

Ship: YES

## Results

| Step | Scenario | Result | Evidence |
|---|---|---|---|
| 1 | Autonomous push-wake | PASS | Previously confirmed live after the channel relay fix: wake latency was about 56ms. |
| 2 | Burst coalescing | PASS | Initial 50ms debounce was too short for human-paced MCP sends. Updated debounce is urgency-aware: red=50ms, yellow=500ms, green=1000ms. Green validation produced one autonomous wake about 1.02s after the last send, and `list_pending` returned 5 `green-burst-*` envelopes. |
| 3 | Urgency ordering | PASS | `list_pending` returned `red-msg`, `yellow-msg`, `green-msg` even though creation order was green, yellow, red. |
| 4 | Same-sender batching | PASS | `list_pending(batch_window_seconds=30)` returned one `batch` entry containing 3 envelopes. Default `list_pending()` returned the same 3 envelopes as flat entries. |
| 5 | Mailbox-authoritative reconnect | PASS | While testbot was closed, an offline envelope was sent. On reconnect, `agent-core-channel` emitted `INBOX: 1 pending`; testbot autonomously called `list_pending`, found `offline-reconnect-test-2026-04-30T14:52Z-resend`, handled it, and confirmed the queue was empty. |

## Daemon Log Notes

- No `ALTER TABLE` errors were observed during validation.
- No data loss was observed.
- One `ClosedResourceError` occurred when a temporary validation MCP client
  closed before the green debounce push completed. The error was handled by
  clearing the stale HTTP session slot; the mailbox entry remained available
  and was later delivered through the reconnect path.

## Verification

Targeted suite after the urgency-aware debounce change:

```text
uv run pytest packages/core/tests/ packages/agent-core-channel/tests/ -q
333 passed, 2 skipped, 3 warnings
```
