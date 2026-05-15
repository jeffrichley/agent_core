# agent-core-voice — local smoke test

Runs on the GPU host with CUDA installed. Not in CI.

## Setup

```powershell
cd E:\workspaces\ai\agents\agent_core
uv sync --extra cu130
```

## Smoke script

Save as `scripts/voice_smoke.py` (path-of-your-choice; not in this commit):

```python
import asyncio
from pathlib import Path

from agent_core_voice.endpoint import VoiceEndpoint


async def main():
    ep = VoiceEndpoint(
        name="voice",
        model_path=r"C:\workspaces\ai\Qwen3-TTS-EasyFinetuning\models\Qwen\Qwen3-TTS-12Hz-1.7B-Base",
        device="cuda:0",
        attn_implementation="sdpa",
        voices={
            "test": {
                "ref_wav": r"C:\workspaces\ai\voices2\blends\custom_S70_C20_G10.wav",
                "ref_text": "<canonical ref text from voices2 wiki>",
            },
        },
        output_dir=Path("./voice_smoke_out"),
        audit_path=Path("./voice_smoke_out/audit.jsonl"),
    )

    for i, line in enumerate([
        "The quick brown fox jumps over the lazy dog.",
        "Hello, world.",
        "This is a smoke test.",
    ]):
        result = await ep.synthesize_safe(
            agent_name="test",
            voice_id="test",
            text=line,
            seed=42 + i,
        )
        print(result)


asyncio.run(main())
```

## Acceptance

1. Startup: "voice 'test' prepared" logged within 60 s.
2. Per-call latency: 8–15 s on sdpa (no flash-attn).
3. Three wav files appear under `./voice_smoke_out/test/<today>/`.
4. Each wav plays in a standard audio player and sounds like the reference voice.
5. `audit.jsonl` has three success lines.

## Baseline runs

(Implementer notes the GPU host date, CUDA version, attn backend, and observed latencies after the first successful local run.)
