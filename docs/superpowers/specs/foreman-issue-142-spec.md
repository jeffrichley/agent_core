# Spec: `synthesize_speech` format selection (mp3, ogg) (issue #142)

## Goal

Add an optional `format` parameter to the `synthesize_speech` MCP tool so callers can request `"mp3"` or `"ogg"` output instead of the default `"wav"`. The voice endpoint transcodes on the server side before writing the output file; callers receive the transcoded path in `SynthesisReadyPayload.wav_path` as today. See issue [#142](https://github.com/jeffrichley/agent_core/issues/142).

## Acceptance criteria

- `SynthesisRequestPayload` in `packages/agent-core-voice/src/agent_core_voice/envelopes.py` gains a `format: Literal["wav", "mp3", "ogg"] = "wav"` field. Passing any other string (e.g. `"opus"`, `"flac"`) raises a Pydantic `ValidationError` at construction time.
- The `_synthesize` tool function in `packages/agent-core-voice/src/agent_core_voice/mcp.py` gains a `format: str = "wav"` parameter; it is forwarded to `SynthesisRequestPayload`. The tool's JSON schema therefore exposes `format` as an optional string property with default `"wav"`. Validation of allowed values happens in `SynthesisRequestPayload` (not in the MCP layer), so invalid values return `{"error": "invalid_request", "detail": "..."}` rather than raising.
- A new `transcode_audio(wav_bytes: bytes, *, target_format: str) -> bytes` function is added to `packages/agent-core-voice/src/agent_core_voice/lifecycle.py`. It returns `wav_bytes` unchanged for `"wav"`, encodes to OGG Vorbis using `soundfile` for `"ogg"`, and shells out to `ffmpeg` via `subprocess.run` for `"mp3"`. An unknown `target_format` raises `ValueError`. An ffmpeg failure raises `RuntimeError` with the first 500 bytes of stderr.
- `write_addressed()` in `lifecycle.py` gains an `ext: str = "wav"` keyword parameter. The written file is `{sha256}.{ext}` instead of the hardcoded `{sha256}.wav`. The meta sidecar gains an `"ext"` key storing the extension. `cleanup_expired()` reads `meta.get("ext", "wav")` to locate the companion audio file, making old sidecars (without `"ext"`) continue to match `.wav`.
- `VoiceEndpoint._handle_synthesis_request()` in `packages/agent-core-voice/src/agent_core_voice/endpoint.py`:
  - Reads `audio_format = getattr(req, "format", "wav") or "wav"` from the validated request.
  - Calls `duration_s, _sr = self._wav_duration(wav_bytes)` on the original WAV bytes (before transcoding, since `soundfile` reliably reads WAV).
  - Calls `await asyncio.to_thread(transcode_audio, wav_bytes, target_format=audio_format)` to produce `audio_bytes`.
  - Calls `write_addressed(audio_bytes, root=self._output_dir, retain_s=retain_s, ext=audio_format)`.
  - A `RuntimeError` or `ValueError` from `transcode_audio` is caught alongside the existing `OSError` guard and publishes `SynthesisFailed(reason="INTERNAL_ERROR", retryable=False)`.
- `SynthesisReadyPayload.wav_path` field **name is unchanged** for backwards compatibility; callers polling `SynthesisReady` continue to read `payload["data"]["wav_path"]` and receive the path to the audio file regardless of format.
- Running `just check` exits zero.

## Approach

No GoF pattern applies cleanly: "no pattern fits, this is a straightforward parameter-threading + format-conversion addition."

**Where `format` lives on the wire.** `SynthesisRequestPayload` (in `envelopes.py`) is the right seam — it is the typed, validated wire contract between the MCP tool and the voice endpoint. Adding `format` there means:
- Pydantic rejects unsupported values at the MCP tool level (before anything reaches the bus), surfacing a clean `{"error": "invalid_request", "detail": "..."}` JSON response.
- `VoiceEndpoint._handle_synthesis_request()` reads `req.format` from an already-validated model — no string parsing in the hot path.

Putting `format` inside the existing `options: dict` field (which is already on the model) would work but bypasses Pydantic validation, hides the parameter from MCP schema introspection, and requires special-casing in the endpoint. First-class field is correct.

**Where conversion lives.** `lifecycle.py` already owns the write-addressed pattern for voice output. A `transcode_audio()` helper there keeps `endpoint.py` thin and makes the conversion path unit-testable in isolation. The endpoint calls `transcode_audio` in an `asyncio.to_thread` call (alongside `write_addressed`) so the CPU-bound conversion doesn't block the event loop.

**OGG via `soundfile`.** `soundfile` is already declared in `pyproject.toml` and imported in `endpoint.py`. It wraps `libsndfile`, which supports OGG Vorbis write natively (`format="OGG"`, `subtype="VORBIS"`). No new Python or system dependency for OGG support.

**MP3 via `ffmpeg` subprocess.** `libsndfile` cannot encode MP3. The spec uses `subprocess.run(["ffmpeg", "-f", "wav", "-i", "pipe:0", "-f", "mp3", "-b:a", "128k", "pipe:1"], ...)` with stdin/stdout pipes. `ffmpeg` is a system-level binary that must be present on `PATH` on the host running the voice daemon. No Python wrapper package is introduced; the call surface is narrow and explicit. A `FileNotFoundError` (ffmpeg not on PATH) is caught and re-raised as `RuntimeError`, which the endpoint converts to `SynthesisFailed(reason="INTERNAL_ERROR", retryable=False)`.

**`SynthesisReadyPayload.wav_path` backwards compat.** Renaming the field to `audio_path` would break the QA smoke test (`test_voice_synthesize_smoke.py` line 162: `wav_path = payload_data.get("wav_path")`) and any existing caller. The field keeps its name; its semantics widen to "path to the synthesized audio file, extension matches the requested format." The docstring is updated to reflect this.

**`cleanup_expired()` backwards compat.** Old meta sidecars (written before this change) have no `"ext"` key; `meta.get("ext", "wav")` defaults to `"wav"`, so cleanup behaves identically on legacy files.

**`synthesize_safe()` is out of scope.** That method is the pre-bus-async path (used by `test_endpoint.py`'s synchronous tests). It has its own `_next_output_path()` that hardcodes `.wav`. Adding format support there would require duplicating the transcoding logic; that method is already legacy and the issue does not mention it.

## Sub-requests (topologically sorted)

1. **`envelopes.py`** — Add `AudioFormat = Literal["wav", "mp3", "ogg"]` type alias. Add `format: AudioFormat = "wav"` field to `SynthesisRequestPayload`. No other changes to this file.

2. **`lifecycle.py`** — Add `transcode_audio(wav_bytes: bytes, *, target_format: str) -> bytes`. Update `write_addressed()` to accept `ext: str = "wav"` and write `{sha256}.{ext}` (not `{sha256}.wav`); update the meta JSON to include `"ext": ext`. Update `cleanup_expired()` to read `meta.get("ext", "wav")` when deriving the audio file path from a sidecar.

3. **`mcp.py`** — Add `format: str = "wav"` parameter to `_synthesize`. Forward it as `SynthesisRequestPayload(..., format=format)`. Update the tool `description` string to mention that `format` accepts `"wav"` (default), `"mp3"`, and `"ogg"`.

4. **`endpoint.py`** — In `_handle_synthesis_request()`: read `audio_format`; call `_wav_duration(wav_bytes)` on original WAV bytes; call `transcode_audio` in `asyncio.to_thread`; call `write_addressed` with `ext=audio_format`; extend the `except (OSError, RuntimeError)` guard to also catch `ValueError` from an unknown format.

5. **`test_envelopes.py`** — Add tests for `SynthesisRequestPayload` with `format` field: valid values `"wav"`, `"mp3"`, `"ogg"` accepted; invalid value `"flac"` raises `ValidationError`; default is `"wav"`.

6. **`test_lifecycle.py`** — Add unit tests for `transcode_audio()` (mock ffmpeg subprocess for MP3 tests; use real soundfile for OGG tests) and `write_addressed()` with `ext` param. Add test that `cleanup_expired()` cleans up `.mp3` and `.ogg` files via meta sidecars with `"ext"` populated.

7. **`test_mcp_tools.py`** — Add a test asserting `format` appears in the `synthesize_speech` tool's JSON schema properties; add a test that passing `format="mp3"` threads it through to `SynthesisRequestPayload` on the published envelope.

8. **`test_endpoint.py`** — Add tests for `_handle_synthesis_request` with `format="ogg"` using `FakeTTSBackend` and a mocked/real `transcode_audio`; assert the result file has `.ogg` extension. Add a test that a `RuntimeError` from `transcode_audio` (simulated via monkeypatch) publishes `SynthesisFailed`. MP3 tests touching real ffmpeg are marked `@pytest.mark.slow`.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-voice/src/agent_core_voice/envelopes.py` | Add `AudioFormat` type alias; add `format: AudioFormat = "wav"` field to `SynthesisRequestPayload`. |
| `packages/agent-core-voice/src/agent_core_voice/lifecycle.py` | Add `transcode_audio()`; update `write_addressed()` signature (add `ext` param, write `{sha}.{ext}`, store `"ext"` in meta JSON); update `cleanup_expired()` to read `meta.get("ext", "wav")`. |
| `packages/agent-core-voice/src/agent_core_voice/mcp.py` | Add `format: str = "wav"` to `_synthesize` signature; forward to `SynthesisRequestPayload`; update tool description. |
| `packages/agent-core-voice/src/agent_core_voice/endpoint.py` | In `_handle_synthesis_request()`: read `audio_format`, call `_wav_duration` on original WAV bytes, call `transcode_audio` in thread, call `write_addressed` with `ext`, broaden `except` to include `ValueError`. |
| `packages/agent-core-voice/tests/test_envelopes.py` | New tests: valid formats accepted; `"flac"` rejected; default `"wav"`. |
| `packages/agent-core-voice/tests/test_lifecycle.py` | New tests: `transcode_audio` (OGG real, MP3 mocked); `write_addressed` ext param; `cleanup_expired` handles `"ext"` in meta. |
| `packages/agent-core-voice/tests/test_mcp_tools.py` | New tests: `format` in schema; `format="mp3"` threads to envelope. |
| `packages/agent-core-voice/tests/test_endpoint.py` | New tests: OGG format synthesis writes `.ogg`; `transcode_audio` failure yields `SynthesisFailed`. |

No other files change. No new Python package dependencies. `ffmpeg` is a system runtime requirement only for MP3 output — callers requesting `"wav"` or `"ogg"` need no system-level additions.

## Alternatives considered

- **Put `format` inside the existing `options` dict.** `options` is already present as `dict[str, Any] | None` and the endpoint already reads `seed`, `chunk_strategy`, `parallel` from it. Rejected: shunting `format` there hides it from MCP schema introspection (it would not appear in `synthesize_speech`'s JSON schema properties), bypasses Pydantic enum validation at the MCP layer, and requires special-casing in the endpoint alongside the other options. A first-class field is both more discoverable and more correct.
- **Use a Python ffmpeg wrapper (e.g., `pydub`, `ffmpeg-python`).** These add Python package dependencies for a function that can be expressed in four lines of `subprocess.run`. Rejected: YAGNI; the subprocess approach is transparent, auditable, and uses no transitive dependency surface. If the team later needs richer audio processing, adding `pydub` as an explicit dependency is straightforward.
- **Rename `SynthesisReadyPayload.wav_path` to `audio_path`.** Semantically cleaner, but breaks the QA smoke test and any caller reading `wav_path`. Rejected for this issue; backwards compat is the constraint the issue author explicitly cited. This is a potential follow-up after all callers are updated.
- **Support `ogg` and `opus` via ffmpeg too (not soundfile).** Uniform implementation — all non-WAV formats go through ffmpeg. Rejected: OGG support via soundfile adds zero system dependencies and is already available. Uniformity is not worth the additional system dependency surface for operators who only want OGG.

## Open questions

None — the approach is unambiguous given the repo conventions and the issue's explicit constraints.

## Out of scope

- `"opus"` format. The issue flags it as future ("possibly ogg/opus"); spec covers `"wav"`, `"mp3"`, `"ogg"` only. `opus` can be a follow-up.
- Bitrate / quality knobs for MP3/OGG. The spec hardcodes `128kbps` MP3. Adding caller-configurable bitrate is a follow-up; the default covers the issue's motivating use case (Discord upload).
- `synthesize_safe()` path in `endpoint.py`. That method is the legacy pre-bus-async path. Format support is intentionally not added to it.
- Renaming `SynthesisReadyPayload.wav_path` to `audio_path`. Correct long-term but out of scope here; callers must be updated first.
- Any change to the QA smoke test (`test_voice_synthesize_smoke.py`). It continues to call `synthesize_speech` without `format` (defaults to `"wav"`) and reads `wav_path` from the result — both still work.
- Audio duration measurement on the transcoded bytes. `_wav_duration` reads the original WAV bytes from madrigal before transcoding; the duration does not change during transcoding, so this is correct.
