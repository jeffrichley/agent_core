# Agent-Core Voice Service — Design

**Status:** Draft (brainstorming approved 2026-05-15)
**Author:** Jeff + Claude Opus 4.7 (1M context)
**Related:**
- Source spec: `C:\workspaces\ai\voices2\docs\pepper_voice_mcp_spec.md` (single-tenant Pepper MCP server reference)
- Reference implementation: `C:\workspaces\ai\voices2\scripts\pepper_mcp\server.py`
- Webcam analogue: `packages/agent-core-webcam/` and `docs/superpowers/specs/2026-05-06-pepper-webcam-design.md`
- Pepper rollout constraint: [[project_pepper_hands_off_until_proven]]

## Goal

Expose synthetic voice as an MCP tool any agent on the bus can call. The bus daemon holds **one** warm Qwen3-TTS model in VRAM and routes synthesis through it using in-context-learning (ICL) voice cloning. Each agent is bound to its own reference voice at mount time; agents cannot select or hear about each other's voices.

A tool call (`synthesize_speech(text, seed=42)`) returns a saved wav path plus duration metadata. The model is warm from call 1 for every configured voice — ICL prompts are pre-built at bus startup.

## Non-goals

- **Streaming generation.** Full-utterance wav only. Qwen3-TTS supports streaming text input via `non_streaming_mode=False`; we don't use it.
- **Live audio transport.** No Discord voice chat, no PortAudio playback, no PCM bus events. Output is a file; consumers forward it.
- **Auto-forwarding.** Tool returns a path. Agents decide where to send it.
- **Hot reload.** Adding/removing voices requires bus restart.
- **Multiple voices per agent.** One bound voice per agent. Future need = future design.
- **Cross-language.** Hardcoded `language="english"`.
- **Non-wav output.** No mp3 / opus / etc. Lossless wav preserves training-data and cache value.
- **Caching same-text-same-seed.** No dedup layer.
- **Multi-host inference.** Single bus daemon, single GPU, in-process. Per [[feedback_bus_services_self_contained]], the service stays in this repo — no reaching into sibling project venvs or remote machines.
- **Per-agent quotas.** Calls queue on the GPU.
- **Admin tool to list voices.** YAML is the source of truth.
- **Training-data pipeline.** Wavs and audit lines are saved (so they could feed a future training run), but no in-process pipeline reuses them.
- **Pepper's live config.** Per [[project_pepper_hands_off_until_proven]], rollout starts with a fresh test agent. Live Pepper YAML stays untouched until the service is validated.

## Architecture

### New package: `packages/agent-core-voice/`

Peer to `agent-core-webcam`. Same shape:

```
packages/agent-core-voice/
├── pyproject.toml                 # torch + qwen-tts deps live here, NOT in core
├── src/agent_core_voice/
│   ├── __init__.py
│   ├── protocol.py                # TTSBackend Protocol, VoiceInfo, error taxonomy
│   ├── qwen_backend.py            # Real backend: Qwen3-TTS + ICL voice clone (lazy-imports torch)
│   ├── fake.py                    # FakeTTSBackend for tests ONLY (never in production wiring)
│   ├── endpoint.py                # VoiceEndpoint (holds backend, voice registry, audit writer)
│   ├── mcp.py                     # register_voice_tools(mcp, endpoint, voice_id, agent_name)
│   ├── audit.py                   # append-only jsonl writer
│   └── plugin.py                  # pluggy hookimpls: register builtin.voice, wire mounters
└── tests/
    ├── conftest.py                # FakeTTSBackend fixture
    ├── test_protocol.py
    ├── test_fake_backend.py
    ├── test_endpoint.py
    ├── test_plugin_wiring.py
    └── test_audit_log.py
```

Heavy `torch` + `qwen-tts` deps live in this package only. `agent_core` core stays unchanged.

### Module-level torch protection

Only `qwen_backend.py` imports torch, and the import is lazy inside `QwenTTSBackend.__init__`. `protocol.py`, `fake.py`, `mcp.py`, `plugin.py`, `audit.py`, `endpoint.py` are importable without torch installed. Tests that exercise the plugin wiring and fake backend run on any host.

### Pluggy plugin hooks

Mirrors `agent-core-webcam/plugin.py` exactly:

- `register_endpoint_types()` → `{"builtin.voice": VoiceEndpoint}`
- `reserved_endpoint_params()` → `["voice", "voice_id"]` (popped from `claude_code_mcp` params before construction)
- `wire_endpoints_after_registration()` → for each `ClaudeCodeMCPEndpoint` whose yaml has `params.voice: <name>` and `params.voice_id: <id>`, validate the names against the voice registry (fail fast at startup), then append a deferred mounter that calls `register_voice_tools(mcp, endpoint=voice_ep, voice_id=<closed-in>, agent_name=<closed-in>)`.

## Isolation guarantee

The `synthesize_speech` tool registered on agent A's FastMCP server has `voice_id` closed into its closure. The tool's signature exposes no `voice_id` parameter. Agent A's tool surface literally only exposes their own voice. Isolation is by construction, not by check.

## Dependency strategy

### torch (uv optional-extras + conflicts)

Decided per host at install time. OS-agnostic — extras let the operator opt into CPU or CUDA on any platform.

```toml
[project]
name = "agent-core-voice"
requires-python = ">=3.12"   # voices2 lock pins Python 3.13; install on 3.13 specifically
dependencies = [
  "soundfile>=0.13",
  "qwen-tts",                # source resolved below
  "agent-core",
]

[project.optional-dependencies]
cpu   = ["torch>=2.11", "torchaudio>=2.11"]
cu130 = ["torch>=2.11", "torchaudio>=2.11"]

[tool.uv]
conflicts = [
  [{ extra = "cpu" }, { extra = "cu130" }],
]

[tool.uv.sources]
torch      = [
  { index = "pytorch-cpu",   extra = "cpu"   },
  { index = "pytorch-cu130", extra = "cu130" },
]
torchaudio = [
  { index = "pytorch-cpu",   extra = "cpu"   },
  { index = "pytorch-cu130", extra = "cu130" },
]

[[tool.uv.index]]
name = "pytorch-cpu"
url  = "https://download.pytorch.org/whl/cpu"
explicit = true

[[tool.uv.index]]
name = "pytorch-cu130"
url  = "https://download.pytorch.org/whl/cu130"
explicit = true
```

**Install commands:**
- GPU host (Windows or Linux with CUDA 13): `uv sync --extra cu130`
- CPU-only host (Mac, CI, dev laptop without GPU): `uv sync --extra cpu`

**Pin alignment with voices2:** voices2's lock pins `torch == 2.11.0`, `torchaudio == 2.11.0`, `transformers == 4.57.3` (last transitive from qwen-tts). `>=2.11` lets uv pick 2.11.0 today and advance when voices2 advances.

**Why extras, not platform markers:** OS doesn't predict GPU availability. A Linux GPU box and a Windows GPU box should both get CUDA wheels; a Mac and a Windows dev laptop without a GPU should both get CPU. Per-host choice via `--extra` is explicit; markers would over-constrain to OS.

### qwen-tts source — open implementation question

The plan phase resolves this with a 30-second check of upstream availability:

1. **Git ref to `github.com/Qwen/Qwen3-TTS`** (preferred if public). qwen-tts's own pyproject claims that repo URL. `qwen-tts = { git = "https://github.com/Qwen/Qwen3-TTS", rev = "<commit>" }`.
2. **Vendor it** under `packages/agent-core-voice/vendor/qwen_tts/` — bulkiest but zero external coupling, fully self-contained.
3. **Workspace-relative path** matching voices2's pattern (rejected): ties agent_core install to voices2 being present on disk.

## Backend protocol

```python
@runtime_checkable
class TTSBackend(Protocol):
    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        """Build + cache the ICL prompt for voice_id. Called once per configured voice at startup."""

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        """Generate audio for an already-prepared voice. Returns (wav_bytes, generation_s)."""

class VoiceError(Exception): ...
class EmptyTextError(VoiceError): ...
class TextTooLongError(VoiceError): ...
class GPUOOMError(VoiceError): ...
class VoiceNotPreparedError(VoiceError): ...
```

`QwenTTSBackend` (production) loads `Qwen3-TTS-12Hz-1.7B-Base` with bfloat16 + sdpa (or flash_attention_2 if available). `FakeTTSBackend` (tests only) returns deterministic dummy wav from a sine wave whose frequency is a hash of `(voice_id, text, seed)` and duration is proportional to `len(text)` — distinct inputs always produce distinct files. Per [[feedback_test_fakes_mirror_real_strictly]], the fake refuses argument shapes the real backend would refuse (unprepared voice, empty text, text over budget).

## VoiceEndpoint

Constructed once at `bus.start()` from yaml. Holds:

- `backend: TTSBackend`
- `voices: dict[str, VoiceInfo]` (the registry from yaml)
- `output_dir: Path`
- `audit: AuditWriter`

Production wiring: `VoiceEndpoint(**yaml_params)` constructs `QwenTTSBackend` internally — yaml says nothing about the backend. Test wiring: `VoiceEndpoint` exposes an explicit injection seam (a classmethod `for_test(backend=fake, voices=..., output_dir=...)` or equivalent kwarg) so tests construct an endpoint against `FakeTTSBackend` without touching torch.

`__init__` calls `backend.prepare_voice(voice_id, ref_wav, ref_text)` for every voice in the registry — every agent is warm from call 1.

Single public method:

```python
async def synthesize_safe(
    self, *,
    agent_name: str,
    voice_id: str,
    text: str,
    seed: int,
) -> SynthesisSuccess | SynthesisError:
    ...
```

It runs `backend.synthesize` inside `asyncio.to_thread` (synthesis blocks on GPU for seconds; keep the event loop responsive), maps exceptions to typed errors, picks the output path, writes the wav with `soundfile.write`, appends an audit line, returns the result envelope. The endpoint never raises — all failure modes become `SynthesisError(message=...)`.

### Output path convention

Service-owned, not caller-provided:

```
<output_dir>/<agent>/<YYYY-MM-DD>/<timestamp>-<seed>-<text_hash[:8]>.wav
```

Uniform layout supports future training-data harvesting and per-agent retention.

## MCP tool surface

Two tools mounted per-agent on each `ClaudeCodeMCPEndpoint`'s FastMCP server. Both are closed over `voice_id` + `agent_name` at mount time.

### `synthesize_speech(text: str, seed: int = 42)`

Returns a JSON `TextContent` block with:
```json
{"path": "...", "duration_s": 3.42, "sample_rate": 24000, "generation_s": 9.1}
```

On failure, returns a single `TextContent` block with `"synthesis failed: <message>"`. No raise (mirrors webcam's error-mapping pattern).

**No `voice_id` arg, no `output_path` arg.** Both are decided by the host's yaml and the plugin's mount-time closure.

### `voice_info()`

Returns the voice's static metadata as JSON `TextContent`: `{name, blend, base_model, ref_clip, ref_text, sample_rate, mode}` for the agent's bound voice **only** — no enumeration.

### Error mapping (the strings the agent sees)

- `"text is empty"` — `EmptyTextError`
- `"text exceeds model budget (N tokens)"` — `TextTooLongError`
- `"GPU is out of memory; try again in a moment"` — `GPUOOMError`
- `"output directory is not writable: <path>"` — filesystem error
- `"voice 'X' is not prepared"` — defensive; should never happen at runtime if startup succeeded

## YAML config

### Voice endpoint

```yaml
endpoints:
  voice:
    type: builtin.voice
    params:
      model_path: C:\workspaces\ai\Qwen3-TTS-EasyFinetuning\models\Qwen\Qwen3-TTS-12Hz-1.7B-Base
      device: cuda:0                  # default cuda:0; "cpu" runs real synthesis slowly
      attn_implementation: sdpa       # default sdpa; set flash_attention_2 only if flash-attn is installed (4–5× faster but optional)
      output_dir: E:\agent_core\voice_out
      audit_path: E:\agent_core\voice_out\audit.jsonl
      voices:
        test_agent:
          ref_wav: E:\workspaces\ai\voices2\blends\test_v1.wav
          ref_text: "..."
          blend: "..."                # optional metadata for voice_info
```

### MCP endpoint (per agent)

```yaml
  test_agent_mcp:
    type: builtin.claude_code_mcp
    params:
      voice: voice
      voice_id: test_agent
      # ... other claude_code_mcp params unchanged
```

An MCP endpoint that omits `voice` doesn't get `synthesize_speech` / `voice_info` mounted — voice is opt-in per agent.

### Startup validation (fail fast in `wire_endpoints_after_registration`)

1. `params.voice` names an endpoint that exists.
2. That endpoint is a `VoiceEndpoint`.
3. `params.voice_id` is in that endpoint's voices registry — error lists available ids.
4. Each voice's `ref_wav` file exists and is readable (checked at endpoint construction).
5. `output_dir` exists or is creatable; daemon has write permission.
6. If `device` starts with `cuda`, CUDA is available — otherwise clear startup error, not first-call failure.

## Model lifecycle + GPU model

### Production

- `QwenTTSBackend` always. Loaded once at `bus.start()`. Holds 6–10 GB VRAM for the bus daemon's lifetime.
- Startup time: model load (~10–30 s) + N × ICL-prompt build (sub-second each). Bus startup is delayed by this; budget for ~30–45 s with one voice.
- Per call (without flash-attn): roughly 8–15 s for one-sentence utterances on sdpa.
- Backend is never `FakeTTSBackend` in production wiring — the fake is test-only.

### Device

- `device: cuda:0` (default) — fail fast at startup if CUDA isn't available.
- `device: cpu` — real synthesis on CPU. Minutes per utterance. Useful for dev on a Mac when you want to actually hear the voice. Never silently substituted.
- No `device: auto`. Explicit only.

### Fakes (test-only)

`FakeTTSBackend` lives in `agent_core_voice/fake.py` and is **never** referenced by `plugin.py` or yaml. Tests construct `VoiceEndpoint` directly with `backend=FakeTTSBackend(...)`. Real synthesis is exercised by a local smoke test run before shipping; not in CI.

## Audit log

`<output_dir>/audit.jsonl` is append-only, one line per call:

```json
{"ts": "2026-05-15T14:23:01Z", "agent": "test_agent", "voice_id": "test_agent",
 "text_len": 42, "seed": 42, "duration_s": 3.42, "generation_s": 9.1,
 "wav_path": "...", "error": null}
```

`error` is null on success, the error message string on failure (and `duration_s` / `wav_path` are null in that case). Useful for capacity planning and finding bad synthesis later.

## Testing approach

Four test files, mapped to four concerns:

- `test_protocol.py` — error taxonomy + `TTSBackend` `runtime_checkable` verifier. No torch.
- `test_fake_backend.py` — fake's contract: refuses what real refuses, distinct inputs → distinct outputs, no torch.
- `test_endpoint.py` — `VoiceEndpoint` wiring against `FakeTTSBackend`: output-dir layout, audit jsonl format, error mapping, seed propagation, path naming.
- `test_plugin_wiring.py` — startup validation (missing voice_id, invalid voice endpoint, voice not in registry), mount-time closure correctly binds voice_id per agent, agent A's tool surface excludes agent B's voice.

No real-model integration test runs in CI. A local smoke test on the GPU host before shipping verifies acceptance criteria 1–6.

## Open implementation questions

1. **`qwen-tts` source** — git ref vs vendor (see Dependency strategy).
2. **CUDA index version** — `cu130` is what voices2's lock uses; verify the GPU host has CUDA 13. If it has 12.8, swap to `cu128`.
3. **Text-too-long budget** — what's the model's actual max sequence length? The endpoint should soft-reject before sending to the model. Pull from `Qwen3-TTS` model config.
4. **Voice registry size at startup** — startup time scales with number of voices. If we register many, may need a progress log.

## Acceptance criteria

1. `uv sync --extra cu130` on the GPU host installs cleanly; `uv sync --extra cpu` on a Mac/CI host installs cleanly.
2. Bus startup with a one-voice yaml logs "voice service ready" (or equivalent) within 60 s and serves the test agent.
3. `synthesize_speech("The quick brown fox jumps over the lazy dog.")` returns a valid wav path within 30 s on the GPU host (sdpa, no flash-attn). Typical 8–15 s.
4. Output integrity: returned wav is 24 kHz mono PCM, plays in any standard audio player, contains intelligible speech of the requested text.
5. Voice fidelity: subjective listening confirms the output sounds like the configured reference voice, not generic Qwen3-TTS default.
6. Ten consecutive calls show no model-reload pattern in logs and similar per-call latency (warm path).
7. `voice_info()` returns the static metadata for the agent's bound voice.
8. Isolation: an agent whose yaml binds `voice_id: A` cannot construct any tool call that produces voice B's output. Verified by inspecting the mounted tool's parameter schema.
9. Audit jsonl exists and contains one line per call with the documented fields.
10. Tests in all four test files pass on a host without torch installed (using `FakeTTSBackend`).
