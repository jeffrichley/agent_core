# Issue #44 — Handoff worker: bounded tail read + loud failure (Design)

> **Status:** Approved 2026-05-07. Ready for implementation plan.
>
> **Issue:** [#44](https://github.com/jeffrichley/agent_core/issues/44) — Handoff worker silent fallback stub on oversized transcripts.
>
> **Parent:** [#35](https://github.com/jeffrichley/agent_core/issues/35) — handoff pipeline reliability tracker.
>
> **Roadmap:** Phase 1 of `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md`.

## Problem

`HandoffJobsEndpoint`'s worker (`packages/core/src/agent_core/endpoints/handoff_jobs.py`) has two coupled defects:

1. `_read_transcript_text()` reads the entire transcript file unconditionally. Pepper's 18 MB transcript (≈4-5M tokens) caused the bundled `claude_agent_sdk.query()` subprocess to exit code 1.
2. `_extract_handoff()` catches every exception, generates a 268-byte fallback stub, and the worker writes status `state: "ready"` with the stub. Status consumers see "ready" but content is meaningless.

Net effect: every long-running agent silently produces stub handoffs presented as fresh.

The `HandoffInjector` consumer (`hooks/tools/handoff_injector.py:125-152`) already handles `state: "failed"` correctly — it labels the existing `handoff.md` as "last-known-good from an earlier successful cycle" and points the agent at `MEMORY.md` / dailies as ground truth. The architecture is sound; only the worker lies about status.

## Out of scope

- Idempotency-key fix (separate issue: [#42](https://github.com/jeffrichley/agent_core/issues/42)).
- Persisting `_idempotency_index` (separate issue: [#43](https://github.com/jeffrichley/agent_core/issues/43)).
- SessionEnd hook investigation (separate issue: [#45](https://github.com/jeffrichley/agent_core/issues/45)).
- Changing the summarization prompt or SDK / model.
- Cost tracking, rate limiting, queue throttling.
- User-configurable tail sizes via yaml. Defaults baked in via constructor params; override possible but no schema work.

## Design

### Tail-read helper

New module-level function in `handoff_jobs.py`:

```python
def _read_transcript_tail(
    transcript_path: Path,
    *,
    max_bytes: int = 256 * 1024,
    max_messages: int = 200,
) -> str:
    """Return the tail of a jsonl transcript, line-aligned, capped both ways."""
```

Behavior:
- File ≤ `max_bytes` → return whole file.
- File > `max_bytes` → seek to `file_size - max_bytes`, read to end, drop the first partial line (always — guarantees jsonl validity at the seam).
- After byte tail, if line count > `max_messages`, drop earliest lines until at threshold.

Constructor adds two params:

```python
def __init__(
    self,
    *,
    name: str,
    mount: str = "/internal/handoff-jobs",
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.25,
    transcript_tail_max_bytes: int = 256 * 1024,
    transcript_tail_max_messages: int = 200,
    jobs_log_dir: Path | None = None,  # see Stderr capture
):
```

### Worker simplification

`_extract_handoff()` collapses to:

```python
async def _extract_handoff(
    self, req: HandoffJobRequest, transcript_text: str, job_id: str
) -> str:
    response_text = await self._call_agent_sdk(
        req=req, transcript_text=transcript_text, job_id=job_id,
    )
    if response_text.strip():
        return response_text
    raise RuntimeError("empty handoff extraction")
```

The outer `try/except` and fallback stub generation are gone. Exceptions propagate to `_process_job`'s `for attempt in range(...)` retry loop (current line 191-219), which already retries up to `_max_attempts` then calls `_write_failed(...)` with the captured error text. **No change to `_process_job`'s control flow.**

`_process_job`'s call to `_read_transcript_text` swaps to `_read_transcript_tail` with the new constructor params threaded through.

### Stderr capture

Wrap `_call_agent_sdk()` so the SDK's exception is captured *with* whatever stderr we can extract before re-raising:

```python
async def _call_agent_sdk(
    self, *, req: HandoffJobRequest, transcript_text: str, job_id: str,
) -> str:
    try:
        # existing query logic unchanged
        ...
        return response_text
    except Exception as exc:
        stderr_text = self._capture_subprocess_stderr(exc)  # best-effort
        job_log_path = self._jobs_log_dir / f"{job_id}.log"
        job_log_path.parent.mkdir(parents=True, exist_ok=True)
        job_log_path.write_text(stderr_text, encoding="utf-8")
        log.error(
            "handoff job %s SDK call failed; full stderr at %s: %s",
            job_id, job_log_path, str(exc)[:500],
        )
        summary = self._summarize_stderr(stderr_text, max_chars=500)
        raise RuntimeError(f"summarizer failed: {summary}") from exc
```

Two helpers:

- **`_capture_subprocess_stderr(exc)`** — best-effort. Walks `exc.__cause__` chain and `str(exc)` for subprocess output. The bundled SDK's exception type may carry it; we extract what we can. Falls back to `repr(exc)` if nothing structured.
- **`_summarize_stderr(text, max_chars)`** — first non-empty line + last non-empty line, joined with `" ... "` if truncation needed, capped at `max_chars` (default 500).

`_jobs_log_dir`: new instance attribute, defaults to `Path("~/.agent-core/handoffs/jobs").expanduser()`. Constructor param so tests can inject a temp dir.

### Three stderr destinations

| Destination | Content | Lifetime |
|---|---|---|
| Status file's `error` field | Truncated summary (≤ 500 chars) | Survives daemon restart; visible to injector → agent |
| Daemon log | One-line `log.error(...)` with path to per-job log | Ephemeral (rotates with logs) |
| `~/.agent-core/handoffs/jobs/<job_id>.log` | Full captured stderr | Survives daemon restart; debuggable later |

## Tests

All new tests in `packages/core/tests/test_handoff_jobs_endpoint.py` (existing file).

### Tail-read helper

- `test_read_transcript_tail_returns_whole_file_when_small`
- `test_read_transcript_tail_caps_by_bytes_aligned_to_newline`
- `test_read_transcript_tail_caps_by_message_count`
- `test_read_transcript_tail_drops_partial_first_line_after_byte_seek`
- `test_read_transcript_tail_handles_empty_file`
- `test_read_transcript_tail_handles_file_with_no_trailing_newline`

### Worker behavior

- `test_handoff_worker_uses_tail_for_oversized_transcripts` — 50 MB fake transcript; assert SDK receives ≤ tail size.
- `test_handoff_worker_marks_failed_when_sdk_raises` — mock SDK raises; status `state: "failed"`, never `ready`.
- `test_handoff_worker_does_not_overwrite_handoff_md_when_failed` — pre-write a real `handoff.md`; SDK fails; assert `handoff.md` unchanged byte-for-byte.
- `test_handoff_worker_writes_per_job_log_on_sdk_failure` — `<jobs_dir>/<job_id>.log` exists with captured stderr.
- `test_handoff_worker_truncates_stderr_in_status_error_field` — status `error` ≤ 500 chars.
- `test_handoff_worker_succeeds_on_small_transcript` — golden-path regression coverage.

### Test discipline

TDD — failing tests as the first commit, fixes in subsequent commits. Matches the roadmap's recommendation for Phase 1.

## Files touched

- `packages/core/src/agent_core/endpoints/handoff_jobs.py` — primary changes.
- `packages/core/tests/test_handoff_jobs_endpoint.py` — new tests.

No schema changes. No yaml changes. No new dependencies.

## Acceptance criteria

1. Worker reads at most `transcript_tail_max_bytes` / `transcript_tail_max_messages` from transcript regardless of file size.
2. SDK summarization failure → status `state: "failed"`, never `ready`.
3. `handoff.md` is never overwritten when summarization fails.
4. Per-job log file lands at `~/.agent-core/handoffs/jobs/<job_id>.log` with subprocess stderr.
5. Status file's `error` field is informative (not "Check stderr output for details") and ≤ 500 chars.
6. Daemon log line includes path to per-job log file for live debugging.
7. All new tests pass; existing tests still pass.
8. Manual verification: a real `/exit` from a `--continue` Pepper session produces either (a) a real `handoff.md` with current content, or (b) a status file showing `failed` with real error info — never a fresh stub marked `ready`.

## Verification step (closes #45 if successful)

After this lands, do a clean experiment:

1. Start a `--continue` session with non-trivial conversation activity.
2. `/exit`.
3. Check daemon log / audit feed for an inbound POST to `/internal/handoff-jobs`.
4. Check `handoff.md` for fresh, real content (or a `failed` status with real error).

If POST hits and handoff produces real content → [#45](https://github.com/jeffrichley/agent_core/issues/45) closes as "investigated, no bug." If no POST hits → [#45](https://github.com/jeffrichley/agent_core/issues/45) stays open and we redesign trigger surface.

## Branch

`fix/issue-44-handoff-worker-tail-fail-loud`

## Risks

- **`_capture_subprocess_stderr` is best-effort.** The bundled `claude_agent_sdk` exception type may not carry stderr in a structured way; we may end up with `repr(exc)` for some failure modes. That's still strictly better than today's "Check stderr output for details" — and the per-job log file at minimum captures what Python sees.
- **Tail-read could miss context for very chatty short windows.** A burst of 200+ small messages within a minute could push out useful earlier context. Mitigation: the byte cap is the primary bound; the message cap is a safety net for the inverse case (one massive tool_result message).
- **The `claude_agent_sdk` subprocess may still fail on the tail** if the prompt + tail still exceeds the model's context window. This is correct behavior — fail loudly, status reflects it, agent reads `failed` injector branch.
