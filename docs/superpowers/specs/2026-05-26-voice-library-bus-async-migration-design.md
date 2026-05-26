# Voice-library bus-async migration design

**Status:** Draft for review
**Authors:** Wren (architecture sketch) + Pepper (criterion-check, consumer-side requirements)
**Date:** 2026-05-26
**Empirical evidence:** Comparison run 2026-05-26 (see §2)

## 1. Goal

Migrate `agent-core-voice`'s synthesis path from the current synchronous in-package `QwenTTSBackend` to the `voice` library's `voice.generate()` orchestrator, with the consumer-facing MCP surface flipping from blocking-sync to async-via-bus.

Two outcomes:

- **3x faster synthesis** on conversational multi-sentence passages (empirically measured)
- **Unblocked agent sessions** — Pepper (and any future consumer) no longer holds her session waiting on a 60-second model inference

## 2. Empirical baseline

Comparison run on Jeff's workstation 2026-05-26, both paths driving the same loaded Qwen3-TTS model with seed=42 and Pepper's reference voice:

| Path | Strategy | Elapsed | Audio duration | RTF |
|------|----------|---------|----------------|-----|
| A — agent-core-voice today | Single call on whole passage | 174.86s | 32.72s | 5.34 |
| B — voice-library | Sentence-chunked + native batched | 58.11s | 36.40s | 1.60 |

**Speedup: 3.01x.** Listener verdict (Jeff): B sounded marginally better on timing and prosody too.

Test passage: 5-sentence conversational Pepper-shaped passage. WAVs at `C:\Users\jeffr\.wren\tmp\voice_compare_out\`.

## 3. Architecture

Three components, one new pattern.

```
┌─────────────────┐                                  ┌──────────────────┐
│  Pepper session │                                  │  voice endpoint  │
│  (claude-code)  │                                  │  (daemon-side)   │
└────────┬────────┘                                  └────────┬─────────┘
         │                                                    │
         │ 1. synthesize_speech(text, ...)                    │
         │    MCP tool call                                   │
         ├──────► returns immediately with                    │
         │        {request_id, status: "queued"}              │
         │                                                    │
         │  (MCP tool publishes SynthesisRequest              │
         │   envelope on bus, doesn't wait)                   │
         │                                                    │
         │        SynthesisRequest envelope                   │
         │        ────────────────────────────────────────►   │
         │                                                    │
         │                                              [voice synthesizes
         │                                               via voice.generate(
         │                                                 text, Spec(...)
         │                                               )]
         │                                                    │
         │        SynthesisReady envelope (or SynthesisFailed)│
         │        ◄────────────────────────────────────────   │
         │        in_reply_to=<request_id>                    │
         │                                                    │
         │ 2. Pepper's inbox wakes with the result envelope.  │
         │    She does what she wanted to do with the WAV.    │
         │                                                    │
```

Key points:

- **MCP tool stays** as a thin envelope-fire helper. Pepper's call-site is uniform.
- **The wait is severed from the synthesis.** Pepper's session is free for the 58s of model inference.
- **Correlation uses the bus's built-in `in_reply_to`.** No new request_id field — every envelope already has an id; SynthesisReady/Failed set `in_reply_to=<request.id>`.
- **voice_id stays endpoint-side-closed.** Pepper cannot request testbot's voice; the voice endpoint reads `envelope.from_` and looks up the correct voice from its yaml config. Same security model as today.

## 4. Envelope schemas

All three use `kind=Event` with domain-specific `payload.type`. This matches how agent_core handles domain events today (see `packages/core/src/agent_core/bus/envelope.py:42`).

### 4.1 SynthesisRequest (caller → voice)

```yaml
kind: Event
from: "pepper"           # bus-stamped at publish
to: "voice"
payload:
  kind: Event
  type: SynthesisRequest
  schema_version: "1"
  data:
    text: str                                          # required
    timeout_s: float | null                            # optional, default 300
    retain_s: float | null                             # optional, default 3600 (1h)
    options:                                           # optional
      chunk_strategy: "none" | "sentence" | "paragraph"
      parallel: bool
      seed: int
```

Defaults (endpoint-side):

- `timeout_s` default 300s (5 min). Caller can override down (e.g., 30s for quick acks) or up. Hard cap: 600s (10 min). Requests with `timeout_s > 600` are rejected with `SynthesisFailed reason=INTERNAL_ERROR message="timeout_s exceeds 600s cap"` at request-parse time. The cap is per-request, not per-passage — voice-library's chunker keeps per-chunk synthesis well under the cap even for long audio (audiobook batch generation chunks into per-paragraph requests, each well under 600s). The cap may be revisited if Chrona audiobook generation pushes against it.
- `retain_s` default 3600s (1 hour). Caller can override; capped at 86400s (24h).
- `options.chunk_strategy` default `"sentence"`.
- `options.parallel` default `True`.
- `options.seed` default 42.

### 4.2 SynthesisReady (voice → caller)

```yaml
kind: Event
from: "voice"
to: "pepper"
in_reply_to: "<SynthesisRequest.id>"
correlation_id: "<SynthesisRequest.correlation_id>"
payload:
  kind: Event
  type: SynthesisReady
  schema_version: "1"
  data:
    wav_path: str                  # absolute path to written WAV
    file_size_bytes: int           # cheap pre-stat for upload limit checks
    duration_s: float              # audio length
    elapsed_s: float               # synthesis wall-clock (model time)
    sample_rate_hz: int
    cache_hit: bool                # voice-library cache (future)
    chunks: int                    # diagnostic — how many chunks were synthesized
    retain_until: str              # ISO 8601 UTC; file may be gone after this
```

### 4.3 SynthesisFailed (voice → caller)

```yaml
kind: Event
from: "voice"
to: "pepper"
in_reply_to: "<SynthesisRequest.id>"
correlation_id: "<SynthesisRequest.correlation_id>"
payload:
  kind: Event
  type: SynthesisFailed
  schema_version: "1"
  data:
    reason: "GPU_OOM" | "TEXT_TOO_LONG" | "VOICE_NOT_PREPARED" | "INTERNAL_ERROR" | "TIMEOUT"
    message: str                   # human-readable detail
    retryable: bool                # caller hint — same text retry?
```

Failure reason taxonomy:

- `GPU_OOM` — retryable=true (transient).
- `TEXT_TOO_LONG` — retryable=false. A single chunk overflowed Qwen3-TTS's tokenizer. Caller must shorten or pre-chunk differently.
- `VOICE_NOT_PREPARED` — retryable=false. Voice ID not registered at endpoint. Bug; surface to ops.
- `INTERNAL_ERROR` — retryable=false generally. Carries `message` with the underlying exception type.
- `TIMEOUT` — voice endpoint emitted this because synthesis exceeded `timeout_s`. retryable=true if caller wants to extend the budget.

## 5. MCP tool surface

`synthesize_speech` stays as a tool but becomes a thin envelope-fire helper.

**Old signature** (blocking):

```python
synthesize_speech(text: str) -> {wav_path: str, duration_s: float, ...}
```

**New signature** (envelope-fire):

```python
synthesize_speech(
    text: str,
    timeout_s: float | None = None,
    retain_s: float | None = None,
    options: dict | None = None,
) -> {request_id: str, status: "queued"}
```

The tool's implementation:

1. Build the SynthesisRequest envelope from arguments.
2. `bus.publish(envelope)` (stamps `from_=<caller agent>`, `id=<uuid>`).
3. Return `{request_id: envelope.id, status: "queued"}`.

That's it. The caller agent's runtime sees the wake when the matching SynthesisReady/Failed arrives — the inbox-wake pattern already in place for every other event-driven flow.

## 6. File lifecycle

Voice endpoint owns WAV file lifecycle.

- **Location:** `~/.agent-core/voice/<sha256>.wav` — content-addressed by sha256 of audio bytes. Identical re-synthesis dedupes naturally.
- **Default TTL:** 3600s (1 hour) from synthesis time. Configurable per-request via `retain_s`.
- **Maximum TTL:** 86400s (24 hours). Hard cap to prevent unbounded disk growth.
- **Cleanup:** A bus-scheduler `voice.cleanup` tick fires every 5 minutes. Walks `~/.agent-core/voice/` and unlinks any `*.wav` whose `mtime + <recorded TTL>` < now. TTL is recorded in a sidecar `<sha256>.meta.json` written alongside the WAV at synthesis time.
- **Caller responsibility:** Callers MUST treat the WAV path as expiring at `retain_until`. After that timestamp, the file may be gone. Long-hold use cases (batch audiobook generation) must request `retain_s` accordingly or copy the file to caller-owned storage.

## 7. Failure semantics

Two complementary mechanisms.

### 7.1 Explicit failure envelope

Voice endpoint emits `SynthesisFailed` whenever it can detect a failure (model exception, voice-not-prepared, text-too-long). Carries enum `reason`, human-readable `message`, and `retryable` hint.

### 7.2 Caller-side timeout

Caller sets `timeout_s` on the request (default 300s). Voice endpoint tracks the deadline; if synthesis hasn't completed by then, it emits `SynthesisFailed` with `reason=TIMEOUT`. This requires the voice endpoint to be alive and responsive — the orphan-envelope case (voice dies mid-request, never emits anything) is called out as a v1 gap in §10.

## 8. Backward compatibility

**This is a breaking change** to the `synthesize_speech` tool surface. Old callers expecting `{wav_path: ..., duration_s: ...}` synchronously will break.

Affected callers (as of 2026-05-26):

- `testbot` — has the tool wired but isn't using it in any active flow per audit.
- `pepper` — has the tool wired; Pepper has been informed and is on-board with the new shape (she co-designed it).

Migration approach: hard-cut. No coexistence period.

**Rationale:** the consumer count is small (2 agents), both are AI agents with active stewards (Wren + Pepper), and there's no external/uncontrolled caller. A coexistence period would add ~3x the implementation work for no real safety win.

**Verification before cut:** grep for `synthesize_speech` callers in `~/.testbot/` and `~/.pepper/` to confirm the audit before merging.

## 9. Implementation phases

Mechanically split into independent units that ship in order.

**Phase 1 — voice library dependency + backend swap (low risk):**

1. Add `voice >= 0.1.0` as a dep in `packages/agent-core-voice/pyproject.toml`.
2. Delete `packages/agent-core-voice/src/agent_core_voice/qwen_backend.py` (now redundant — use `voice.engine.QwenTTSBackend`).
3. Update `endpoint.py` to construct `voice.engine.QwenTTSBackend(...)` and call `voice.generate(text, Spec(voice_id=voice_id, seed=seed, chunk_strategy="sentence", parallel=True))`.
4. Update tests to match.

**Phase 2 — envelope handler (medium risk):**

1. Add `SynthesisRequest` handler to the voice endpoint — listens on bus for envelopes addressed to `voice`, kind=Event, type=SynthesisRequest.
2. On receive: spawn synthesis as an **asyncio task in the daemon's existing event loop** (the same loop the bus uses to dispatch handlers). Record deadline = `now + timeout_s`.
3. On synthesis complete: publish `SynthesisReady` with the WAV path + metadata.
4. On synthesis failure (exception from `voice.generate`): publish `SynthesisFailed` with reason mapped from the `VoiceError` subclass + retryable hint.
5. On timeout reached: publish `SynthesisFailed` with reason=TIMEOUT, mark the task cancelled. **Cancellation is soft** — Qwen3-TTS's blocking GPU work does not honor asyncio cancellation, so the underlying inference runs to completion and its eventual result is discarded. The GPU stays busy for the duration of the cancelled task; subsequent SynthesisRequests queue behind it. A hard-cancel mechanism (subprocess isolation per synthesis) is deferred to v2 if cancellation latency becomes a real problem; for v1 the soft path is sufficient because the timeout is the safety net, not a routine flow.

**Phase 3 — MCP tool flip (low risk, but breaking):**

1. Update `synthesize_speech` MCP tool implementation in `agent_core_voice/mcp.py` to publish the envelope and return immediately.
2. Update tool schema (return type changes from `{wav_path, ...}` to `{request_id, status}`).
3. Update Pepper + testbot's per-agent docs/CLAUDE.md if they document the tool's return shape.

**Phase 4 — file lifecycle (low risk):**

1. Implement content-addressed WAV write path (`~/.agent-core/voice/<sha>.wav` + sidecar `.meta.json`).
2. Add `voice.cleanup` scheduler job (every 5 min, walks dir, unlinks expired).
3. Integrate with voice-library's cache: the on-disk WAV IS the cache artifact.

**Phase 5 — chunker over-split followup (separate, against voice repo):**

Not part of this migration. Open issue against `voice` repo: "chunker over-splits on common abbreviations (Dr., Mr., vs., P.S., PR)". Document as known limitation in voice's chunking module.

## 10. Known limitations

- **Chunker over-split on abbreviations.** Voice-library's `chunk_strategy="sentence"` splits on `[.!?]\s+`, which over-fires on abbreviations like "Dr.", "Mr.", "vs.", "P.S.", "PR". Empirically observed: Pepper's 5-sentence passage chunked to 9 pieces. Audio quality not worse than the unchunked baseline (Jeff's listening verdict), but suboptimal prosody at false-boundary chunks. Tracked as voice-repo followup.
- **Per-chunk text limit unknown at spec-time.** Qwen3-TTS has some tokenizer limit per call. Voice-library's chunker mitigates by sentence-splitting, but the practical per-chunk safe limit isn't documented. Implementation plan should include an empirical discoverable. Until then, `TEXT_TOO_LONG` is the reason callers get when a single chunk overflows; the failure is non-retryable with the same text.
- **No streaming.** Voice-library's batched path synthesizes all chunks in parallel then returns the concat. Callers wait the full inference window before getting anything. Adequate for current consumers (Pepper, audiobook batch); a future streaming variant is a separate feature.
- **Single GPU serialization.** Voice endpoint runs one synthesis at a time on the single GPU. Concurrent SynthesisRequests queue up behind the active one. Acceptable for the current 2-agent consumer base; multi-GPU sharding is out of scope.
- **Orphan-envelope risk (v1 gap).** If the voice endpoint dies mid-synthesis or is offline at request time, the caller's correlation never resolves — no SynthesisReady, no SynthesisFailed, no TIMEOUT (because TIMEOUT requires voice to be alive enough to emit it). The caller waits forever on a request that won't come back. Two crash modes: (1) voice crashes after receiving SynthesisRequest but before completing; (2) SynthesisRequest published to a queue that voice never drains. A centralized caller-side safety net (one shared "wake me at deadline if no in_reply_to matches" utility, living in agent_core_channel or as a bus primitive) is the right v2 mechanism — better than every consumer reimplementing the deadline-watcher. For v1 this is documented as a known gap; consumers tolerate the wedge in exchange for not blocking individual implementations on the centralized primitive. Daemon-supervisor heartbeats already detect voice-endpoint death within ~30s; recovery from an orphaned request is a manual session-restart for now.

## 11. Implementation cost estimate

Per-phase rough sizing (with prove-before-claim discipline — these are estimates, real runs will validate):

- Phase 1 (lib dep + backend swap): ~30 min code + tests
- Phase 2 (envelope handler): ~90 min code + tests
- Phase 3 (MCP tool flip): ~30 min code + tests + caller-doc updates
- Phase 4 (file lifecycle + cleanup): ~60 min code + tests
- Integration tests + real-engine validation: ~30 min
- **Total: ~4 hours.** Calls for one focused work session, possibly split across two.

Calibration note: yesterday's `agent-core-voice → voice-library` migration estimate-hedged at half-day (~4h) for what Jeff sized at 30min — but that was when the migration was scoped as just the backend swap (Phase 1). The async work (Phase 2-4) is what makes this a real 4h ticket. Honesty about scope, not anxiety-padding.

## 12. Testing strategy

- **Unit tests:** Per-phase, against fakes and the voice-library `FakeTTSBackend`. No GPU dependency.
- **Integration tests:** One bus-end-to-end test per envelope type (Request → Ready, Request → Failed reasons, Request → Timeout). Uses fake backend, real bus. Lives in `packages/agent-core-voice/tests/test_endpoint_envelope_flow.py`.
- **Real-engine validation gate:** Before declaring done, run one synthesis through the wired pipeline against the real Qwen3-TTS model on Jeff's workstation. Acceptance criteria: SynthesisRequest published → daemon synthesizes → SynthesisReady arrives at caller → WAV file exists at `wav_path` with size matching `file_size_bytes`. Latency comparable to today's 58s on a 5-sentence passage.
- **Pepper round-trip validation:** Pepper sends a real envelope from her session (not a test), gets the audio back, plays it. Same pattern as 2026-05-25's plugin-renderer behavioral round-trip.

## 13. Open questions

None identified. All criterion-check loops with Pepper closed; Jeff's high-level direction is set; phase split is mechanical.

## 14. References

- Voice library spec: `e:/workspaces/ai/agents/voice/docs/superpowers/specs/2026-05-24-voice-plan.md`
- Voice parallel-gen spec: `e:/workspaces/ai/agents/voice/docs/superpowers/specs/2026-05-25-voice-parallel-gen-design.md`
- Agent_core envelope schema: `packages/core/src/agent_core/bus/envelope.py`
- Agent_core voice endpoint (current impl): `packages/agent-core-voice/src/agent_core_voice/`
- Empirical comparison script: `C:\Users\jeffr\.wren\tmp\voice_compare.py`
- Empirical comparison output: `C:\Users\jeffr\.wren\tmp\voice_compare_out\`
- Pepper-Wren brainstorming envelopes (bus log): correlation_id `a659e00f2c9c4511aad0e6b87f920672` on 2026-05-26
