# Agent-Core Voice Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `packages/agent-core-voice/` workspace member that exposes Qwen3-TTS voice synthesis as MCP tools per-agent on the bus, with one warm model in VRAM and per-agent reference-voice isolation enforced at mount time.

**Architecture:** Mirrors `agent-core-webcam`. A `VoiceEndpoint` (`builtin.voice`) loads Qwen3-TTS once and pre-builds an ICL voice-clone prompt for every voice in a yaml-declared registry. Pluggy hooks wire two MCP tools (`synthesize_speech`, `voice_info`) onto each `ClaudeCodeMCPEndpoint` whose params name a voice — the tools' `voice_id` is closed into the closure at mount time so no agent can request another agent's voice.

**Tech Stack:** Python 3.12+, uv workspace, pluggy, FastMCP, soundfile, torch (≥2.11 via uv extras), qwen-tts (≥0.1.1), pytest, pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-05-15-agent-core-voice-design.md`

---

## File Structure

**New package** (`packages/agent-core-voice/`):

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, torch + qwen-tts deps via uv optional-extras (cpu/cu130), pluggy entry point |
| `src/agent_core_voice/__init__.py` | Package marker; exports nothing public |
| `src/agent_core_voice/protocol.py` | `TTSBackend` Protocol, `VoiceInfo` dataclass, error taxonomy |
| `src/agent_core_voice/fake.py` | `FakeTTSBackend` — deterministic sine-wave dummy for tests, refuses what real refuses |
| `src/agent_core_voice/qwen_backend.py` | `QwenTTSBackend` — real backend, lazy-imports torch inside `__init__` |
| `src/agent_core_voice/audit.py` | `AuditLog` + `AuditEvent` for jsonl append (mirrors webcam) |
| `src/agent_core_voice/endpoint.py` | `VoiceEndpoint` (holds backend, voice registry, audit, output_dir); `SynthesisSuccess`/`SynthesisError` envelopes |
| `src/agent_core_voice/mcp.py` | `register_voice_tools(mcp, endpoint, voice_id, agent_name)` mounts the two tools with voice_id closed in |
| `src/agent_core_voice/plugin.py` | Three pluggy hookimpls (`register_endpoint_types`, `reserved_endpoint_params`, `wire_endpoints_after_registration`) |
| `tests/conftest.py` | Shared fixtures (tmp output_dir, fake backend) |
| `tests/test_protocol.py` | Error inheritance + `TTSBackend` runtime-checkable verification |
| `tests/test_fake_backend.py` | Fake refuses what real refuses; deterministic distinct outputs |
| `tests/test_audit.py` | Audit log writes one jsonl line per event with the documented schema |
| `tests/test_endpoint.py` | `VoiceEndpoint` wiring against fake: startup validation, path layout, error mapping, seed propagation, audit |
| `tests/test_plugin_wiring.py` | `register_endpoint_types` + `wire_endpoints_after_registration` validation + closure binding |

**Files to modify** (root):

| File | Change |
|---|---|
| `pyproject.toml` | Add `agent-core-voice = { workspace = true }` to `[tool.uv.sources]`; add `packages/agent-core-voice/tests` to `[tool.pytest.ini_options].testpaths` |

---

## Task 1: Bootstrap package skeleton + resolve qwen-tts source

**Files:**
- Create: `packages/agent-core-voice/pyproject.toml`
- Create: `packages/agent-core-voice/src/agent_core_voice/__init__.py`
- Create: `packages/agent-core-voice/tests/__init__.py` (empty)
- Modify: `pyproject.toml` (root)

- [ ] **Step 1: Probe upstream qwen-tts availability**

Run: `curl -s -o /dev/null -w "%{http_code}\n" https://github.com/Qwen/Qwen3-TTS`

If 200: prefer **git source** for qwen-tts. Capture a commit hash to pin:
`gh api repos/Qwen/Qwen3-TTS/commits/main --jq '.sha'`

If 404 / private: fall back to **vendoring** — copy `E:\workspaces\ai\voices2\finetune\upstream\Qwen3-TTS\qwen_tts\` into `packages/agent-core-voice/vendor/qwen_tts/`, copy the upstream LICENSE alongside, and reference it from `[tool.uv.sources]` as `qwen-tts = { path = "vendor/Qwen3-TTS", editable = false }` (vendor the full project root, not just the package dir, so its own pyproject builds).

Record the decision in `packages/agent-core-voice/QWEN_TTS_SOURCE.md` (one paragraph: which option, why, the commit/version pinned). This file is for ops, not the design.

- [ ] **Step 2: Write package pyproject.toml**

Create `packages/agent-core-voice/pyproject.toml`. Replace the `qwen-tts` source block to match the choice from Step 1.

```toml
[project]
name = "agent-core-voice"
version = "0.1.0"
description = "Voice synthesis endpoint for agent_core — per-agent Qwen3-TTS via ICL voice cloning"
requires-python = ">=3.12"
dependencies = [
    "agent-core",
    "fastmcp>=2.0",
    "pluggy>=1.6",
    "pydantic>=2.7",
    "soundfile>=0.13",
    "qwen-tts",
]

[project.optional-dependencies]
cpu   = ["torch>=2.11", "torchaudio>=2.11"]
cu130 = ["torch>=2.11", "torchaudio>=2.11"]

[project.entry-points."agent_core"]
voice_aliases = "agent_core_voice.plugin"

[tool.uv]
conflicts = [
    [{ extra = "cpu" }, { extra = "cu130" }],
]

[tool.uv.sources]
torch = [
    { index = "pytorch-cpu",   extra = "cpu"   },
    { index = "pytorch-cu130", extra = "cu130" },
]
torchaudio = [
    { index = "pytorch-cpu",   extra = "cpu"   },
    { index = "pytorch-cu130", extra = "cu130" },
]
# qwen-tts source: REPLACE with the option chosen in Step 1.
# Option A (git): qwen-tts = { git = "https://github.com/Qwen/Qwen3-TTS", rev = "<commit>" }
# Option B (vendor): qwen-tts = { path = "vendor/Qwen3-TTS", editable = false }

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[[tool.uv.index]]
name = "pytorch-cu130"
url = "https://download.pytorch.org/whl/cu130"
explicit = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_voice"]
```

- [ ] **Step 3: Write empty package init**

Create `packages/agent-core-voice/src/agent_core_voice/__init__.py`:

```python
"""agent-core-voice — Qwen3-TTS voice synthesis endpoint for agent_core."""
```

Create `packages/agent-core-voice/tests/__init__.py` as an empty file.

- [ ] **Step 4: Register package in root workspace**

Edit `pyproject.toml` (root). In `[tool.uv.sources]`, after the `agent-core-hatchery` line, add:

```toml
agent-core-voice = { workspace = true }
```

In `[tool.pytest.ini_options].testpaths`, after `"packages/agent-core-webcam/tests"`, add:

```toml
    "packages/agent-core-voice/tests",
```

- [ ] **Step 5: Verify install (CPU mode)**

Run: `uv sync --extra cpu`

Expected: completes without error; `agent_core_voice` is importable. Verify:
`uv run python -c "import agent_core_voice; print('ok')"` → prints `ok`.

If the qwen-tts source fails to resolve, that's the moment to flip Step 1's decision and retry.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-voice pyproject.toml
git commit -m "feat(voice): bootstrap agent-core-voice package skeleton"
```

---

## Task 2: TTSBackend protocol + error taxonomy

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/protocol.py`
- Create: `packages/agent-core-voice/tests/test_protocol.py`

- [ ] **Step 1: Write failing test**

Create `packages/agent-core-voice/tests/test_protocol.py`:

```python
"""Error taxonomy + TTSBackend Protocol runtime-checkable verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core_voice.protocol import (
    EmptyTextError,
    GPUOOMError,
    TextTooLongError,
    TTSBackend,
    VoiceError,
    VoiceInfo,
    VoiceNotPreparedError,
)


def test_error_hierarchy() -> None:
    """All errors descend from VoiceError so handlers can catch one base."""
    for cls in (EmptyTextError, TextTooLongError, GPUOOMError, VoiceNotPreparedError):
        assert issubclass(cls, VoiceError)
        assert issubclass(cls, Exception)


def test_voice_info_is_frozen_dataclass() -> None:
    """VoiceInfo is immutable so it can be safely shared between threads."""
    info = VoiceInfo(
        voice_id="x",
        ref_wav=Path("/tmp/r.wav"),
        ref_text="hi",
        blend="test",
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        info.voice_id = "y"  # type: ignore[misc]


def test_tts_backend_is_runtime_checkable() -> None:
    """isinstance(obj, TTSBackend) works at runtime."""

    class _Impl:
        def prepare_voice(self, voice_id, ref_wav, ref_text):  # type: ignore[no-untyped-def]
            return None

        def synthesize(self, voice_id, text, seed):  # type: ignore[no-untyped-def]
            return b"", 0.0

    class _Missing:
        def prepare_voice(self, voice_id, ref_wav, ref_text):  # type: ignore[no-untyped-def]
            return None

    assert isinstance(_Impl(), TTSBackend)
    assert not isinstance(_Missing(), TTSBackend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-voice/tests/test_protocol.py -v`

Expected: FAIL with `ImportError: cannot import name 'TTSBackend' from 'agent_core_voice.protocol'`.

- [ ] **Step 3: Implement protocol module**

Create `packages/agent-core-voice/src/agent_core_voice/protocol.py`:

```python
"""TTSBackend protocol + error taxonomy.

The ``TTSBackend`` Protocol is the seam that lets ``VoiceEndpoint`` work
against either ``QwenTTSBackend`` (real) or ``FakeTTSBackend`` (tests).
All failure modes the endpoint maps to agent-readable error messages
descend from ``VoiceError`` so the endpoint's exception handling stays
simple.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class VoiceError(Exception):
    """Base for every error a TTSBackend may raise."""


class EmptyTextError(VoiceError):
    """Caller passed an empty or whitespace-only text."""


class TextTooLongError(VoiceError):
    """Text exceeds the model's token budget."""


class GPUOOMError(VoiceError):
    """The GPU ran out of memory during synthesis. Usually retryable."""


class VoiceNotPreparedError(VoiceError):
    """synthesize() called for a voice_id that was never prepare_voice()'d."""


@dataclass(frozen=True)
class VoiceInfo:
    """One configured voice in the registry."""

    voice_id: str
    ref_wav: Path
    ref_text: str
    blend: str | None = None


@runtime_checkable
class TTSBackend(Protocol):
    """The seam between VoiceEndpoint and the actual TTS model."""

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        """Build + cache the ICL prompt for ``voice_id``. Called once per voice at startup."""

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        """Generate audio for an already-prepared voice.

        Returns ``(wav_bytes, generation_s)``. Raises a ``VoiceError`` subclass on
        any failure the endpoint should turn into a SynthesisError.
        """


__all__ = [
    "EmptyTextError",
    "GPUOOMError",
    "TextTooLongError",
    "TTSBackend",
    "VoiceError",
    "VoiceInfo",
    "VoiceNotPreparedError",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent-core-voice/tests/test_protocol.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/protocol.py \
        packages/agent-core-voice/tests/test_protocol.py
git commit -m "feat(voice): TTSBackend protocol + error taxonomy"
```

---

## Task 3: FakeTTSBackend (test seam)

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/fake.py`
- Create: `packages/agent-core-voice/tests/test_fake_backend.py`

- [ ] **Step 1: Write failing tests**

Create `packages/agent-core-voice/tests/test_fake_backend.py`:

```python
"""FakeTTSBackend contract: refuses what real refuses, deterministic distinct outputs."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import soundfile as sf

from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.protocol import (
    EmptyTextError,
    TextTooLongError,
    VoiceNotPreparedError,
)


@pytest.fixture
def ref_wav(tmp_path: Path) -> Path:
    """A 1-second 24 kHz mono wav file for prepare_voice to read."""
    import numpy as np

    path = tmp_path / "ref.wav"
    sf.write(str(path), np.zeros(24000, dtype="float32"), 24000)
    return path


def test_synthesize_unprepared_raises(ref_wav: Path) -> None:
    backend = FakeTTSBackend()
    with pytest.raises(VoiceNotPreparedError):
        backend.synthesize("never_prepared", "hello", 42)


def test_empty_text_raises(ref_wav: Path) -> None:
    backend = FakeTTSBackend()
    backend.prepare_voice("v", ref_wav, "ref")
    with pytest.raises(EmptyTextError):
        backend.synthesize("v", "", 42)
    with pytest.raises(EmptyTextError):
        backend.synthesize("v", "   ", 42)


def test_text_too_long_raises(ref_wav: Path) -> None:
    backend = FakeTTSBackend(max_text_len=10)
    backend.prepare_voice("v", ref_wav, "ref")
    with pytest.raises(TextTooLongError):
        backend.synthesize("v", "this is way too long", 42)


def test_synthesize_deterministic(ref_wav: Path) -> None:
    """Same (voice_id, text, seed) → byte-identical output."""
    backend = FakeTTSBackend()
    backend.prepare_voice("v", ref_wav, "ref")
    a, _ = backend.synthesize("v", "hello", 42)
    b, _ = backend.synthesize("v", "hello", 42)
    assert a == b


def test_synthesize_distinct_inputs_distinct_outputs(ref_wav: Path) -> None:
    """Different inputs must produce different audio (catches voice mix-up bugs)."""
    backend = FakeTTSBackend()
    backend.prepare_voice("v1", ref_wav, "ref")
    backend.prepare_voice("v2", ref_wav, "ref")

    by_voice_v1, _ = backend.synthesize("v1", "hello", 42)
    by_voice_v2, _ = backend.synthesize("v2", "hello", 42)
    assert by_voice_v1 != by_voice_v2, "Different voice_id must change audio"

    by_text_a, _ = backend.synthesize("v1", "hello", 42)
    by_text_b, _ = backend.synthesize("v1", "world", 42)
    assert by_text_a != by_text_b, "Different text must change audio"

    by_seed_a, _ = backend.synthesize("v1", "hello", 1)
    by_seed_b, _ = backend.synthesize("v1", "hello", 2)
    assert by_seed_a != by_seed_b, "Different seed must change audio"


def test_synthesize_returns_valid_wav(ref_wav: Path) -> None:
    """The returned bytes are a real 24 kHz mono wav soundfile can decode."""
    backend = FakeTTSBackend()
    backend.prepare_voice("v", ref_wav, "ref")
    wav_bytes, generation_s = backend.synthesize("v", "hello world", 42)

    data, sr = sf.read(io.BytesIO(wav_bytes))
    assert sr == 24000
    assert data.ndim == 1
    assert len(data) > 0
    assert generation_s >= 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_fake_backend.py -v`

Expected: FAIL with `ImportError: cannot import name 'FakeTTSBackend'`.

- [ ] **Step 3: Implement fake backend**

Create `packages/agent-core-voice/src/agent_core_voice/fake.py`:

```python
"""FakeTTSBackend — test-only stand-in for QwenTTSBackend.

Returns deterministic sine-wave wav bytes keyed by ``(voice_id, text, seed)``.
Distinct inputs always produce byte-distinct outputs so tests that confuse
voices or seeds fail loudly. Refuses the same argument shapes the real
backend refuses (per the test-fakes-mirror-real-strictly principle).

NEVER referenced from plugin.py or yaml. Tests construct VoiceEndpoint
directly with ``backend=FakeTTSBackend(...)``.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import soundfile as sf

from agent_core_voice.protocol import (
    EmptyTextError,
    TextTooLongError,
    VoiceNotPreparedError,
)

_SAMPLE_RATE = 24000


class FakeTTSBackend:
    """Deterministic dummy backend for tests. Never used in production wiring."""

    def __init__(self, *, max_text_len: int = 10_000) -> None:
        self._prepared: set[str] = set()
        self._max_text_len = max_text_len

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        if not Path(ref_wav).exists():
            raise FileNotFoundError(f"ref_wav not found: {ref_wav}")
        self._prepared.add(voice_id)

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        if voice_id not in self._prepared:
            raise VoiceNotPreparedError(f"voice {voice_id!r} not prepared")
        if not text or not text.strip():
            raise EmptyTextError("text is empty")
        if len(text) > self._max_text_len:
            raise TextTooLongError(
                f"text length {len(text)} exceeds budget {self._max_text_len}"
            )

        # Frequency is a hash of (voice_id, text, seed) so distinct inputs
        # produce distinct audio. Duration is proportional to text length so
        # callers can assert on it.
        key = f"{voice_id}|{text}|{seed}".encode("utf-8")
        digest = hashlib.sha256(key).digest()
        freq_hz = 200 + (int.from_bytes(digest[:4], "big") % 600)  # 200–800 Hz
        duration_s = max(0.25, min(5.0, 0.05 * len(text)))

        n_samples = int(duration_s * _SAMPLE_RATE)
        t = np.arange(n_samples, dtype=np.float32) / _SAMPLE_RATE
        signal = 0.3 * np.sin(2 * np.pi * freq_hz * t).astype(np.float32)

        buf = io.BytesIO()
        sf.write(buf, signal, _SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue(), 0.001


__all__ = ["FakeTTSBackend"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_fake_backend.py -v`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/fake.py \
        packages/agent-core-voice/tests/test_fake_backend.py
git commit -m "feat(voice): FakeTTSBackend with deterministic distinct outputs"
```

---

## Task 4: Audit log

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/audit.py`
- Create: `packages/agent-core-voice/tests/test_audit.py`

- [ ] **Step 1: Write failing tests**

Create `packages/agent-core-voice/tests/test_audit.py`:

```python
"""AuditLog writes one jsonl line per event with the documented schema."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core_voice.audit import AuditEvent, AuditLog


@pytest.mark.asyncio
async def test_writes_success_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    await log.write(
        AuditEvent(
            timestamp=datetime(2026, 5, 15, 14, 23, 1, tzinfo=UTC),
            agent="alice",
            voice_id="alice",
            text_len=42,
            seed=42,
            duration_s=3.42,
            generation_s=9.1,
            wav_path="/tmp/x.wav",
            error=None,
        )
    )

    line = path.read_text("utf-8").strip()
    payload = json.loads(line)
    assert payload == {
        "ts": "2026-05-15T14:23:01+00:00",
        "agent": "alice",
        "voice_id": "alice",
        "text_len": 42,
        "seed": 42,
        "duration_s": 3.42,
        "generation_s": 9.1,
        "wav_path": "/tmp/x.wav",
        "error": None,
    }


@pytest.mark.asyncio
async def test_writes_error_line(tmp_path: Path) -> None:
    """On failure, duration_s + wav_path null, error populated."""
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    await log.write(
        AuditEvent(
            timestamp=datetime(2026, 5, 15, 14, 23, 1, tzinfo=UTC),
            agent="alice",
            voice_id="alice",
            text_len=0,
            seed=42,
            duration_s=None,
            generation_s=None,
            wav_path=None,
            error="text is empty",
        )
    )

    payload = json.loads(path.read_text("utf-8").strip())
    assert payload["error"] == "text is empty"
    assert payload["duration_s"] is None
    assert payload["wav_path"] is None


@pytest.mark.asyncio
async def test_appends_multiple_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    for i in range(3):
        await log.write(
            AuditEvent(
                timestamp=datetime(2026, 5, 15, 14, 23, i, tzinfo=UTC),
                agent="alice",
                voice_id="alice",
                text_len=i,
                seed=42,
                duration_s=1.0,
                generation_s=1.0,
                wav_path=f"/tmp/{i}.wav",
                error=None,
            )
        )

    lines = path.read_text("utf-8").splitlines()
    assert len(lines) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_audit.py -v`

Expected: FAIL with `ImportError: cannot import name 'AuditEvent'`.

- [ ] **Step 3: Implement audit log**

Create `packages/agent-core-voice/src/agent_core_voice/audit.py`:

```python
"""Append-only JSONL audit log for voice synthesis calls.

One line per ``synthesize_speech`` call (success or failure). Schema is
documented in the design spec. Audit-write failures are swallowed so an
audit problem never breaks a synthesis call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEvent:
    """One line in the voice audit log."""

    timestamp: datetime
    agent: str
    voice_id: str
    text_len: int
    seed: int
    duration_s: float | None
    generation_s: float | None
    wav_path: str | None
    error: str | None


class AuditLog:
    """Append-only JSONL audit log."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, event: AuditEvent) -> None:
        try:
            line = self._serialize(event)
            await asyncio.to_thread(self._append_line, self._path, line)
        except Exception as exc:
            msg = f"agent_core_voice.audit: write failed for {self._path}: {exc}"
            log.warning(msg)
            print(msg, file=sys.stderr)

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")

    @staticmethod
    def _serialize(event: AuditEvent) -> str:
        payload = {
            "ts": event.timestamp.isoformat(),
            "agent": event.agent,
            "voice_id": event.voice_id,
            "text_len": event.text_len,
            "seed": event.seed,
            "duration_s": event.duration_s,
            "generation_s": event.generation_s,
            "wav_path": event.wav_path,
            "error": event.error,
        }
        return json.dumps(payload, ensure_ascii=False)


__all__ = ["AuditEvent", "AuditLog"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_audit.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/audit.py \
        packages/agent-core-voice/tests/test_audit.py
git commit -m "feat(voice): jsonl audit log with success/error schema"
```

---

## Task 5: VoiceEndpoint construction + startup prep

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/endpoint.py`
- Create: `packages/agent-core-voice/tests/conftest.py`
- Create: `packages/agent-core-voice/tests/test_endpoint.py`

- [ ] **Step 1: Write conftest fixtures**

Create `packages/agent-core-voice/tests/conftest.py`:

```python
"""Shared fixtures for agent-core-voice tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from agent_core_voice.fake import FakeTTSBackend


@pytest.fixture
def ref_wav(tmp_path: Path) -> Path:
    """A 1-second 24 kHz mono wav file usable as a reference clip."""
    path = tmp_path / "ref.wav"
    sf.write(str(path), np.zeros(24000, dtype="float32"), 24000)
    return path


@pytest.fixture
def fake_backend() -> FakeTTSBackend:
    """A fresh FakeTTSBackend with no voices prepared."""
    return FakeTTSBackend()
```

- [ ] **Step 2: Write failing tests for VoiceEndpoint construction**

Create `packages/agent-core-voice/tests/test_endpoint.py`:

```python
"""VoiceEndpoint wiring against FakeTTSBackend."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.protocol import VoiceInfo


def test_init_prepares_every_voice(tmp_path: Path, ref_wav: Path) -> None:
    """All configured voices are prepare_voice'd before __init__ returns."""
    backend = FakeTTSBackend()
    voices = {
        "alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="hi alice"),
        "bob":   VoiceInfo(voice_id="bob",   ref_wav=ref_wav, ref_text="hi bob"),
    }
    ep = VoiceEndpoint.for_test(
        backend=backend,
        voices=voices,
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    assert ep.voice_ids() == {"alice", "bob"}
    # FakeTTSBackend recorded which voices were prepared.
    assert backend._prepared == {"alice", "bob"}


def test_init_creates_output_dir(tmp_path: Path, ref_wav: Path) -> None:
    out = tmp_path / "voice_out"
    assert not out.exists()
    VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=out,
        audit_path=out / "audit.jsonl",
    )
    assert out.is_dir()


def test_init_missing_ref_wav_raises(tmp_path: Path) -> None:
    """ref_wav validation runs during prepare_voice (the fake refuses missing files)."""
    with pytest.raises(FileNotFoundError):
        VoiceEndpoint.for_test(
            backend=FakeTTSBackend(),
            voices={
                "v": VoiceInfo(
                    voice_id="v",
                    ref_wav=tmp_path / "nope.wav",
                    ref_text="r",
                )
            },
            output_dir=tmp_path / "out",
            audit_path=tmp_path / "audit.jsonl",
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_endpoint.py -v`

Expected: FAIL with `ImportError: cannot import name 'VoiceEndpoint'`.

- [ ] **Step 4: Implement VoiceEndpoint construction**

Create `packages/agent-core-voice/src/agent_core_voice/endpoint.py`:

```python
"""VoiceEndpoint — bus endpoint exposing per-agent synthesis via MCP.

Implements the standard Endpoint protocol but ``deliver`` is a no-op:
voice is tool-only — no inbox, no agent-to-agent envelopes. The endpoint
holds the warm TTS backend, the registry of configured voices, the
output directory, and the audit log.

Construction wiring:

* Production: bus runner calls ``VoiceEndpoint(name=..., **yaml_params)``
  which constructs ``QwenTTSBackend`` internally.
* Tests: ``VoiceEndpoint.for_test(backend=fake, voices=..., ...)`` skips
  the real backend and injects a fake.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_core_voice.audit import AuditLog
from agent_core_voice.protocol import TTSBackend, VoiceInfo

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SynthesisSuccess:
    """Successful synthesis result."""

    path: str
    duration_s: float
    generation_s: float


@dataclass(frozen=True)
class SynthesisError:
    """Failed synthesis — message is the agent-readable string."""

    message: str


class VoiceEndpoint:
    """Tool-only bus endpoint backing the voice MCP tool surface."""

    def __init__(
        self,
        *,
        name: str,
        backend: TTSBackend,
        voices: dict[str, VoiceInfo],
        output_dir: Path | str,
        audit_path: Path | str,
    ) -> None:
        self._name = name
        self._backend = backend
        self._voices: dict[str, VoiceInfo] = dict(voices)
        self._output_dir = Path(output_dir)
        self._audit = AuditLog(Path(audit_path))

        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-build ICL prompts for every configured voice. After this returns,
        # every agent is warm from call 1.
        for voice_id, info in self._voices.items():
            self._backend.prepare_voice(voice_id, Path(info.ref_wav), info.ref_text)
            log.info("voice %r prepared (ref_wav=%s)", voice_id, info.ref_wav)

    @classmethod
    def for_test(
        cls,
        *,
        backend: TTSBackend,
        voices: dict[str, VoiceInfo],
        output_dir: Path | str,
        audit_path: Path | str,
        name: str = "voice_test",
    ) -> "VoiceEndpoint":
        """Test seam — same constructor, explicit name default."""
        return cls(
            name=name,
            backend=backend,
            voices=voices,
            output_dir=output_dir,
            audit_path=audit_path,
        )

    @property
    def name(self) -> str:
        return self._name

    def voice_ids(self) -> set[str]:
        return set(self._voices.keys())

    def voice_info(self, voice_id: str) -> dict[str, Any]:
        info = self._voices[voice_id]
        return {
            "voice_id": info.voice_id,
            "ref_clip": str(info.ref_wav),
            "ref_text": info.ref_text,
            "blend": info.blend,
            "sample_rate": 24000,
            "mode": "1.7B Base + ICL voice clone",
        }

    # Endpoint protocol stubs (voice is tool-only — no envelope traffic).
    async def deliver(self, envelope: "Envelope", bus: "BusHandle") -> None:
        del envelope, bus  # voice publishes nothing

    async def start(self, bus: "BusHandle") -> None:
        del bus

    async def stop(self) -> None:
        return None


__all__ = [
    "SynthesisError",
    "SynthesisSuccess",
    "VoiceEndpoint",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_endpoint.py -v`

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/endpoint.py \
        packages/agent-core-voice/tests/conftest.py \
        packages/agent-core-voice/tests/test_endpoint.py
git commit -m "feat(voice): VoiceEndpoint construction + voice-registry prep"
```

---

## Task 6: VoiceEndpoint.synthesize_safe — happy path + path layout

**Files:**
- Modify: `packages/agent-core-voice/src/agent_core_voice/endpoint.py`
- Modify: `packages/agent-core-voice/tests/test_endpoint.py`

- [ ] **Step 1: Write failing happy-path test**

Append to `packages/agent-core-voice/tests/test_endpoint.py`:

```python
import io
import re

import pytest
import soundfile as sf

from agent_core_voice.endpoint import SynthesisSuccess


@pytest.mark.asyncio
async def test_synthesize_safe_happy_path(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )

    result = await ep.synthesize_safe(
        agent_name="alice",
        voice_id="alice",
        text="hello world",
        seed=42,
    )

    assert isinstance(result, SynthesisSuccess)
    path = Path(result.path)
    assert path.exists()
    assert path.is_relative_to(tmp_path / "out" / "alice")
    # File is a valid 24 kHz mono wav.
    data, sr = sf.read(str(path))
    assert sr == 24000
    assert data.ndim == 1
    assert result.duration_s > 0
    assert result.generation_s >= 0


@pytest.mark.asyncio
async def test_synthesize_output_path_layout(tmp_path: Path, ref_wav: Path) -> None:
    """<output_dir>/<agent>/<YYYY-MM-DD>/<timestamp>-<seed>-<text_hash>.wav"""
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )

    result = await ep.synthesize_safe(
        agent_name="alice",
        voice_id="alice",
        text="hello",
        seed=42,
    )
    rel = Path(result.path).relative_to(tmp_path / "out")
    # alice / YYYY-MM-DD / timestamp-42-hash.wav
    parts = rel.parts
    assert parts[0] == "alice"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1])
    assert re.fullmatch(r"\d{8}T\d{6}-42-[0-9a-f]{8}\.wav", parts[2])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_endpoint.py -v`

Expected: FAIL with `AttributeError: 'VoiceEndpoint' object has no attribute 'synthesize_safe'`.

- [ ] **Step 3: Implement synthesize_safe happy path**

Add the imports + method to `packages/agent-core-voice/src/agent_core_voice/endpoint.py`. After existing imports, add:

```python
import asyncio
import hashlib
import io
from datetime import UTC, datetime

import soundfile as sf

from agent_core_voice.audit import AuditEvent
```

Inside `class VoiceEndpoint`, add the public method (place after `voice_info`):

```python
    async def synthesize_safe(
        self,
        *,
        agent_name: str,
        voice_id: str,
        text: str,
        seed: int,
    ) -> SynthesisSuccess | SynthesisError:
        """Synthesize text in ``voice_id``, write the wav, append audit, return envelope.

        Never raises. All failures land as ``SynthesisError(message=...)``.
        """
        wav_bytes, generation_s = await asyncio.to_thread(
            self._backend.synthesize, voice_id, text, seed
        )
        duration_s, sample_rate = self._wav_duration(wav_bytes)
        path = self._next_output_path(agent_name, seed, text)
        await asyncio.to_thread(path.write_bytes, wav_bytes)

        await self._audit.write(
            AuditEvent(
                timestamp=datetime.now(UTC),
                agent=agent_name,
                voice_id=voice_id,
                text_len=len(text),
                seed=seed,
                duration_s=duration_s,
                generation_s=generation_s,
                wav_path=str(path),
                error=None,
            )
        )
        return SynthesisSuccess(
            path=str(path),
            duration_s=duration_s,
            generation_s=generation_s,
        )

    def _next_output_path(self, agent_name: str, seed: int, text: str) -> Path:
        now = datetime.now(UTC)
        day = now.strftime("%Y-%m-%d")
        ts = now.strftime("%Y%m%dT%H%M%S")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        dir_ = self._output_dir / agent_name / day
        dir_.mkdir(parents=True, exist_ok=True)
        return dir_ / f"{ts}-{seed}-{text_hash}.wav"

    @staticmethod
    def _wav_duration(wav_bytes: bytes) -> tuple[float, int]:
        with sf.SoundFile(io.BytesIO(wav_bytes)) as f:
            return f.frames / float(f.samplerate), int(f.samplerate)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_endpoint.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/endpoint.py \
        packages/agent-core-voice/tests/test_endpoint.py
git commit -m "feat(voice): synthesize_safe happy path with service-owned output paths"
```

---

## Task 7: VoiceEndpoint.synthesize_safe — error mapping + audit on failure

**Files:**
- Modify: `packages/agent-core-voice/src/agent_core_voice/endpoint.py`
- Modify: `packages/agent-core-voice/tests/test_endpoint.py`

- [ ] **Step 1: Write failing error-mapping tests**

Append to `packages/agent-core-voice/tests/test_endpoint.py`:

```python
import json

from agent_core_voice.endpoint import SynthesisError


@pytest.mark.asyncio
async def test_synthesize_safe_empty_text_returns_error(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    result = await ep.synthesize_safe(agent_name="v", voice_id="v", text="", seed=42)
    assert isinstance(result, SynthesisError)
    assert "empty" in result.message.lower()


@pytest.mark.asyncio
async def test_synthesize_safe_text_too_long(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(max_text_len=5),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    result = await ep.synthesize_safe(
        agent_name="v", voice_id="v", text="this is too long", seed=42
    )
    assert isinstance(result, SynthesisError)
    assert "exceeds" in result.message.lower() or "too long" in result.message.lower()


@pytest.mark.asyncio
async def test_synthesize_safe_unprepared_voice(tmp_path: Path, ref_wav: Path) -> None:
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    result = await ep.synthesize_safe(
        agent_name="v", voice_id="other", text="hello", seed=42
    )
    assert isinstance(result, SynthesisError)
    assert "not prepared" in result.message.lower()


@pytest.mark.asyncio
async def test_audit_line_written_on_error(tmp_path: Path, ref_wav: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"v": VoiceInfo(voice_id="v", ref_wav=ref_wav, ref_text="r")},
        output_dir=tmp_path / "out",
        audit_path=audit_path,
    )
    await ep.synthesize_safe(agent_name="v", voice_id="v", text="", seed=42)

    payload = json.loads(audit_path.read_text("utf-8").strip())
    assert payload["error"] is not None
    assert payload["wav_path"] is None
    assert payload["duration_s"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_endpoint.py -v`

Expected: 4 new tests FAIL — `synthesize_safe` currently lets the underlying exception escape.

- [ ] **Step 3: Implement error mapping**

In `packages/agent-core-voice/src/agent_core_voice/endpoint.py`, add imports:

```python
from agent_core_voice.protocol import (
    EmptyTextError,
    GPUOOMError,
    TextTooLongError,
    TTSBackend,
    VoiceError,
    VoiceInfo,
    VoiceNotPreparedError,
)
```

Replace the body of `synthesize_safe` with a try/except that maps `VoiceError` subclasses (and any other unexpected exception) to `SynthesisError` and writes an audit line on failure:

```python
    async def synthesize_safe(
        self,
        *,
        agent_name: str,
        voice_id: str,
        text: str,
        seed: int,
    ) -> SynthesisSuccess | SynthesisError:
        try:
            wav_bytes, generation_s = await asyncio.to_thread(
                self._backend.synthesize, voice_id, text, seed
            )
        except VoiceError as exc:
            return await self._record_error(agent_name, voice_id, text, seed, exc)
        except Exception as exc:  # defensive — unknown backend failure
            log.exception("voice backend raised unexpected exception")
            return await self._record_error(agent_name, voice_id, text, seed, exc)

        try:
            duration_s, _ = self._wav_duration(wav_bytes)
            path = self._next_output_path(agent_name, seed, text)
            await asyncio.to_thread(path.write_bytes, wav_bytes)
        except OSError as exc:
            return await self._record_error(agent_name, voice_id, text, seed, exc)

        await self._audit.write(
            AuditEvent(
                timestamp=datetime.now(UTC),
                agent=agent_name,
                voice_id=voice_id,
                text_len=len(text),
                seed=seed,
                duration_s=duration_s,
                generation_s=generation_s,
                wav_path=str(path),
                error=None,
            )
        )
        return SynthesisSuccess(
            path=str(path),
            duration_s=duration_s,
            generation_s=generation_s,
        )

    async def _record_error(
        self,
        agent_name: str,
        voice_id: str,
        text: str,
        seed: int,
        exc: BaseException,
    ) -> SynthesisError:
        message = self._error_message(exc)
        await self._audit.write(
            AuditEvent(
                timestamp=datetime.now(UTC),
                agent=agent_name,
                voice_id=voice_id,
                text_len=len(text),
                seed=seed,
                duration_s=None,
                generation_s=None,
                wav_path=None,
                error=message,
            )
        )
        return SynthesisError(message=message)

    @staticmethod
    def _error_message(exc: BaseException) -> str:
        if isinstance(exc, EmptyTextError):
            return "text is empty"
        if isinstance(exc, TextTooLongError):
            return f"text exceeds model budget ({exc})"
        if isinstance(exc, GPUOOMError):
            return "GPU is out of memory; try again in a moment"
        if isinstance(exc, VoiceNotPreparedError):
            return str(exc)
        if isinstance(exc, OSError):
            return f"output directory is not writable: {exc}"
        return f"synthesis failed: {exc}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_endpoint.py -v`

Expected: 9 passed (5 existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/endpoint.py \
        packages/agent-core-voice/tests/test_endpoint.py
git commit -m "feat(voice): error mapping + audit-on-failure for synthesize_safe"
```

---

## Task 8: MCP tool registration with closure-bound voice_id

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/mcp.py`
- Create: `packages/agent-core-voice/tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests**

Create `packages/agent-core-voice/tests/test_mcp_tools.py`:

```python
"""register_voice_tools mounts two tools with voice_id closed in."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import FastMCP

from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.mcp import register_voice_tools
from agent_core_voice.protocol import VoiceInfo


@pytest.fixture
def endpoint(tmp_path: Path, ref_wav: Path) -> VoiceEndpoint:
    return VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={
            "alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r-alice"),
            "bob":   VoiceInfo(voice_id="bob",   ref_wav=ref_wav, ref_text="r-bob"),
        },
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )


@pytest.mark.asyncio
async def test_tools_registered(endpoint: VoiceEndpoint) -> None:
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await mcp.get_tools()
    assert "synthesize_speech" in tools
    assert "voice_info" in tools


@pytest.mark.asyncio
async def test_synthesize_speech_has_no_voice_id_arg(endpoint: VoiceEndpoint) -> None:
    """ISOLATION: the tool signature must not expose voice_id as a parameter."""
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await mcp.get_tools()
    schema = tools["synthesize_speech"].parameters
    # JSON-schema for FastMCP tools — voice_id should not appear in properties.
    props = schema.get("properties", {})
    assert "voice_id" not in props
    assert "output_path" not in props
    assert "text" in props


@pytest.mark.asyncio
async def test_synthesize_speech_success_returns_json_text(endpoint: VoiceEndpoint) -> None:
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await mcp.get_tools()
    result = await tools["synthesize_speech"].run({"text": "hello world", "seed": 42})
    # Result is list of TextContent blocks; the first block carries JSON.
    blocks = result.content if hasattr(result, "content") else result
    text = blocks[0].text
    payload = json.loads(text)
    assert "path" in payload and Path(payload["path"]).exists()
    assert payload["sample_rate"] == 24000
    assert payload["duration_s"] > 0


@pytest.mark.asyncio
async def test_synthesize_speech_failure_returns_human_string(endpoint: VoiceEndpoint) -> None:
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await mcp.get_tools()
    result = await tools["synthesize_speech"].run({"text": "", "seed": 42})
    blocks = result.content if hasattr(result, "content") else result
    text = blocks[0].text
    assert text.startswith("synthesis failed:")
    assert "empty" in text.lower()


@pytest.mark.asyncio
async def test_voice_info_returns_bound_voice_only(endpoint: VoiceEndpoint) -> None:
    """voice_info exposes only the agent's bound voice, not the registry."""
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await mcp.get_tools()
    result = await tools["voice_info"].run({})
    blocks = result.content if hasattr(result, "content") else result
    info = json.loads(blocks[0].text)
    assert info["voice_id"] == "alice"
    assert info["ref_text"] == "r-alice"
    # bob must NOT leak.
    assert "bob" not in json.dumps(info)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_mcp_tools.py -v`

Expected: FAIL with `ImportError: cannot import name 'register_voice_tools'`.

- [ ] **Step 3: Implement MCP tools**

Create `packages/agent-core-voice/src/agent_core_voice/mcp.py`:

```python
"""Agent-facing MCP tool surface for the voice endpoint.

Two tools: ``synthesize_speech`` and ``voice_info``. Both close ``voice_id``
and ``agent_name`` into their callable at mount time. The agent's tool
surface has no ``voice_id`` parameter — by construction, an agent cannot
request another agent's voice.

Returns a list of MCP TextContent blocks. On error, returns a single
TextContent with ``"synthesis failed: <reason>"``. No exceptions escape.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp.types import TextContent

from agent_core_voice.endpoint import SynthesisError, SynthesisSuccess

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from agent_core_voice.endpoint import VoiceEndpoint


def register_voice_tools(
    *,
    mcp: "FastMCP",
    endpoint: "VoiceEndpoint",
    voice_id: str,
    agent_name: str,
) -> None:
    """Mount ``synthesize_speech`` and ``voice_info`` on ``mcp``.

    ``voice_id`` and ``agent_name`` are closed into the callables — they are
    NOT parameters of the resulting tools.
    """

    @mcp.tool(
        name="synthesize_speech",
        description=(
            "Synthesize text in your assigned voice and save to a wav file. "
            "Returns the saved file path plus duration and sample-rate metadata. "
            "Use the returned path to attach to Discord, archive, or hand off "
            "to another tool."
        ),
    )
    async def _synthesize(text: str, seed: int = 42) -> list[Any]:
        result = await endpoint.synthesize_safe(
            agent_name=agent_name,
            voice_id=voice_id,
            text=text,
            seed=seed,
        )
        if isinstance(result, SynthesisError):
            return [TextContent(type="text", text=f"synthesis failed: {result.message}")]
        assert isinstance(result, SynthesisSuccess)
        payload = {
            "path": result.path,
            "duration_s": result.duration_s,
            "sample_rate": 24000,
            "generation_s": result.generation_s,
        }
        return [TextContent(type="text", text=json.dumps(payload))]

    @mcp.tool(
        name="voice_info",
        description=(
            "Return metadata about your assigned voice (id, ref clip, sample rate, mode)."
        ),
    )
    async def _voice_info() -> list[Any]:
        info = endpoint.voice_info(voice_id)
        return [TextContent(type="text", text=json.dumps(info))]


__all__ = ["register_voice_tools"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_mcp_tools.py -v`

Expected: 5 passed.

If the FastMCP `get_tools()` / `parameters` API shape differs slightly from this plan (e.g., returns objects rather than dicts), adapt the test inspection — the contract being verified is "voice_id is not in the input schema."

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/mcp.py \
        packages/agent-core-voice/tests/test_mcp_tools.py
git commit -m "feat(voice): MCP synthesize_speech + voice_info with closure-bound voice_id"
```

---

## Task 9: Plugin hookimpls — register_endpoint_types + reserved_endpoint_params

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/plugin.py`
- Create: `packages/agent-core-voice/tests/test_plugin_wiring.py`

- [ ] **Step 1: Write failing tests**

Create `packages/agent-core-voice/tests/test_plugin_wiring.py`:

```python
"""Plugin hookimpls — registration + wiring + isolation."""

from __future__ import annotations

from agent_core_voice import plugin as voice_plugin
from agent_core_voice.endpoint import VoiceEndpoint


def test_register_endpoint_types() -> None:
    types = voice_plugin.register_endpoint_types()
    assert types == {"builtin.voice": VoiceEndpoint}


def test_reserved_endpoint_params() -> None:
    reserved = voice_plugin.reserved_endpoint_params()
    assert set(reserved) == {"voice", "voice_id"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_plugin_wiring.py -v`

Expected: FAIL with `ImportError: No module named 'agent_core_voice.plugin'`.

- [ ] **Step 3: Implement the two simple hookimpls**

Create `packages/agent-core-voice/src/agent_core_voice/plugin.py`:

```python
"""Agent_core entry-point hook surface for the voice service.

Three hookimpls:

* ``register_endpoint_types`` — exposes ``builtin.voice`` so the bus
  runner can construct a ``VoiceEndpoint`` from a yaml entry.
* ``reserved_endpoint_params`` — declares ``voice`` and ``voice_id`` so
  the runner pops them from claude_code_mcp's params before constructing.
* ``wire_endpoints_after_registration`` — for each
  ``ClaudeCodeMCPEndpoint`` whose yaml params name a voice, validate
  the voice exists and append a deferred mounter that registers the
  two voice tools on the FastMCP server with ``voice_id`` closed in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    from agent_core.bus.protocol import Endpoint
    from agent_core.plugins.specs import RunnerServices

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict[str, type[Any]]:
    """Register ``builtin.voice`` as a bus endpoint type."""
    from agent_core_voice.endpoint import VoiceEndpoint

    return {"builtin.voice": VoiceEndpoint}


@hookimpl
def reserved_endpoint_params() -> list[str]:
    """The runner pops these keys from each endpoint's params before constructing."""
    return ["voice", "voice_id"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_plugin_wiring.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/plugin.py \
        packages/agent-core-voice/tests/test_plugin_wiring.py
git commit -m "feat(voice): plugin register_endpoint_types + reserved_endpoint_params"
```

---

## Task 10: Plugin wire_endpoints_after_registration + isolation guarantee

**Files:**
- Modify: `packages/agent-core-voice/src/agent_core_voice/plugin.py`
- Modify: `packages/agent-core-voice/tests/test_plugin_wiring.py`

- [ ] **Step 1: Write failing wiring tests**

Append to `packages/agent-core-voice/tests/test_plugin_wiring.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.protocol import VoiceInfo


class _FakeClaudeCodeMCP:
    """Stand-in for ClaudeCodeMCPEndpoint — only needs ._mcp and .deferred_tool_mounters."""

    def __init__(self) -> None:
        self._mcp = FastMCP(name="agent")
        self.deferred_tool_mounters: list = []


@pytest.fixture
def two_agents(tmp_path: Path, ref_wav: Path):
    voice = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={
            "alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="ra"),
            "bob":   VoiceInfo(voice_id="bob",   ref_wav=ref_wav, ref_text="rb"),
        },
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    alice_mcp = _FakeClaudeCodeMCP()
    bob_mcp = _FakeClaudeCodeMCP()
    return voice, alice_mcp, bob_mcp


def _patch_isinstance_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the plugin's `isinstance(_, ClaudeCodeMCPEndpoint)` accept our fake."""
    import agent_core_voice.plugin as plugin_mod

    fake_cls = _FakeClaudeCodeMCP
    monkeypatch.setattr(
        plugin_mod,
        "_resolve_claude_code_mcp_cls",
        lambda: fake_cls,
        raising=False,
    )


def test_wire_happy_path_appends_mounter(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    voice, alice_mcp, bob_mcp = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp, "bob": bob_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "alice"}},
        "bob":   {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "bob"}},
    }
    voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())

    assert len(alice_mcp.deferred_tool_mounters) == 1
    assert len(bob_mcp.deferred_tool_mounters) == 1


def test_wire_unknown_voice_raises(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    voice, alice_mcp, _ = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "nope"}},
    }
    with pytest.raises(ValueError) as exc:
        voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())
    assert "nope" in str(exc.value)
    assert "alice" in str(exc.value) or "bob" in str(exc.value)


def test_wire_missing_voice_endpoint_raises(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    _, alice_mcp, _ = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"alice": alice_mcp}
    raw = {
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "alice"}},
    }
    with pytest.raises(ValueError) as exc:
        voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())
    assert "voice" in str(exc.value)


def test_wire_omitted_voice_param_skips_mount(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    voice, alice_mcp, _ = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {}},  # no voice/voice_id
    }
    voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())
    assert alice_mcp.deferred_tool_mounters == []


@pytest.mark.asyncio
async def test_mounter_closure_binds_per_agent(
    two_agents, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the mounter runs, alice's MCP exposes ONLY alice's voice via voice_info."""
    voice, alice_mcp, bob_mcp = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp, "bob": bob_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "alice"}},
        "bob":   {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "bob"}},
    }
    voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())

    # Run each mounter (the bus_handle arg is unused by the voice mounter).
    alice_mcp.deferred_tool_mounters[0](bus_handle=None)
    bob_mcp.deferred_tool_mounters[0](bus_handle=None)

    alice_tools = await alice_mcp._mcp.get_tools()
    bob_tools = await bob_mcp._mcp.get_tools()

    import json as _json

    a_info_blocks = await alice_tools["voice_info"].run({})
    b_info_blocks = await bob_tools["voice_info"].run({})
    a_info = _json.loads(
        (a_info_blocks.content if hasattr(a_info_blocks, "content") else a_info_blocks)[0].text
    )
    b_info = _json.loads(
        (b_info_blocks.content if hasattr(b_info_blocks, "content") else b_info_blocks)[0].text
    )
    assert a_info["voice_id"] == "alice"
    assert b_info["voice_id"] == "bob"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-voice/tests/test_plugin_wiring.py -v`

Expected: 5 new tests FAIL — `wire_endpoints_after_registration` not yet implemented.

- [ ] **Step 3: Implement the wiring hookimpl**

Append to `packages/agent-core-voice/src/agent_core_voice/plugin.py`:

```python
def _resolve_claude_code_mcp_cls() -> type[Any]:
    """Lazy import; overridable by tests via monkeypatch."""
    from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint

    return ClaudeCodeMCPEndpoint


@hookimpl
def wire_endpoints_after_registration(
    endpoints: "dict[str, Endpoint]",
    raw_endpoint_configs: dict[str, dict[str, Any]],
    services: "RunnerServices",
) -> None:
    """Mount voice tools on every MCP endpoint that names a voice + voice_id."""
    del services  # unused — kept for hookspec parity

    from agent_core_voice.endpoint import VoiceEndpoint
    from agent_core_voice.mcp import register_voice_tools

    claude_code_mcp_cls = _resolve_claude_code_mcp_cls()

    for name, endpoint in endpoints.items():
        if not isinstance(endpoint, claude_code_mcp_cls):
            continue
        raw = raw_endpoint_configs.get(name) or {}
        params = raw.get("params") or {}
        voice_name = params.get("voice")
        voice_id = params.get("voice_id")

        if not voice_name and not voice_id:
            continue
        if not voice_name or not voice_id:
            raise ValueError(
                f"endpoint {name!r} sets one of 'voice'/'voice_id' but not both; "
                f"got voice={voice_name!r}, voice_id={voice_id!r}"
            )

        voice_ep = endpoints.get(voice_name)
        if voice_ep is None:
            available = sorted(n for n, e in endpoints.items() if isinstance(e, VoiceEndpoint))
            raise ValueError(
                f"endpoint {name!r} names voice={voice_name!r} but no endpoint with that "
                f"name is registered. Available VoiceEndpoint names: {available}"
            )
        if not isinstance(voice_ep, VoiceEndpoint):
            available = sorted(n for n, e in endpoints.items() if isinstance(e, VoiceEndpoint))
            raise ValueError(
                f"endpoint {name!r} names voice={voice_name!r}, but that endpoint is a "
                f"{type(voice_ep).__name__}, not a VoiceEndpoint. "
                f"Available VoiceEndpoint names: {available}"
            )
        if voice_id not in voice_ep.voice_ids():
            available = sorted(voice_ep.voice_ids())
            raise ValueError(
                f"endpoint {name!r} requests voice_id={voice_id!r} but voice endpoint "
                f"{voice_name!r} has no such voice. Available voice ids: {available}"
            )

        def _mounter(
            bus_handle,
            *,
            voice_ep: VoiceEndpoint = voice_ep,
            mcp_endpoint=endpoint,
            voice_id: str = voice_id,
            agent_name: str = name,
        ) -> None:
            del bus_handle  # voice tools don't publish onto the bus
            register_voice_tools(
                mcp=mcp_endpoint._mcp,
                endpoint=voice_ep,
                voice_id=voice_id,
                agent_name=agent_name,
            )

        endpoint.deferred_tool_mounters.append(_mounter)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-voice/tests/test_plugin_wiring.py -v`

Expected: 7 passed (2 from Task 9 + 5 here).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/plugin.py \
        packages/agent-core-voice/tests/test_plugin_wiring.py
git commit -m "feat(voice): plugin wire_endpoints_after_registration with isolation"
```

---

## Task 11: QwenTTSBackend skeleton (lazy torch import, manual smoke only)

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/qwen_backend.py`
- Create: `packages/agent-core-voice/SMOKE_TEST.md`

There is no automated test for this task — CI doesn't have a GPU and we don't want to install torch in the bus daemon's base venv. The contract is verified by the protocol tests (Task 2) and a local smoke test.

- [ ] **Step 1: Implement QwenTTSBackend**

Create `packages/agent-core-voice/src/agent_core_voice/qwen_backend.py`:

```python
"""Real Qwen3-TTS backend with in-context-learning voice cloning.

Torch and qwen_tts are lazy-imported inside ``__init__`` so the rest of
the package (protocol, fake, endpoint, mcp, plugin) is importable on
hosts where torch hasn't been installed (e.g., CI runners using the cpu
extra without GPU, or any host running only the unit tests).

Never used by tests. Production wiring constructs this class with the
yaml params; the bus runner is responsible for instantiating it via
``register_endpoint_types``.
"""

from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import soundfile as sf

from agent_core_voice.protocol import (
    EmptyTextError,
    GPUOOMError,
    TTSBackend,
    VoiceNotPreparedError,
)

log = logging.getLogger(__name__)


class QwenTTSBackend:
    """Real backend: loads Qwen3-TTS once, holds ICL prompts per voice."""

    def __init__(
        self,
        *,
        model_path: str,
        device: str = "cuda:0",
        attn_implementation: str = "sdpa",
    ) -> None:
        # Lazy imports — torch and qwen_tts only required for production.
        import torch  # noqa: PLC0415
        from qwen_tts import Qwen3TTSModel  # noqa: PLC0415

        self._torch = torch
        self._device = device
        log.info(
            "loading Qwen3-TTS: model_path=%s device=%s attn=%s",
            model_path,
            device,
            attn_implementation,
        )
        self._model = Qwen3TTSModel.from_pretrained(
            model_path,
            device_map=device,
            torch_dtype=torch.bfloat16,
            attn_implementation=attn_implementation,
        )
        self._prompts: dict[str, Any] = {}

    def prepare_voice(self, voice_id: str, ref_wav: Path, ref_text: str) -> None:
        ref_wav = Path(ref_wav)
        if not ref_wav.exists():
            raise FileNotFoundError(f"ref_wav not found: {ref_wav}")
        prompt = self._model.create_voice_clone_prompt(
            ref_audio=str(ref_wav),
            ref_text=ref_text,
        )
        self._prompts[voice_id] = prompt
        log.info("voice %r prepared", voice_id)

    def synthesize(self, voice_id: str, text: str, seed: int) -> tuple[bytes, float]:
        if voice_id not in self._prompts:
            raise VoiceNotPreparedError(f"voice {voice_id!r} not prepared")
        if not text or not text.strip():
            raise EmptyTextError("text is empty")

        self._torch.manual_seed(seed)
        start = time.monotonic()
        try:
            wavs, sr = self._model.generate_voice_clone(
                text=text,
                language="english",
                voice_clone_prompt=self._prompts[voice_id],
            )
        except RuntimeError as exc:
            if "CUDA out of memory" in str(exc) or "OutOfMemoryError" in type(exc).__name__:
                raise GPUOOMError(str(exc)) from exc
            raise

        generation_s = time.monotonic() - start

        buf = BytesIO()
        sf.write(buf, wavs[0], int(sr), format="WAV", subtype="PCM_16")
        return buf.getvalue(), generation_s


__all__ = ["QwenTTSBackend"]
```

- [ ] **Step 2: Wire QwenTTSBackend into VoiceEndpoint's production path**

In `packages/agent-core-voice/src/agent_core_voice/endpoint.py`, add an alternate `__init__` signature that accepts the yaml params and constructs `QwenTTSBackend` internally. Replace the existing `__init__` with:

```python
    def __init__(
        self,
        *,
        name: str,
        backend: TTSBackend | None = None,
        voices: dict[str, VoiceInfo] | dict[str, dict] | None = None,
        output_dir: Path | str,
        audit_path: Path | str,
        # Real-backend params, only used when backend is None:
        model_path: str | None = None,
        device: str = "cuda:0",
        attn_implementation: str = "sdpa",
    ) -> None:
        self._name = name

        if backend is None:
            if model_path is None:
                raise ValueError(
                    "VoiceEndpoint requires either backend=... (tests) or "
                    "model_path=... (production with QwenTTSBackend)"
                )
            from agent_core_voice.qwen_backend import QwenTTSBackend  # noqa: PLC0415

            backend = QwenTTSBackend(
                model_path=model_path,
                device=device,
                attn_implementation=attn_implementation,
            )
        self._backend = backend

        # Normalize voices: yaml gives dict[str, dict]; tests give dict[str, VoiceInfo].
        normalized: dict[str, VoiceInfo] = {}
        for vid, raw in (voices or {}).items():
            if isinstance(raw, VoiceInfo):
                normalized[vid] = raw
            else:
                normalized[vid] = VoiceInfo(
                    voice_id=vid,
                    ref_wav=Path(raw["ref_wav"]),
                    ref_text=raw["ref_text"],
                    blend=raw.get("blend"),
                )
        self._voices = normalized

        self._output_dir = Path(output_dir)
        self._audit = AuditLog(Path(audit_path))
        self._output_dir.mkdir(parents=True, exist_ok=True)

        for voice_id, info in self._voices.items():
            self._backend.prepare_voice(voice_id, Path(info.ref_wav), info.ref_text)
            log.info("voice %r prepared (ref_wav=%s)", voice_id, info.ref_wav)
```

- [ ] **Step 3: Run all tests to make sure the dual signature still passes the fake path**

Run: `uv run pytest packages/agent-core-voice/tests/ -v`

Expected: all green.

- [ ] **Step 4: Write smoke test instructions**

Create `packages/agent-core-voice/SMOKE_TEST.md`:

```markdown
# agent-core-voice — local smoke test

Runs on the GPU host with CUDA installed. Not in CI.

## Setup

```powershell
cd E:\workspaces\ai\agents\agent_core
uv sync --extra cu130
```

## Smoke script

```python
# scripts/voice_smoke.py
import asyncio
from pathlib import Path

from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.protocol import VoiceInfo

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
        "Pepper here. Just checking in.",
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
```

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/qwen_backend.py \
        packages/agent-core-voice/src/agent_core_voice/endpoint.py \
        packages/agent-core-voice/SMOKE_TEST.md
git commit -m "feat(voice): QwenTTSBackend skeleton + endpoint production path"
```

---

## Task 12: Workspace lint / type / full test sweep

**Files:**
- Verify only — no new files.

- [ ] **Step 1: Run full ruff lint across the new package**

Run: `uv run ruff check packages/agent-core-voice/`

Expected: clean. Fix any findings (typically import order, unused imports).

- [ ] **Step 2: Run ruff format check**

Run: `uv run ruff format --check packages/agent-core-voice/`

Expected: clean. If not, run `uv run ruff format packages/agent-core-voice/` and commit the format-only change as `style(voice): ruff format`.

- [ ] **Step 3: Run full test suite for the package**

Run: `uv run pytest packages/agent-core-voice/tests/ -v`

Expected: all tests in `test_protocol.py`, `test_fake_backend.py`, `test_audit.py`, `test_endpoint.py`, `test_mcp_tools.py`, `test_plugin_wiring.py` pass.

- [ ] **Step 4: Run the whole workspace test suite to make sure we didn't break anyone**

Run: `uv run pytest`

Expected: all green. If a webcam/briefs/discord test fails, that's a real regression we caused — don't paper over it.

- [ ] **Step 5: Commit any lint/format fixes**

```bash
# Only if there were fixes in Step 1 or 2:
git add packages/agent-core-voice/
git commit -m "style(voice): ruff lint/format fixes"
```

---

## Task 13: End-to-end yaml smoke (manual, on GPU host)

This is the final acceptance gate. Run on the GPU host after Tasks 1–12 are green.

- [ ] **Step 1: Install with CUDA extra**

```powershell
cd E:\workspaces\ai\agents\agent_core
uv sync --extra cu130
```

Expected: completes; the CUDA-13 torch wheels download from `pytorch-cu130`.

- [ ] **Step 2: Create a minimal test-agent yaml**

Create a scratch yaml (path of your choice; not committed) referencing a fresh test agent — NOT Pepper's live config, per [[project_pepper_hands_off_until_proven]]:

```yaml
endpoints:
  voice:
    type: builtin.voice
    params:
      model_path: C:\workspaces\ai\Qwen3-TTS-EasyFinetuning\models\Qwen\Qwen3-TTS-12Hz-1.7B-Base
      device: cuda:0
      attn_implementation: sdpa
      output_dir: E:\agent_core\voice_out
      audit_path: E:\agent_core\voice_out\audit.jsonl
      voices:
        test_agent:
          ref_wav: C:\workspaces\ai\voices2\blends\custom_S70_C20_G10.wav
          ref_text: "<canonical ref text from voices2 wiki>"
          blend: "Scarlett 70 / Charlize 20 / Gwyneth 10"

  test_agent_mcp:
    type: builtin.claude_code_mcp
    params:
      mount: /mcp/test_agent
      voice: voice
      voice_id: test_agent
```

- [ ] **Step 3: Run the smoke script from Task 11**

```powershell
uv run python scripts/voice_smoke.py
```

Walk the acceptance checklist in `SMOKE_TEST.md`:

1. Startup time < 60 s.
2. Per-call latency 8–15 s.
3. Three wavs land under `./voice_smoke_out/test/<today>/`.
4. Subjective listen: it's the configured reference voice, not Qwen3-TTS default.
5. `audit.jsonl` has three success lines with the documented schema.

If all 5 pass, the service is ready for a real test-agent rollout (separate, not in this plan).

- [ ] **Step 4: Document the result**

Append a single paragraph to `packages/agent-core-voice/SMOKE_TEST.md` recording the date, host, CUDA version, attn backend, and observed latencies. This becomes the baseline for future regressions.

```bash
git add packages/agent-core-voice/SMOKE_TEST.md
git commit -m "docs(voice): record smoke-test baseline"
```

---

## Spec coverage check

| Spec section | Implemented in |
|---|---|
| Package shape (`packages/agent-core-voice/`) | Task 1 |
| Dependency strategy (uv extras + conflicts) | Task 1 |
| qwen-tts source decision | Task 1 (Step 1 probe + `QWEN_TTS_SOURCE.md`) |
| Backend protocol + error taxonomy | Task 2 |
| FakeTTSBackend (test-only) | Task 3 |
| Audit log | Task 4 |
| VoiceEndpoint construction + voice registry | Task 5 |
| `synthesize_safe` happy path + service-owned paths | Task 6 |
| Error mapping + audit on failure | Task 7 |
| MCP tool surface (`synthesize_speech`, `voice_info`, no `voice_id` arg) | Task 8 |
| Plugin `register_endpoint_types` + `reserved_endpoint_params` | Task 9 |
| Plugin `wire_endpoints_after_registration` + isolation enforcement | Task 10 |
| QwenTTSBackend (real, lazy torch import) + production endpoint path | Task 11 |
| Lint + format + cross-package test sweep | Task 12 |
| Smoke acceptance on GPU host | Task 13 |
| Out-of-scope items (streaming, hot reload, etc.) | Honored by absence; no task touches them |
| Pepper hands-off | Task 13 step 2 (test_agent yaml, not Pepper's) |
