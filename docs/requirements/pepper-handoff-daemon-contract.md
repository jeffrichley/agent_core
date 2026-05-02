# Pepper Handoff Daemon Contract (Hook-Minimal)

**Author:** Pepper + Jeff alignment
**Date:** 2026-05-02
**Priority:** Critical
**Related:** `docs/requirements/pepper-requirements.md`, `packages/core/src/agent_core/hooks/tools/handoff_writer.py`, `packages/core/src/agent_core/bus/`

---

## Goal

Move handoff generation out of hook execution and into a daemon worker so session shutdown is reliable and hook runtime stays minimal.

The hook must become enqueue-only. The daemon must own status transitions and handoff writes.

---

## Decisions Locked In

1. **No hook-side handoff writing**
   - Hook code must never run the actual write/generation path.
   - Hook code must never spawn `claude -p` (or equivalent) for handoff generation.
   - No backward-compatible fallback path that re-enables hook writing.

2. **Daemon is single writer of `handoff-status.json`**
   - Hook does not write or mutate status.
   - Daemon transitions `pending -> ready|failed`.

3. **Agent SDK in daemon worker**
   - Generation happens via Agent SDK in daemon process.
   - CLI subprocess generation from hooks is removed for this flow.

4. **Pointer-first transcript handoff**
   - Hook submits `transcript_path`; it does not copy transcript files by default.
   - We assume daemon and hook can both read the same filesystem.
   - Seeing post-enqueue edits to transcript is acceptable for v1.

---

## Non-Goals (for this cut)

- Transcript immutability snapshotting in hook.
- Hook fallback mode for daemon outages.
- Multi-host distributed storage assumptions.
- Cryptographic signing of status sidecar.

---

## Lifecycle Overview

1. Claude lifecycle hook fires (`SessionEnd` or `PreCompact`).
2. Hook performs minimal validation and sends one request to daemon.
3. Daemon accepts job, records `pending` status.
4. Worker reads transcript from `transcript_path`, runs Agent SDK extraction.
5. Worker atomically writes `handoff.md`.
6. Worker atomically writes `handoff-status.json` as `ready` or `failed`.
7. Daemon publishes completion notification via bus.

---

## Hook Contract (enqueue-only)

### Responsibilities

- Validate required fields exist in incoming hook payload.
- Submit handoff job request to daemon.
- Return quickly with success/failure of enqueue attempt.

### Hard constraints

- No transcript parsing in hook.
- No LLM calls in hook.
- No direct `handoff.md` writes in hook.
- No `handoff-status.json` writes in hook.

### Required job payload fields

- `session_id` (string, non-empty)
- `event` (enum: `SessionEnd` or `PreCompact`)
- `agent_name` (string)
- `vault_root` (string path)
- `handoff_path` (string path)
- `handoff_status_path` (string path)
- `transcript_path` (string path)
- `requested_at` (RFC3339 timestamp)

### Optional payload fields

- `idempotency_key` (string; if omitted daemon derives one)
- `context` (small JSON object for diagnostics only; no transcript body)

---

## Daemon HTTP Contract

### Endpoint

- `POST /internal/handoff-jobs`

### Request body (JSON)

```json
{
  "session_id": "uuid-or-session-string",
  "event": "SessionEnd",
  "agent_name": "pepper",
  "vault_root": "C:/Users/jeffr/.pepper/Memory/pepper",
  "handoff_path": "C:/Users/jeffr/.pepper/Memory/pepper/handoff.md",
  "handoff_status_path": "C:/Users/jeffr/.pepper/Memory/pepper/handoff-status.json",
  "transcript_path": "C:/Users/jeffr/.pepper/transcripts/abc.jsonl",
  "requested_at": "2026-05-02T14:56:00Z",
  "idempotency_key": "optional"
}
```

### Success response

- HTTP `202 Accepted`

```json
{
  "job_id": "uuid",
  "status": "accepted"
}
```

### Failure responses

- HTTP `400` invalid payload
- HTTP `403` path outside allowed root
- HTTP `409` duplicate idempotency key already completed (optional behavior)
- HTTP `503` daemon cannot enqueue

---

## Validation + Safety Rules (daemon side)

1. **Path jail**
   - `handoff_path`, `handoff_status_path`, and `transcript_path` must resolve under `vault_root`.
   - Reject any path traversal or unresolved outside-root target.

2. **Idempotency**
   - Default key: hash of `session_id + event + handoff_path`.
   - Duplicate in-flight request returns same `job_id` (recommended).
   - Duplicate completed request may return `409` or prior `job_id`; choose one behavior and keep it stable.

3. **Atomic writes**
   - Write temp file then replace for `handoff.md` and `handoff-status.json`.

4. **Status ownership**
   - Daemon writes `pending` when job accepted for execution.
   - Daemon writes terminal status (`ready` or `failed`) exactly once per attempt.

---

## Status File Contract (`handoff-status.json`)

### Pending

```json
{
  "state": "pending",
  "session_id": "<session_id>",
  "updated_at": "<rfc3339>",
  "job_id": "<job_id>"
}
```

### Ready

```json
{
  "state": "ready",
  "session_id": "<session_id>",
  "updated_at": "<rfc3339>",
  "job_id": "<job_id>",
  "content_sha256": "<sha256 of written handoff.md>"
}
```

### Failed

```json
{
  "state": "failed",
  "session_id": "<session_id>",
  "updated_at": "<rfc3339>",
  "job_id": "<job_id>",
  "error": "<sanitized error summary>"
}
```

---

## Bus Notification Contract

After terminal status write, daemon publishes one envelope:

- `HandoffReady` on success
- `HandoffFailed` on terminal failure

Minimum payload:

- `job_id`
- `session_id`
- `agent_name`
- `handoff_path`
- `handoff_status_path`
- `content_sha256` (ready only)
- `error` (failed only)

---

## Retry Policy (v1)

- Retry on transient worker/SDK errors with exponential backoff.
- Do not retry on validation/path-jail errors.
- Max attempts configured in daemon settings.
- On final exhaustion, write `failed` and publish `HandoffFailed`.

---

## Implementation Notes

1. Build daemon route + queue + worker stub first (no LLM text quality tuning yet).
2. Switch hook tool to enqueue-only and remove local writer behavior in the same cut.
3. Keep logs structured with `job_id`, `session_id`, `event`, `attempt`.

---

## Acceptance Criteria

1. Hook execution path contains no direct handoff generation/writing logic.
2. Hook never invokes `claude -p` for handoff generation.
3. Daemon writes all `handoff-status.json` states.
4. Handoff generation occurs via Agent SDK in daemon worker.
5. End-to-end run produces:
   - `202` enqueue response
   - `pending` then `ready` (or `failed`) status transition
   - bus completion notification
6. If daemon is unavailable, hook fails enqueue fast and does not attempt local write.

