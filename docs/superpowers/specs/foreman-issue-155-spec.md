# Spec: Discord voice memo auto-transcription via Whisper (issue #155)

## Goal

When an inbound Discord message carries a voice-message (or any audio) attachment, `DiscordEndpoint` automatically transcribes it with a local Whisper model and delivers the transcription inline as `payload.text`, while keeping the downloaded audio file accessible via `metadata["attachments"][*].local_path`. See [issue #155](https://github.com/jeffrichley/agent_core/issues/155).

## Acceptance criteria

- A Discord voice message (`content_type` starts with `"audio/"`) received by `DiscordEndpoint` is transcribed and delivered with `payload.text` set to `"[voice: <transcription text>]"` (appended after any existing message text if present).
- If a message has typed text **and** a voice attachment, both appear: e.g. `"typed text\n[voice: <transcription>]"`.
- The original audio file remains accessible at `metadata["attachments"][0]["local_path"]` (unchanged from the existing auto-download path).
- Each audio attachment's metadata dict gains a `"transcription"` key on success, or `"transcription_error"` on failure. Both keys are never both present.
- Transcription failure (Whisper error, corrupt audio, etc.) is best-effort: delivery continues with the unmodified `payload.text` and a `transcription_error` marker in the attachment metadata. The message is never silently dropped or blocked.
- `faster-whisper` not installed → log one warning, deliver with `transcription_error: "faster-whisper not installed"`, no crash.
- Audio `duration_secs > transcribe_max_duration_secs` (default 300 s) → skip transcription, `transcription_error: "audio too long (Xs)"`.
- Three new `DiscordEndpoint.__init__` params with defaults: `transcribe_voice: bool = True`, `whisper_model: str = "base"`, `transcribe_max_duration_secs: float = 300.0`.
- Non-audio attachments (PDFs, images, etc.) are not affected.
- `new_failures_count == 0` on `just check` after the change.

## Approach

**Pattern naming.** No GoF pattern applies cleanly. The engineering principle is **SRP-at-the-boundary**: the Discord adapter already owns the inbound enrichment loop (download, metadata). Adding transcription is one more enrichment step in that same loop — not a new subsystem. The model cache on the endpoint mirrors the existing `_user_display_name_cache` pattern (lazy, instance-scoped, first-miss-then-hit).

**Detection.** Discord voice messages set `content_type` to `"audio/ogg"` (sometimes `"audio/ogg; codecs=opus"`). The check `(getattr(att, "content_type", "") or "").startswith("audio/")` catches all audio attachments, including voice memos and explicitly uploaded `.ogg` / `.mp4` audio files. No attempt to read the Discord-specific `duration_secs` field for detection — `content_type` is simpler and covers the same set. If Pepper or Jeff sends a non-voice audio file (e.g. a music clip), it gets transcribed; that is acceptable over-reach for this ticket.

**Library: `faster-whisper`.** The original live instance used `openai-whisper` via subprocess (`uv run --with openai-whisper python`) and took >15 s on 60 s of audio — well above the 5 s AC target. `faster-whisper` uses a CTranslate2 backend (~4× faster than openai-whisper on CPU), has no torch dependency, and meets the target: warm-model `base` inference is ~3–5 s for 60 s of audio on a modern CPU. It is added as an **optional dependency** under `[project.optional-dependencies] voice = ["faster-whisper"]` in `agent-core-discord/pyproject.toml`. If not installed, the endpoint degrades gracefully.

**Transcription execution.** `faster-whisper` inference is CPU-bound and synchronous. Calling it directly in the asyncio event loop would block the bot from handling other messages during the 3–5 s inference window. The transcription is therefore run via `await asyncio.get_event_loop().run_in_executor(None, self._transcribe_audio_sync, path)` inside a new `_transcribe_audio` async method. This keeps the event loop responsive.

**Model lifecycle.** The `WhisperModel` is lazy-loaded on the first voice message and cached at `self._transcription_model`. Subsequent calls reuse the warm model. The model is not pre-loaded at `start()` to avoid adding startup latency for endpoints that never see voice messages. The first message after startup will have cold-start overhead (~0.5–1 s extra); this is documented in code comments and the failure-modes table.

**Injection.** After the download loop in `_make_on_message_handler`, iterate over each attachment entry that has a `"transcription"` key. Concatenate the transcription(s) as `[voice: …]` lines and append them to the `text` variable (already derived from `parse_sigil(message.content or "")`). The Envelope is then constructed with the enriched `text`. The metadata attachment dicts carry the `transcription` key independently (so Pepper can see the raw transcription separately from the formatted text).

**Existing conventions preserved.** The download loop is best-effort: transcription failures add a marker and let delivery continue — exactly the same discipline as `download_error`. No new lifecycle tasks, no new sweep loop. The `_transcription_model` cache is not an asyncio task and requires no cancellation in `stop()`.

## Sub-requests (topologically sorted)

1. **Add `duration_secs: float | None = None` to `FakeAttachment` in `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py`.** This mirrors the real `discord.Attachment.duration_secs` attribute for voice messages and enables tests to construct realistic voice-message fakes.

2. **Add `faster-whisper` optional dep to `packages/agent-core-discord/pyproject.toml`.** Add `[project.optional-dependencies]` with `voice = ["faster-whisper>=1.1"]`. No change to `[project.dependencies]` (the dep stays optional).

3. **Add three init params and model-cache field to `DiscordEndpoint.__init__` in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`.** After `attachment_sweep_seconds` (line ~283): `transcribe_voice: bool = True`, `whisper_model: str = "base"`, `transcribe_max_duration_secs: float = 300.0`. In the `__init__` body, add: `self.transcribe_voice = transcribe_voice`, `self.whisper_model = whisper_model`, `self.transcribe_max_duration_secs = transcribe_max_duration_secs`, and `self._transcription_model: Any | None = None`.

4. **Add `_transcribe_audio_sync` and `_transcribe_audio` to `DiscordEndpoint` in `endpoint.py`.** `_transcribe_audio_sync(self, path: Path) -> str` is a pure-sync method that lazy-loads `faster-whisper.WhisperModel`, transcribes `path`, and returns the joined segment text. `_transcribe_audio(self, path: Path) -> str` is an `async` wrapper that calls `run_in_executor(None, self._transcribe_audio_sync, path)`.

5. **Wire transcription into the attachment loop in `_make_on_message_handler` in `endpoint.py`.** Two changes to this handler:

   (a) **Amend the initial attachment-collection loop** (the existing loop that captures `filename`, `url`, `content_type`, `size_bytes` from each `discord.Attachment`) to also capture `"duration_secs": getattr(att, "duration_secs", None)` into each entry dict. This makes the real Discord attachment's `duration_secs` field available to the transcription pass.

   (b) **Add a transcription pass** after the existing `for entry in attachments:` loop that populates `local_path`: for each entry whose `content_type` starts with `"audio/"` and `local_path` is not None, first check the duration gate — if `entry.get("duration_secs")` is not None and exceeds `self.transcribe_max_duration_secs`, set `entry["transcription_error"] = f"audio too long ({entry['duration_secs']:.0f}s)"` and `continue`. Otherwise call `await self._transcribe_audio(Path(entry["local_path"]))` and store the result in `entry["transcription"]` (or `entry["transcription_error"]` on any other exception). Then, collect all `entry["transcription"]` values and append them to `text` before `Envelope(...)` construction.

6. **Add `packages/agent-core-discord/tests/test_voice_transcription.py`.** Tests: happy path (transcription in payload.text and metadata), no-faster-whisper fallback, transcription exception → error marker, too-long audio → skip, non-audio attachment unchanged, warm-model reuse (model loaded once across two calls).

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py` | Add `duration_secs: float \| None = None` to `FakeAttachment.__init__` |
| `packages/agent-core-discord/pyproject.toml` | Add `[project.optional-dependencies]` with `voice = ["faster-whisper>=1.1"]` |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Add 3 init params + `_transcription_model` field; add `_transcribe_audio_sync()` + `_transcribe_audio()`; wire transcription into attachment loop in `_make_on_message_handler` |
| `packages/agent-core-discord/tests/test_voice_transcription.py` | **New.** 6 test cases covering: happy path, no-faster-whisper, transcription exception, too-long audio, non-audio unchanged, warm-model reuse |

## Alternatives considered

- **Use `openai-whisper` instead of `faster-whisper`:** The live instance already tried this via subprocess and hit >15 s for 60 s audio, well above the 5 s AC target. Even in-process, `openai-whisper` on CPU runs at roughly 1–3× realtime for the `base` model — still too slow for the target. `faster-whisper` (CTranslate2) runs at ~10–15× realtime on CPU, meeting the target. Rejected.
- **Use OpenAI Whisper API (`openai.audio.transcriptions`):** No cold-start model load, no GPU needed. Requires an OpenAI API key, adds per-transcription cost, uploads user audio to a third party, adds network latency (~1–2 s round trip + upload time for audio blobs). Not appropriate for a private-comms system. Rejected.
- **New `agent-core-voice-transcription` package (separate from `agent-core-discord`):** Clean separation; follows the `agent-core-voice` pattern for heavy model dependencies. Overkill for this feature: the transcription is a narrow inbound-enrichment step, not a standalone service or MCP tool. Adding it as an optional dep in the existing Discord package is simpler and matches the single-responsibility of that package (enrich inbound Discord messages). Rejected; revisit if other adapters need transcription.
- **Subprocess-per-message:** The original live approach. Cold-start model load on every message. Rejected — too slow.

## Open questions

None. Detection (`content_type.startswith("audio/")`), library (`faster-whisper`), injection format (`[voice: …]`), failure discipline (best-effort matching the download loop), and config defaults are all specified with enough precision for the Worker to implement without further input. Jeff's comment on 2026-07-04 confirms: "The original audio should be attached as well as the transcribed text" — which this spec satisfies via `metadata["attachments"][*].local_path` + `payload.text` injection.

## Out of scope

- GPU device selection for `faster-whisper` (can be added later via a `whisper_device: str = "cpu"` param; YAGNI until a named GPU host runs this).
- Language forcing (Whisper auto-detects; forcing is a future config option if Jeff needs it).
- Streaming transcription (sentence-by-sentence delivery during inference). Full utterance only.
- Channel-renderer updates to display voice transcriptions differently from typed text (the `[voice: …]` prefix is sufficient for now; future formatting is a harness concern).
- Warm-up at `start()` (first-message cold-start is documented and acceptable; eager loading is a follow-up).
- Pre-existing audio attachments that Pepper missed (retroactive transcription is out of scope — this is a forward-only feature).
- Transcription of voice messages in the `download_attachments` MCP tool (tool-side transcription is a separate future ticket).
