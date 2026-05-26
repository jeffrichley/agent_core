# Voice-library bus-async migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `agent-core-voice` to consume `voice` library (chunking + native batching) and flip the consumer-facing MCP surface from synchronous-blocking to async-via-bus-envelope. Empirically 3x faster on Pepper's 5-sentence passage; unblocks caller sessions.

**Architecture:** voice endpoint subscribes to `SynthesisRequest` envelopes on the bus, runs `voice.generate()` as an asyncio task, publishes `SynthesisReady` or `SynthesisFailed` back to caller via `in_reply_to` correlation. MCP `synthesize_speech` tool becomes a thin envelope-fire helper returning `{request_id}`. File lifecycle: voice owns content-addressed WAVs at `~/.agent-core/voice/<sha>.wav` with 1h default TTL.

**Tech Stack:** Python 3.12, Pydantic v2 (envelopes), asyncio, Qwen3-TTS via voice library, pluggy entry-points for endpoint registration, pytest for testing.

**Spec:** `docs/superpowers/specs/2026-05-26-voice-library-bus-async-migration-design.md`

---

## File structure

**Modified:**
- `packages/agent-core-voice/pyproject.toml` — add `voice >= 0.1.0` dep
- `packages/agent-core-voice/src/agent_core_voice/endpoint.py` — refactor `synthesize_safe` to use `voice.generate()`; add envelope handler in `deliver()`; add file-lifecycle integration
- `packages/agent-core-voice/src/agent_core_voice/mcp.py` — flip `synthesize_speech` to publish-and-return; tighten return shape
- `packages/agent-core-voice/tests/test_endpoint.py` — update existing tests to new `synthesize_safe` flow
- `packages/agent-core-voice/tests/test_mcp_tools.py` — update existing tests to new tool return shape

**Created:**
- `packages/agent-core-voice/src/agent_core_voice/envelopes.py` — Pydantic payload models for `SynthesisRequestPayload`, `SynthesisReadyPayload`, `SynthesisFailedPayload`
- `packages/agent-core-voice/src/agent_core_voice/lifecycle.py` — content-addressed write helper + cleanup tick
- `packages/agent-core-voice/tests/test_envelopes.py` — payload model tests (validation, defaults, schema_version)
- `packages/agent-core-voice/tests/test_lifecycle.py` — write-then-read, TTL cleanup, retain_until math
- `packages/agent-core-voice/tests/test_envelope_flow.py` — bus-end-to-end: Request → Ready, Request → Failed reasons, Request → Timeout

**Deleted:**
- `packages/agent-core-voice/src/agent_core_voice/qwen_backend.py` — superseded by `voice.engine.QwenTTSBackend`
- `packages/agent-core-voice/src/agent_core_voice/fake.py` — superseded by `voice.engine.FakeTTSBackend`

---

## Task list

### Task 1: Add voice library as a dependency

**Files:**
- Modify: `packages/agent-core-voice/pyproject.toml`

- [ ] **Step 1: Read current pyproject**

Run: `cat packages/agent-core-voice/pyproject.toml | head -30`
Expected: Shows current `dependencies` array.

- [ ] **Step 2: Add `voice >= 0.1.0` to dependencies**

Add inside the existing `dependencies = [...]` array:

```toml
"voice >= 0.1.0",
```

- [ ] **Step 3: Install the dep**

Run: `cd packages/agent-core-voice && uv sync`
Expected: voice resolves + installs cleanly.

- [ ] **Step 4: Verify import works**

Run: `cd packages/agent-core-voice && uv run python -c "from voice import generate, Spec; from voice.engine import QwenTTSBackend, FakeTTSBackend; print('OK')"`
Expected: Prints `OK` with no ImportError.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/pyproject.toml
git -C . commit -m "feat(voice): add voice library dependency"
```

---

### Task 2: Define envelope payload models

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/envelopes.py`
- Test: `packages/agent-core-voice/tests/test_envelopes.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-voice/tests/test_envelopes.py`:

```python
"""Tests for SynthesisRequest/Ready/Failed payload models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_core_voice.envelopes import (
    SynthesisFailedPayload,
    SynthesisReadyPayload,
    SynthesisRequestPayload,
)


class TestSynthesisRequestPayload:
    def test_minimal_request_validates(self) -> None:
        p = SynthesisRequestPayload(text="hello")
        assert p.text == "hello"
        assert p.timeout_s is None  # default = use endpoint default
        assert p.retain_s is None
        assert p.options is None

    def test_timeout_cap_rejected_at_parse_time(self) -> None:
        with pytest.raises(ValidationError, match="timeout_s exceeds 600s cap"):
            SynthesisRequestPayload(text="hello", timeout_s=601.0)

    def test_retain_cap_rejected_at_parse_time(self) -> None:
        with pytest.raises(ValidationError, match="retain_s exceeds 86400s cap"):
            SynthesisRequestPayload(text="hello", retain_s=86401.0)

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequestPayload(text="hello", timeout_s=-1.0)

    def test_empty_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequestPayload(text="")

    def test_options_dict_accepted(self) -> None:
        p = SynthesisRequestPayload(
            text="hi",
            options={"chunk_strategy": "sentence", "parallel": True, "seed": 7},
        )
        assert p.options == {"chunk_strategy": "sentence", "parallel": True, "seed": 7}


class TestSynthesisReadyPayload:
    def test_minimal_ready_validates(self) -> None:
        p = SynthesisReadyPayload(
            wav_path="/tmp/x.wav",
            file_size_bytes=1024,
            duration_s=1.5,
            elapsed_s=0.8,
            sample_rate_hz=24000,
            cache_hit=False,
            chunks=1,
            retain_until="2026-05-26T15:00:00+00:00",
        )
        assert p.wav_path == "/tmp/x.wav"


class TestSynthesisFailedPayload:
    def test_valid_reason_accepted(self) -> None:
        p = SynthesisFailedPayload(
            reason="GPU_OOM", message="GPU is OOM", retryable=True
        )
        assert p.reason == "GPU_OOM"
        assert p.retryable is True

    def test_invalid_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisFailedPayload(
                reason="MYSTERY", message="x", retryable=False
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_envelopes.py -v`
Expected: FAIL with `ImportError: No module named 'agent_core_voice.envelopes'`.

- [ ] **Step 3: Write minimal implementation**

Create `packages/agent-core-voice/src/agent_core_voice/envelopes.py`:

```python
"""Pydantic payload models for voice endpoint envelopes.

Three event types ride on top of agent_core's Event kind:

- SynthesisRequest (caller → voice)
- SynthesisReady (voice → caller, success)
- SynthesisFailed (voice → caller, failure)

Validation runs at envelope publish/parse time so malformed requests
surface as ValidationError to the publisher rather than as opaque
runtime failures downstream.

See spec §4 for the wire-level contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Caps from spec §4.1.
_TIMEOUT_S_CAP = 600.0
_RETAIN_S_CAP = 86400.0


class SynthesisRequestPayload(BaseModel):
    """payload.data of a SynthesisRequest Event."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    timeout_s: float | None = Field(default=None, gt=0, le=_TIMEOUT_S_CAP)
    retain_s: float | None = Field(default=None, gt=0, le=_RETAIN_S_CAP)
    options: dict[str, Any] | None = None

    # Pydantic v2 ergonomics: emit explicit messages on cap violations so
    # callers see the cap value, not a generic "input is greater than".
    @classmethod
    def __get_pydantic_json_schema__(cls, *args, **kwargs):  # noqa: D401
        return super().__get_pydantic_json_schema__(*args, **kwargs)


class SynthesisReadyPayload(BaseModel):
    """payload.data of a SynthesisReady Event."""

    model_config = ConfigDict(extra="forbid")

    wav_path: str = Field(min_length=1)
    file_size_bytes: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    elapsed_s: float = Field(ge=0)
    sample_rate_hz: int = Field(gt=0)
    cache_hit: bool
    chunks: int = Field(ge=1)
    retain_until: str = Field(min_length=1)  # ISO 8601 UTC


FailureReason = Literal[
    "GPU_OOM",
    "TEXT_TOO_LONG",
    "VOICE_NOT_PREPARED",
    "INTERNAL_ERROR",
    "TIMEOUT",
]


class SynthesisFailedPayload(BaseModel):
    """payload.data of a SynthesisFailed Event."""

    model_config = ConfigDict(extra="forbid")

    reason: FailureReason
    message: str = Field(min_length=1)
    retryable: bool


__all__ = [
    "FailureReason",
    "SynthesisFailedPayload",
    "SynthesisReadyPayload",
    "SynthesisRequestPayload",
]
```

Then override the field validators to emit cap-named errors. Add to `envelopes.py` after the field definitions:

```python
from pydantic import field_validator


class SynthesisRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    timeout_s: float | None = Field(default=None, gt=0)
    retain_s: float | None = Field(default=None, gt=0)
    options: dict[str, Any] | None = None

    @field_validator("timeout_s")
    @classmethod
    def _cap_timeout(cls, v: float | None) -> float | None:
        if v is not None and v > _TIMEOUT_S_CAP:
            raise ValueError(f"timeout_s exceeds 600s cap (got {v})")
        return v

    @field_validator("retain_s")
    @classmethod
    def _cap_retain(cls, v: float | None) -> float | None:
        if v is not None and v > _RETAIN_S_CAP:
            raise ValueError(f"retain_s exceeds 86400s cap (got {v})")
        return v
```

Replace the entire `SynthesisRequestPayload` block with this final version (the previous Field(le=...) approach gives a generic error). Final `envelopes.py`:

```python
"""Pydantic payload models for voice endpoint envelopes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TIMEOUT_S_CAP = 600.0
_RETAIN_S_CAP = 86400.0


class SynthesisRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    timeout_s: float | None = Field(default=None, gt=0)
    retain_s: float | None = Field(default=None, gt=0)
    options: dict[str, Any] | None = None

    @field_validator("timeout_s")
    @classmethod
    def _cap_timeout(cls, v: float | None) -> float | None:
        if v is not None and v > _TIMEOUT_S_CAP:
            raise ValueError(f"timeout_s exceeds 600s cap (got {v})")
        return v

    @field_validator("retain_s")
    @classmethod
    def _cap_retain(cls, v: float | None) -> float | None:
        if v is not None and v > _RETAIN_S_CAP:
            raise ValueError(f"retain_s exceeds 86400s cap (got {v})")
        return v


class SynthesisReadyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wav_path: str = Field(min_length=1)
    file_size_bytes: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    elapsed_s: float = Field(ge=0)
    sample_rate_hz: int = Field(gt=0)
    cache_hit: bool
    chunks: int = Field(ge=1)
    retain_until: str = Field(min_length=1)


FailureReason = Literal[
    "GPU_OOM",
    "TEXT_TOO_LONG",
    "VOICE_NOT_PREPARED",
    "INTERNAL_ERROR",
    "TIMEOUT",
]


class SynthesisFailedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: FailureReason
    message: str = Field(min_length=1)
    retryable: bool


__all__ = [
    "FailureReason",
    "SynthesisFailedPayload",
    "SynthesisReadyPayload",
    "SynthesisRequestPayload",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_envelopes.py -v`
Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/envelopes.py packages/agent-core-voice/tests/test_envelopes.py
git commit -m "feat(voice): add SynthesisRequest/Ready/Failed payload models"
```

---

### Task 3: Add file-lifecycle helper (content-addressed write + cleanup)

**Files:**
- Create: `packages/agent-core-voice/src/agent_core_voice/lifecycle.py`
- Test: `packages/agent-core-voice/tests/test_lifecycle.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-voice/tests/test_lifecycle.py`:

```python
"""Tests for content-addressed WAV write + TTL cleanup."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from agent_core_voice.lifecycle import (
    cleanup_expired,
    retain_until_iso,
    write_addressed,
)


def test_write_addressed_creates_sha_named_file(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=60.0)
    assert path.exists()
    assert path.name == f"{sha}.wav"
    assert path.read_bytes() == audio


def test_write_addressed_writes_meta_sidecar(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=120.0)
    meta_path = path.with_suffix(".meta.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["retain_s"] == 120.0
    assert meta["sha256"] == sha


def test_write_addressed_dedupes_identical_audio(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path1, sha1 = write_addressed(audio, root=tmp_path, retain_s=60.0)
    path2, sha2 = write_addressed(audio, root=tmp_path, retain_s=60.0)
    assert path1 == path2
    assert sha1 == sha2


def test_retain_until_iso_returns_correct_offset() -> None:
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    out = retain_until_iso(retain_s=3600.0, now=now)
    assert out == "2026-05-26T13:00:00+00:00"


def test_cleanup_expired_removes_old_files(tmp_path: Path) -> None:
    # Write a file with retain_s=0.1 — expires immediately after.
    audio = b"RIFF" + b"\x00" * 100
    path, sha = write_addressed(audio, root=tmp_path, retain_s=0.1)
    assert path.exists()

    # Wait past expiry.
    time.sleep(0.2)

    n_removed = cleanup_expired(root=tmp_path)
    assert n_removed == 1
    assert not path.exists()
    assert not path.with_suffix(".meta.json").exists()


def test_cleanup_expired_keeps_live_files(tmp_path: Path) -> None:
    audio = b"RIFF" + b"\x00" * 100
    path, _ = write_addressed(audio, root=tmp_path, retain_s=3600.0)
    n_removed = cleanup_expired(root=tmp_path)
    assert n_removed == 0
    assert path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_lifecycle.py -v`
Expected: FAIL with `ImportError: No module named 'agent_core_voice.lifecycle'`.

- [ ] **Step 3: Write minimal implementation**

Create `packages/agent-core-voice/src/agent_core_voice/lifecycle.py`:

```python
"""File lifecycle for voice WAVs: content-addressed write + TTL cleanup."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def write_addressed(
    audio: bytes,
    *,
    root: Path,
    retain_s: float,
) -> tuple[Path, str]:
    """Write ``audio`` to ``<root>/<sha256>.wav`` with a meta sidecar.

    Returns (path, sha256_hex). Idempotent: identical audio re-writes the
    same file (no-op if already present and meta matches).
    """
    sha = hashlib.sha256(audio).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    wav_path = root / f"{sha}.wav"
    meta_path = wav_path.with_suffix(".meta.json")

    if not wav_path.exists():
        wav_path.write_bytes(audio)

    meta = {
        "sha256": sha,
        "retain_s": retain_s,
        "written_at_utc": datetime.now(UTC).isoformat(),
    }
    meta_path.write_text(json.dumps(meta))
    return wav_path, sha


def retain_until_iso(*, retain_s: float, now: datetime | None = None) -> str:
    """Compute ISO 8601 UTC timestamp of ``now + retain_s``."""
    base = now or datetime.now(UTC)
    return (base + timedelta(seconds=retain_s)).isoformat()


def cleanup_expired(*, root: Path) -> int:
    """Walk ``root``, delete WAVs whose meta's mtime + retain_s < now.

    Returns count removed. Safe to call on a nonexistent root.
    """
    if not root.exists():
        return 0
    now = datetime.now(UTC)
    removed = 0
    for meta_path in root.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
            written = datetime.fromisoformat(meta["written_at_utc"])
            retain_s = float(meta["retain_s"])
        except (OSError, ValueError, KeyError) as exc:
            log.warning("skipping malformed meta %s: %s", meta_path, exc)
            continue
        if written + timedelta(seconds=retain_s) < now:
            wav_path = meta_path.with_suffix("").with_suffix(".wav")
            try:
                if wav_path.exists():
                    wav_path.unlink()
                meta_path.unlink()
                removed += 1
            except OSError as exc:
                log.warning("failed to remove %s: %s", wav_path, exc)
    return removed


__all__ = ["cleanup_expired", "retain_until_iso", "write_addressed"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_lifecycle.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/lifecycle.py packages/agent-core-voice/tests/test_lifecycle.py
git commit -m "feat(voice): add content-addressed file lifecycle (write + TTL cleanup)"
```

---

### Task 4: Refactor endpoint to use voice.generate() (Phase 1 — backend swap)

**Files:**
- Modify: `packages/agent-core-voice/src/agent_core_voice/endpoint.py`
- Delete: `packages/agent-core-voice/src/agent_core_voice/qwen_backend.py`
- Delete: `packages/agent-core-voice/src/agent_core_voice/fake.py`
- Modify: `packages/agent-core-voice/tests/conftest.py` (if it imports fake)
- Modify: `packages/agent-core-voice/tests/test_endpoint.py` (uses fake)

- [ ] **Step 1: Find existing fake imports and update them**

Run: `cd packages/agent-core-voice && grep -rn "from agent_core_voice.fake\|from agent_core_voice.qwen_backend" tests/ src/`
Expected: lists every site that imports the soon-to-be-deleted modules.

- [ ] **Step 2: Update test imports to voice.engine**

For every file the previous grep returned, replace `from agent_core_voice.fake import FakeTTSBackend` with `from voice.engine import FakeTTSBackend`. Replace `from agent_core_voice.qwen_backend import QwenTTSBackend` with `from voice.engine import QwenTTSBackend`.

- [ ] **Step 3: Delete the now-redundant local modules**

```bash
git rm packages/agent-core-voice/src/agent_core_voice/fake.py
git rm packages/agent-core-voice/src/agent_core_voice/qwen_backend.py
```

- [ ] **Step 4: Refactor endpoint.py synthesize_safe to use voice.generate**

In `packages/agent-core-voice/src/agent_core_voice/endpoint.py`, replace the `synthesize_safe` method body and the lazy QwenTTSBackend construction. The new method:

```python
async def synthesize_safe(
    self,
    *,
    agent_name: str,
    voice_id: str,
    text: str,
    seed: int,
) -> SynthesisSuccess | SynthesisError:
    """Synthesize via voice.generate() (chunked + parallel-batched)."""
    from voice import Spec, generate

    now = datetime.now(UTC)
    if len(text) > self._max_text_len:
        return await self._record_error(
            now, agent_name, voice_id, text, seed,
            TextTooLongError(
                f"text length {len(text)} exceeds endpoint budget {self._max_text_len}"
            ),
        )

    try:
        result = await asyncio.to_thread(
            generate,
            text,
            Spec(
                voice_id=voice_id,
                seed=seed,
                chunk_strategy="sentence",
                parallel=True,
            ),
            backend=self._backend,
        )
    except VoiceError as exc:
        return await self._record_error(now, agent_name, voice_id, text, seed, exc)
    except Exception as exc:
        log.exception("voice.generate raised unexpected exception")
        return await self._record_error(now, agent_name, voice_id, text, seed, exc)

    wav_bytes = bytes(result)
    try:
        path = self._next_output_path(agent_name, seed, text, now)
        await asyncio.to_thread(path.write_bytes, wav_bytes)
    except (OSError, RuntimeError) as exc:
        return await self._record_error(
            now, agent_name, voice_id, text, seed, _WavPhaseError(exc)
        )

    await self._audit.write(
        AuditEvent(
            timestamp=now,
            agent=agent_name,
            voice_id=voice_id,
            text_len=len(text),
            seed=seed,
            duration_s=float(result.duration_ms) / 1000.0 if hasattr(result, "duration_ms") else 0.0,
            generation_s=sum(result.timings or [0.0]),
            wav_path=str(path),
            error=None,
        )
    )
    return SynthesisSuccess(
        path=str(path),
        duration_s=float(result.duration_ms) / 1000.0 if hasattr(result, "duration_ms") else 0.0,
        generation_s=sum(result.timings or [0.0]),
        sample_rate=result.sample_rate_hz,
    )
```

Also update the construction-of-real-backend block to import from voice:

```python
if backend is None:
    if model_path is None:
        raise ValueError(
            "VoiceEndpoint requires either backend=... (tests) or "
            "model_path=... (production with QwenTTSBackend)"
        )
    from voice.engine import QwenTTSBackend

    backend = QwenTTSBackend(
        model_path=model_path,
        device=device,
        attn_implementation=attn_implementation,
    )
```

Note: `voice.Result.duration_ms` exists per voice spec; check actual attribute in `voice/result.py` and adjust if needed.

- [ ] **Step 5: Run the existing endpoint tests**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_endpoint.py -v`
Expected: all tests pass. If they fail because `voice.engine.FakeTTSBackend.synthesize()` signature differs slightly from the old fake, update the test setup to use voice's fake's exact contract (see `voice/packages/voice/src/voice/engine/fake.py`).

- [ ] **Step 6: Run the full agent-core-voice test suite**

Run: `cd packages/agent-core-voice && uv run pytest -v`
Expected: green. Voice library's synthesize is functionally compatible with the old QwenTTSBackend.synthesize signature.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/endpoint.py packages/agent-core-voice/tests/
git commit -m "feat(voice): swap QwenTTSBackend for voice.generate() — Phase 1"
```

---

### Task 5: Add SynthesisRequest envelope handler to endpoint (Phase 2)

**Files:**
- Modify: `packages/agent-core-voice/src/agent_core_voice/endpoint.py`
- Test: `packages/agent-core-voice/tests/test_envelope_flow.py`

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-voice/tests/test_envelope_flow.py`:

```python
"""Bus-end-to-end tests for SynthesisRequest → Ready / Failed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from voice.engine import FakeTTSBackend

from agent_core.bus.core import Bus
from agent_core.bus.envelope import Envelope, EventPayload
from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.protocol import VoiceInfo


@pytest.fixture
def fake_ref_wav(tmp_path: Path) -> Path:
    """Voice library's fake requires the ref_wav path to exist."""
    p = tmp_path / "ref.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 64)
    return p


@pytest.fixture
async def wired_bus_and_voice(tmp_path: Path, fake_ref_wav: Path):
    """Bus with voice endpoint + a stub caller endpoint to capture replies."""
    from agent_core.bus.core import Bus
    from agent_core.endpoints.stub import StubEndpoint

    bus = Bus(storage_path=tmp_path / "bus.sqlite")

    fake_backend = FakeTTSBackend()
    voice = VoiceEndpoint.for_test(
        backend=fake_backend,
        voices={"pepper": VoiceInfo(voice_id="pepper", ref_wav=fake_ref_wav, ref_text="hi")},
        output_dir=tmp_path / "voice-out",
        audit_path=tmp_path / "voice-audit.jsonl",
    )
    pepper_stub = StubEndpoint(name="pepper")

    await bus.register("voice", voice)
    await bus.register("pepper", pepper_stub)
    await bus.start()
    yield bus, voice, pepper_stub
    await bus.stop()


@pytest.mark.asyncio
async def test_synthesis_request_yields_ready(wired_bus_and_voice) -> None:
    bus, voice, pepper_stub = wired_bus_and_voice
    pepper_handle = bus.handle_for("pepper")

    req = Envelope(
        id="req-1",
        correlation_id="c1",
        to="voice",
        kind="Event",
        payload=EventPayload(
            type="SynthesisRequest",
            data={"text": "Hello, world."},
        ),
    )
    await pepper_handle.publish(req)

    # Drain bus dispatcher.
    for _ in range(50):
        if pepper_stub.received:
            break
        await asyncio.sleep(0.05)

    assert len(pepper_stub.received) == 1
    reply = pepper_stub.received[0]
    assert reply.kind == "Event"
    assert reply.payload.type == "SynthesisReady"
    assert reply.in_reply_to == "req-1"
    data = reply.payload.data
    assert Path(data["wav_path"]).exists()
    assert data["file_size_bytes"] > 0
    assert data["duration_s"] > 0
    assert data["chunks"] >= 1
    assert "retain_until" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_envelope_flow.py::test_synthesis_request_yields_ready -v`
Expected: FAIL — no envelope handler exists; pepper_stub.received stays empty.

- [ ] **Step 3: Implement the envelope handler in endpoint.py**

In `packages/agent-core-voice/src/agent_core_voice/endpoint.py`, replace the stub `deliver` method:

```python
async def deliver(self, envelope: Envelope) -> None:
    """Route SynthesisRequest envelopes to async synthesis task.

    Non-SynthesisRequest envelopes are debug-logged and acked (voice
    has no inbox semantics for other event types).
    """
    if envelope.kind != "Event" or envelope.payload.type != "SynthesisRequest":
        log.debug("VoiceEndpoint(name=%s) ignoring envelope %s (kind=%s, type=%s)",
                  self._name, envelope.id,
                  envelope.kind,
                  getattr(envelope.payload, "type", None))
        if self._handle is not None:
            await self._handle.ack(envelope.id)
        return

    from agent_core_voice.envelopes import SynthesisRequestPayload

    try:
        req = SynthesisRequestPayload.model_validate(envelope.payload.data)
    except Exception as exc:
        await self._publish_failed(envelope, "INTERNAL_ERROR", f"invalid SynthesisRequest payload: {exc}", retryable=False)
        if self._handle is not None:
            await self._handle.ack(envelope.id)
        return

    asyncio.create_task(self._handle_synthesis_request(envelope, req))


async def _handle_synthesis_request(
    self,
    envelope: Envelope,
    req,
) -> None:
    """Run synthesis as async task; publish Ready or Failed at the end."""
    from agent_core_voice.envelopes import SynthesisReadyPayload
    from agent_core_voice.lifecycle import retain_until_iso, write_addressed
    from voice import Spec, generate

    agent_name = envelope.from_
    voice_id = self._voice_id_for(agent_name)
    if voice_id is None:
        await self._publish_failed(envelope, "VOICE_NOT_PREPARED",
                                   f"no voice configured for agent {agent_name!r}",
                                   retryable=False)
        if self._handle is not None:
            await self._handle.ack(envelope.id)
        return

    timeout_s = req.timeout_s if req.timeout_s is not None else 300.0
    retain_s = req.retain_s if req.retain_s is not None else 3600.0

    options = req.options or {}
    spec = Spec(
        voice_id=voice_id,
        seed=options.get("seed", 42),
        chunk_strategy=options.get("chunk_strategy", "sentence"),
        parallel=options.get("parallel", True),
    )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(generate, req.text, spec, backend=self._backend),
            timeout=timeout_s,
        )
    except TimeoutError:
        await self._publish_failed(envelope, "TIMEOUT",
                                   f"synthesis exceeded timeout_s={timeout_s}",
                                   retryable=True)
        if self._handle is not None:
            await self._handle.ack(envelope.id)
        return
    except VoiceError as exc:
        await self._publish_failed_from_voiceerror(envelope, exc)
        if self._handle is not None:
            await self._handle.ack(envelope.id)
        return
    except Exception as exc:
        log.exception("voice.generate raised unexpected exception")
        await self._publish_failed(envelope, "INTERNAL_ERROR",
                                   f"{type(exc).__name__}: {exc}",
                                   retryable=False)
        if self._handle is not None:
            await self._handle.ack(envelope.id)
        return

    wav_bytes = bytes(result)
    wav_path, _sha = await asyncio.to_thread(
        write_addressed, wav_bytes,
        root=self._output_dir,
        retain_s=retain_s,
    )
    ready = Envelope(
        id="",  # bus stamps
        correlation_id=envelope.correlation_id,
        in_reply_to=envelope.id,
        to=envelope.from_,
        kind="Event",
        payload=EventPayload(
            type="SynthesisReady",
            data=SynthesisReadyPayload(
                wav_path=str(wav_path),
                file_size_bytes=wav_path.stat().st_size,
                duration_s=float(getattr(result, "duration_ms", 0)) / 1000.0,
                elapsed_s=sum(result.timings or [0.0]),
                sample_rate_hz=result.sample_rate_hz,
                cache_hit=bool(result.cache_hit),
                chunks=len(result.timings or [0]),
                retain_until=retain_until_iso(retain_s=retain_s),
            ).model_dump(),
        ),
    )
    assert self._handle is not None
    await self._handle.publish(ready)
    await self._handle.ack(envelope.id)


async def _publish_failed(self, request_env, reason, message, retryable):
    from agent_core_voice.envelopes import SynthesisFailedPayload

    assert self._handle is not None
    failed = Envelope(
        id="",
        correlation_id=request_env.correlation_id,
        in_reply_to=request_env.id,
        to=request_env.from_,
        kind="Event",
        payload=EventPayload(
            type="SynthesisFailed",
            data=SynthesisFailedPayload(
                reason=reason, message=message, retryable=retryable,
            ).model_dump(),
        ),
    )
    await self._handle.publish(failed)


async def _publish_failed_from_voiceerror(self, request_env, exc):
    reason = "INTERNAL_ERROR"
    retryable = False
    if isinstance(exc, GPUOOMError):
        reason, retryable = "GPU_OOM", True
    elif isinstance(exc, TextTooLongError):
        reason = "TEXT_TOO_LONG"
    elif isinstance(exc, VoiceNotPreparedError):
        reason = "VOICE_NOT_PREPARED"
    await self._publish_failed(request_env, reason, str(exc), retryable)


def _voice_id_for(self, agent_name: str) -> str | None:
    """Look up which voice_id belongs to agent_name.

    The endpoint config (yaml) maps agent_name -> voice_id via plugin.py's
    wire_endpoints_after_registration. For v1 we maintain a small dict
    here mirroring that wiring; in Phase 3 we'll thread it through
    cleanly from plugin.py construction.
    """
    return self._agent_to_voice.get(agent_name)
```

Also add to `__init__`:

```python
self._agent_to_voice: dict[str, str] = {}  # populated by plugin.py at wire time
```

And add a method:

```python
def register_agent(self, agent_name: str, voice_id: str) -> None:
    """Plugin.py calls this when wiring an MCP endpoint that names this voice."""
    if voice_id not in self._voices:
        raise ValueError(f"voice_id={voice_id!r} not configured")
    self._agent_to_voice[agent_name] = voice_id
```

- [ ] **Step 4: Update plugin.py to call register_agent**

In `packages/agent-core-voice/src/agent_core_voice/plugin.py`, inside the `wire_endpoints_after_registration` loop, after the voice_id validation, add:

```python
voice_ep.register_agent(name, voice_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_envelope_flow.py::test_synthesis_request_yields_ready -v`
Expected: PASS — pepper_stub.received[0] is a SynthesisReady envelope with valid data.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/endpoint.py packages/agent-core-voice/src/agent_core_voice/plugin.py packages/agent-core-voice/tests/test_envelope_flow.py
git commit -m "feat(voice): add SynthesisRequest envelope handler — Phase 2"
```

---

### Task 6: Add timeout + failure-path envelope-flow tests

**Files:**
- Modify: `packages/agent-core-voice/tests/test_envelope_flow.py`

- [ ] **Step 1: Add timeout test**

Append to `test_envelope_flow.py`:

```python
@pytest.mark.asyncio
async def test_synthesis_request_timeout_yields_failed(wired_bus_and_voice, monkeypatch) -> None:
    """When synthesis exceeds timeout_s, voice emits SynthesisFailed(TIMEOUT)."""
    import time
    bus, voice, pepper_stub = wired_bus_and_voice
    pepper_handle = bus.handle_for("pepper")

    # Monkeypatch voice.generate to sleep longer than timeout_s.
    def slow_generate(*args, **kwargs):
        time.sleep(2.0)
        raise RuntimeError("should have timed out")

    monkeypatch.setattr("agent_core_voice.endpoint.generate", slow_generate, raising=False)

    req = Envelope(
        id="req-2",
        correlation_id="c2",
        to="voice",
        kind="Event",
        payload=EventPayload(
            type="SynthesisRequest",
            data={"text": "Hello, world.", "timeout_s": 0.1},
        ),
    )
    await pepper_handle.publish(req)

    for _ in range(100):
        if pepper_stub.received:
            break
        await asyncio.sleep(0.05)

    assert len(pepper_stub.received) == 1
    reply = pepper_stub.received[0]
    assert reply.payload.type == "SynthesisFailed"
    assert reply.payload.data["reason"] == "TIMEOUT"
    assert reply.payload.data["retryable"] is True
    assert reply.in_reply_to == "req-2"


@pytest.mark.asyncio
async def test_synthesis_request_voice_not_prepared(wired_bus_and_voice) -> None:
    """If a non-registered agent publishes, voice emits VOICE_NOT_PREPARED."""
    bus, voice, pepper_stub = wired_bus_and_voice
    # Publish from an unregistered agent — call the bus enqueue directly to spoof from_.
    from agent_core.bus.core import Bus
    fake_handle = bus.handle_for("pepper")
    req = Envelope(
        id="req-3",
        correlation_id="c3",
        to="voice",
        kind="Event",
        from_="randoagent",  # not registered with voice
        payload=EventPayload(
            type="SynthesisRequest",
            data={"text": "Hello, world."},
        ),
    )
    # Bus stamps from_; we route this through pepper but the endpoint's
    # register_agent table only has pepper. Quick workaround: temporarily clear voice's table.
    voice._agent_to_voice.clear()
    await fake_handle.publish(req)

    for _ in range(50):
        if pepper_stub.received:
            break
        await asyncio.sleep(0.05)

    assert len(pepper_stub.received) == 1
    reply = pepper_stub.received[0]
    assert reply.payload.type == "SynthesisFailed"
    assert reply.payload.data["reason"] == "VOICE_NOT_PREPARED"
    assert reply.payload.data["retryable"] is False
```

- [ ] **Step 2: Run tests**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_envelope_flow.py -v`
Expected: 3 tests pass (success + timeout + voice-not-prepared).

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-voice/tests/test_envelope_flow.py
git commit -m "test(voice): add envelope-flow timeout + failure-path coverage"
```

---

### Task 7: Flip MCP tool to publish-and-return (Phase 3)

**Files:**
- Modify: `packages/agent-core-voice/src/agent_core_voice/mcp.py`
- Modify: `packages/agent-core-voice/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

In `packages/agent-core-voice/tests/test_mcp_tools.py`, replace the existing `synthesize_speech` test with:

```python
@pytest.mark.asyncio
async def test_synthesize_speech_publishes_and_returns_request_id(
    bus_with_voice_and_mcp,
):
    """New behavior: synthesize_speech publishes a SynthesisRequest and
    returns {request_id, status: 'queued'} immediately, without waiting."""
    bus, voice, mcp, voice_stub_receiver = bus_with_voice_and_mcp

    # Find the tool the plugin mounted.
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "synthesize_speech")

    out = await tool.fn(text="hello world")
    import json
    payload = json.loads(out[0].text)
    assert "request_id" in payload
    assert payload["status"] == "queued"

    # Voice received the SynthesisRequest envelope.
    for _ in range(20):
        if voice_stub_receiver.received:
            break
        await asyncio.sleep(0.05)
    assert len(voice_stub_receiver.received) == 1
    req = voice_stub_receiver.received[0]
    assert req.payload.type == "SynthesisRequest"
    assert req.id == payload["request_id"]
```

(Adjust the existing `bus_with_voice_and_mcp` fixture or create one; the test asserts the tool fires an envelope but doesn't synthesize.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_mcp_tools.py::test_synthesize_speech_publishes_and_returns_request_id -v`
Expected: FAIL — current tool returns `{path, duration_s, ...}` synchronously.

- [ ] **Step 3: Rewrite synthesize_speech in mcp.py**

Replace the entire `register_voice_tools` function in `packages/agent-core-voice/src/agent_core_voice/mcp.py`:

```python
"""Agent-facing MCP tool surface for the voice endpoint (async-via-bus)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp.types import TextContent

from agent_core_voice.envelopes import SynthesisRequestPayload

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle
    from fastmcp import FastMCP


def register_voice_tools(
    *,
    mcp: FastMCP,
    bus_handle: BusHandle,
    voice_endpoint_name: str,
    voice_id: str,
    agent_name: str,
    voice_info: dict[str, Any],
) -> None:
    """Mount synthesize_speech (envelope-fire) + voice_info on `mcp`.

    `voice_id`, `agent_name`, and the target voice endpoint name are closed
    into the tool. Synthesis is async-via-bus: the tool publishes a
    SynthesisRequest envelope and returns the envelope id immediately.
    """

    @mcp.tool(
        name="synthesize_speech",
        description=(
            "Synthesize text in your assigned voice. Returns {request_id, status} "
            "immediately. The synthesized WAV arrives on your inbox as a "
            "SynthesisReady event with `wav_path`. SynthesisFailed arrives on the "
            "same inbox if synthesis fails."
        ),
    )
    async def _synthesize(
        text: str,
        timeout_s: float | None = None,
        retain_s: float | None = None,
        options: dict | None = None,
    ) -> list[Any]:
        from agent_core.bus.envelope import Envelope, EventPayload
        import uuid

        try:
            payload = SynthesisRequestPayload(
                text=text, timeout_s=timeout_s, retain_s=retain_s, options=options,
            )
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({
                "error": "invalid_request",
                "detail": str(exc),
            }))]

        env_id = str(uuid.uuid4())
        env = Envelope(
            id=env_id,
            correlation_id=env_id,
            to=voice_endpoint_name,
            kind="Event",
            payload=EventPayload(
                type="SynthesisRequest",
                data=payload.model_dump(exclude_none=True),
            ),
        )
        await bus_handle.publish(env)
        return [TextContent(type="text", text=json.dumps({
            "request_id": env_id,
            "status": "queued",
        }))]

    @mcp.tool(
        name="voice_info",
        description="Return metadata about your assigned voice.",
    )
    async def _voice_info() -> list[Any]:
        return [TextContent(type="text", text=json.dumps(voice_info, ensure_ascii=False))]


__all__ = ["register_voice_tools"]
```

- [ ] **Step 4: Update plugin.py wire_endpoints_after_registration to pass bus_handle**

In `packages/agent-core-voice/src/agent_core_voice/plugin.py`, change the `_mounter` closure to accept `bus_handle` (it already does — `def _mounter(bus_handle, *, ...)`) and pass it through to `register_voice_tools`:

```python
def _mounter(
    bus_handle,
    *,
    voice_ep: VoiceEndpoint = voice_ep,
    voice_endpoint_name: str = voice_name,
    mcp_endpoint=endpoint,
    voice_id: str = voice_id,
    agent_name: str = name,
) -> None:
    register_voice_tools(
        mcp=mcp_endpoint._mcp,
        bus_handle=bus_handle,
        voice_endpoint_name=voice_endpoint_name,
        voice_id=voice_id,
        agent_name=agent_name,
        voice_info=voice_ep.voice_info(voice_id),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_mcp_tools.py -v`
Expected: new test passes; old test (synchronous expectation) deleted or rewritten.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/mcp.py packages/agent-core-voice/src/agent_core_voice/plugin.py packages/agent-core-voice/tests/test_mcp_tools.py
git commit -m "feat(voice): flip synthesize_speech MCP tool to envelope-fire — Phase 3"
```

---

### Task 8: Wire file-lifecycle cleanup tick (Phase 4 — finish)

**Files:**
- Modify: `packages/agent-core-voice/src/agent_core_voice/endpoint.py`
- Test: `packages/agent-core-voice/tests/test_lifecycle.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/agent-core-voice/tests/test_lifecycle.py`:

```python
@pytest.mark.asyncio
async def test_endpoint_cleanup_tick_runs_periodically(tmp_path):
    """VoiceEndpoint exposes a cleanup_tick coroutine the scheduler can drive."""
    from agent_core_voice.endpoint import VoiceEndpoint
    from agent_core_voice.lifecycle import write_addressed
    from agent_core_voice.protocol import VoiceInfo
    from voice.engine import FakeTTSBackend

    output_dir = tmp_path / "voice-out"
    output_dir.mkdir()
    audio = b"RIFF" + b"\x00" * 100
    path, _ = write_addressed(audio, root=output_dir, retain_s=0.05)

    ref_wav = tmp_path / "ref.wav"
    ref_wav.write_bytes(b"RIFF" + b"\x00" * 64)

    ep = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={"pepper": VoiceInfo(voice_id="pepper", ref_wav=ref_wav, ref_text="hi")},
        output_dir=output_dir,
        audit_path=tmp_path / "audit.jsonl",
    )

    import asyncio
    await asyncio.sleep(0.1)
    n = await ep.cleanup_tick()
    assert n == 1
    assert not path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_lifecycle.py::test_endpoint_cleanup_tick_runs_periodically -v`
Expected: FAIL — `cleanup_tick` method doesn't exist.

- [ ] **Step 3: Add cleanup_tick to VoiceEndpoint**

In `packages/agent-core-voice/src/agent_core_voice/endpoint.py`:

```python
async def cleanup_tick(self) -> int:
    """Sweep expired WAVs from the output directory. Returns count removed.

    Called periodically (every 5 min) by a scheduler job, configured in
    yaml. Safe to call manually for tests.
    """
    from agent_core_voice.lifecycle import cleanup_expired
    return await asyncio.to_thread(cleanup_expired, root=self._output_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/agent-core-voice && uv run pytest tests/test_lifecycle.py -v`
Expected: all 7 tests pass.

- [ ] **Step 5: Document the scheduler-job yaml entry in CHANGELOG**

Add to `packages/agent-core-voice/CHANGELOG.md` (or append a section to it):

```markdown
## File-lifecycle cleanup job

After upgrading, add this scheduler job to `~/.agent-core/jobs.yaml` so
voice WAVs get cleaned up:

```yaml
- name: voice-cleanup-tick
  schedule: "*/5 * * * *"        # every 5 minutes
  prompt: "Call voice.cleanup_tick()"
  target: voice
```

(Exact yaml shape depends on scheduler endpoint's job format — check
`agent_core/endpoints/scheduler.py` for the current schema.)
```

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-voice/src/agent_core_voice/endpoint.py packages/agent-core-voice/tests/test_lifecycle.py packages/agent-core-voice/CHANGELOG.md
git commit -m "feat(voice): wire file-lifecycle cleanup_tick — Phase 4"
```

---

### Task 9: Verify caller-grep + audit (pre-cut)

- [ ] **Step 1: Grep for old synthesize_speech expectations**

Run: `grep -rn "synthesize_speech" ~/.testbot ~/.pepper 2>/dev/null`
Expected: lists every reference. Document each in the PR description.

- [ ] **Step 2: For each caller, confirm migration path**

For each grep hit, decide:
- Pepper/testbot CLAUDE.md docs referencing the old return shape — update them
- Live flows expecting `{wav_path, duration_s}` — must consume `SynthesisReady` envelope instead

- [ ] **Step 3: Update consumer docs**

Edit each affected CLAUDE.md to describe the new tool return shape and the inbox-wake expectation. Show an example.

- [ ] **Step 4: Commit**

```bash
git add <updated docs>
git commit -m "docs(voice): update consumer agents' tool docs for envelope-fire shape"
```

---

### Task 10: Real-engine validation gate (the prove-before-claim ritual)

**Files:** None — operational task.

- [ ] **Step 1: Stop the daemon**

Run: `agent-core daemon stop`

- [ ] **Step 2: Install the new package**

Run: `uv pip install -e packages/agent-core-voice --python C:/Users/jeffr/.agent-core/.venv/Scripts/python.exe`

- [ ] **Step 3: Restart the daemon**

Run: `agent-core daemon start`

- [ ] **Step 4: Wait for voice endpoint to warm**

Run: `agent-core daemon status`
Expected: daemon up, voice endpoint reports voices prepared (~5-10s after start).

- [ ] **Step 5: Send a real synthesis envelope (Wren or testbot)**

Using one of the running agent sessions, call `synthesize_speech(text="<5-sentence test passage>")`.

- [ ] **Step 6: Verify the WAV file exists**

Wait for the SynthesisReady inbox wake. From the envelope's `wav_path`:

Run: `ls -la <wav_path>` and confirm file size matches `file_size_bytes`.

- [ ] **Step 7: Listen to the WAV**

Open the WAV file in a player. Confirm audio quality matches the comparison-script baseline (or better — chunker improvements should land here too).

- [ ] **Step 8: Verify latency**

Compare `elapsed_s` in the SynthesisReady payload to the comparison-script's ~58s for a 5-sentence passage. Should be in the same range.

- [ ] **Step 9: Document the validation in the PR**

PR description includes: envelope ids, WAV paths, elapsed_s numbers, listener verdict on quality, any deviations.

---

## Self-review (run on this plan before handing off)

**Spec coverage check:**

- §3 Architecture (envelope flow) → Task 5
- §4.1 SynthesisRequest schema → Task 2
- §4.2 SynthesisReady schema → Task 2
- §4.3 SynthesisFailed schema → Task 2
- §5 MCP tool surface → Task 7
- §6 File lifecycle → Tasks 3 + 8
- §7 Failure semantics (typed envelope + timeout) → Tasks 5 + 6
- §8 Backward compat (caller audit) → Task 9
- §9 Phase 1 backend swap → Task 4
- §9 Phase 2 envelope handler → Tasks 5 + 6
- §9 Phase 3 MCP tool flip → Task 7
- §9 Phase 4 file lifecycle → Tasks 3 + 8
- §10 Known limitations (orphan-envelope) — documented in spec only, no implementation work
- §12 Testing strategy (real-engine gate) → Task 10
- All covered.

**Placeholder scan:**

- No "TBD", "implement later", "handle edge cases" patterns.
- One soft spot: Task 4 step 4 references `voice.Result.duration_ms` with a "check actual attribute" note — that's a real instruction, not a placeholder, but should be confirmed in the voice repo at execution time.

**Type consistency:**

- `SynthesisRequestPayload`, `SynthesisReadyPayload`, `SynthesisFailedPayload` referenced consistently.
- `voice_endpoint_name`, `voice_id`, `agent_name` flow consistently from plugin.py → mcp.py.
- `cleanup_tick`, `register_agent`, `write_addressed`, `cleanup_expired`, `retain_until_iso` names consistent across tasks.
