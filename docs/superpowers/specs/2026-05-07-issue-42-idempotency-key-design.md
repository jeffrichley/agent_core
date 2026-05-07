# Issue #42 — Handoff idempotency key collides across `--continue` resumes (Design)

> **Status:** Approved 2026-05-07. Ready for implementation plan.
>
> **Issue:** [#42](https://github.com/jeffrichley/agent_core/issues/42) — Handoff idempotency key collides across `--continue` session resumes.
>
> **Parent:** [#35](https://github.com/jeffrichley/agent_core/issues/35) — handoff pipeline reliability tracker.
>
> **Roadmap:** Phase 1 of `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md`. Phase 1 is half-complete: #44 and #45 closed; this is the second half.

## Problem

`HandoffJobsEndpoint`'s deduplication key collides across `--continue` session resumes. Symptom: the second `/exit` of a `--continue` session returns the prior `job_id` without re-running the worker, so `handoff.md` stays frozen at the first run's content.

Two places construct the key with the same defect:

- **Hook** (`packages/core/src/agent_core/hooks/tools/handoff_writer.py:66`):
  ```python
  "idempotency_key": f"{session_id}:{event}:{output_path}"
  ```
- **Daemon fallback** (`packages/core/src/agent_core/endpoints/handoff_jobs.py:507-509`):
  ```python
  raw = f"{req.session_id}|{req.event}|{req.handoff_path}"
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()
  ```

`--continue` reuses the same `session_id`, `event` is always `SessionEnd`, and `output_path` is fixed per agent. So every `/exit` produces the same key and dedupes against the first one.

The previously-fixed #44 silent-fallback bug masked this — once #44 surfaced failures correctly, #42 became the next layer of "frozen handoff" failure mode. After the daemon restart that picked up #44, the first `/exit` produced a real handoff (validated 2026-05-07 against Pepper's session `c92cdbdd-...`), but a second `/exit` without restart will still hit #42 and return the stale `job_id`.

## Out of scope

- **#43 (persist `_idempotency_index` across daemon restart).** Naturally resolved by this fix — see "Resolves #43 by superseding it" below.
- **Hook deployment independent of daemon.** The hook and daemon ship in the same Python package and always update together.
- **Idempotency for direct-API callers.** The `idempotency_key` field stays optional on `HandoffJobRequest` for callers who want explicit control.

## Design

### One source of truth: daemon-derived key

The hook stops sending `idempotency_key`. The daemon's `_derive_idempotency_key` becomes authoritative and includes `transcript_size`:

```python
def _derive_idempotency_key(self, req: HandoffJobRequest) -> str:
    """Derive a content-varying idempotency key.

    Includes ``transcript_size`` so real session activity produces a fresh
    key (the original ``--continue`` collision bug, #42). For files that
    no longer exist at intake time, falls back to size=-1 — the worker
    will fail loudly with FileNotFoundError downstream, which is the
    correct loud-failure behavior.
    """
    transcript_path = Path(req.transcript_path).expanduser()
    try:
        size = transcript_path.stat().st_size
    except OSError:
        size = -1
    raw = f"{req.session_id}|{req.event}|{req.handoff_path}|{size}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

Changes from the existing implementation:

- `@staticmethod` → instance method (allows future enhancements to use `self`-state without re-plumbing; no functional cost today).
- Adds `transcript_size` to the raw input, separated by `|`.
- Catches `OSError` (covers `FileNotFoundError`, `PermissionError`, etc.) and uses `-1` as a sentinel — a deterministic, reproducible value that survives to dedup retries of the same missing-file state. The downstream worker still fails loudly via the existing path.

### Hook simplification

In `packages/core/src/agent_core/hooks/tools/handoff_writer.py`, the `idempotency_key` line at line 66 is deleted entirely. The payload becomes:

```python
payload = {
    "session_id": session_id,
    "event": event,
    "agent_name": agent_name,
    "vault_root": str(vault_root),
    "handoff_path": str(output_path),
    "handoff_status_path": str(status_path),
    "transcript_path": transcript_path_str,
    "requested_at": datetime.now(UTC).isoformat(),
    "context": {},
}
```

Conditional `mailbox` and `transcript_root` additions stay as they are. `HandoffJobRequest.idempotency_key: str | None = None` stays — the field remains available for direct-API callers.

### Why this is enough

The original Pepper bug played out across two `/exit`s of the same `--continue` session, with conversation activity between them:

| Before fix | After fix |
|---|---|
| First `/exit`: key = `H(c92cdbdd:SessionEnd:handoff.md)`. New key, worker runs, handoff written. | First `/exit`: key = `H(c92cdbdd\|SessionEnd\|handoff.md\|18234567)`. New key, worker runs, handoff written. |
| Second `/exit`: key = same as first. Daemon dedupes, returns stale job_id. handoff.md stays frozen. | Second `/exit`: transcript grew (real activity → new bytes). key = `H(c92cdbdd\|SessionEnd\|handoff.md\|18241902)` — **different**. Worker runs, fresh handoff written. |

Within the same state (no activity between fires), the key stays stable and dedup still protects against rapid duplicates.

### Resolves #43 by superseding it

[#43 (persist `_idempotency_index` across daemon restart)](https://github.com/jeffrichley/agent_core/issues/43) was filed because the in-memory dedup cache wipes on daemon restart. With `transcript_size` in the key, **no two real handoff fires share a key** (transcripts only grow). Daemon-restart cache loss stops being observable: there's no scenario where a stale cached key would have been hit, because the previous state's key wouldn't match the current state's key anyway.

**Recommendation:** Close #43 as superseded once this PR lands. We may revisit later if rapid-dupe protection across daemon restarts ever becomes a real need.

## Tests

### Unit (in `packages/core/tests/test_handoff_jobs_endpoint.py`)

- `test_derive_idempotency_key_includes_transcript_size` — same `session_id` / `event` / `handoff_path` but different `st_size` → different keys.
- `test_derive_idempotency_key_stable_for_same_size` — two calls with the file unchanged → same key (rapid-dupe protection still works).
- `test_derive_idempotency_key_handles_missing_transcript` — nonexistent transcript path → deterministic key using size=-1, no exception raised at derivation time.

### Integration (also in `test_handoff_jobs_endpoint.py`)

- `test_post_job_dedupes_when_transcript_unchanged` — two POSTs with same payload + transcript file unchanged between them → second returns same `job_id`, worker runs once.
- `test_post_job_creates_new_job_when_transcript_grows` — two POSTs with same payload but transcript grew between them → different `job_id`s, worker runs twice.

### Hook regression (in `packages/core/tests/test_handoff_writer.py`)

- `test_handoff_writer_does_not_send_idempotency_key` — captures POST body; asserts `idempotency_key` is absent from payload.

## Files touched

- `packages/core/src/agent_core/endpoints/handoff_jobs.py` — `_derive_idempotency_key` updated.
- `packages/core/src/agent_core/hooks/tools/handoff_writer.py` — `idempotency_key` field removed from payload.
- `packages/core/tests/test_handoff_jobs_endpoint.py` — 5 new tests.
- `packages/core/tests/test_handoff_writer.py` — 1 new test.

No schema changes. No constructor changes. No yaml changes. No new dependencies.

## Acceptance criteria

1. Two `/exit`s on the same `--continue` session, with conversation activity between them, produce two distinct `job_id`s and two real handoff runs.
2. Two `/exit`s with no activity between them dedupe (same `job_id` returned, worker runs once).
3. The hook no longer sends `idempotency_key` in its POST body.
4. The daemon's `_derive_idempotency_key` always derives (the `or` fallback in `_post_job` falls through unconditionally for hook-driven traffic).
5. All existing handoff-related tests still pass; 6 new tests pass.
6. Manual verification on Pepper: `/exit` her `--continue` session a second time **without restarting the daemon**, confirm a fresh handoff is produced (different `job_id`, fresh `content_sha256`, real new content).

## Branch

`fix/issue-42-idempotency-key`

## Risks

- **Subtle change to dedup semantics.** Today, two POSTs with identical fields-but-not-content dedupe; tomorrow, identical fields-and-content dedup. The vast majority of callers send the same content for the same fields (because the path identifies the session). The narrow case where dedup behavior changes is "POSTed twice with file changed in between" — which is exactly the scenario we want to *not* dedupe. Acceptable risk; the new semantics match the bug fix.
- **`os.stat()` on the daemon's hot intake path.** Negligible: local filesystem stat is microseconds. Wouldn't be safe to put a network-mounted transcript path here, but the existing transcript_root resolution already constrains paths to local roots.
- **Direct-API callers who relied on the old key shape.** The default-derived shape changes (`H(s|e|p)` → `H(s|e|p|size)`). Any external caller who computed the daemon-side key offline and supplied it via `idempotency_key` would still work (their explicit key wins via the `or`). But if any caller computed `H(s|e|p)` themselves and expected dedup against daemon-derived keys, that's now broken. We have no such external callers in this monorepo.
