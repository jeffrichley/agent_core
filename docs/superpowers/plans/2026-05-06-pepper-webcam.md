# Pepper Webcam Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `agent-core-webcam` package + `builtin.webcam` endpoint that exposes `capture_webcam_frame` and `list_cameras` MCP tools on Pepper's `/mcp/pepper` surface.

**Architecture:** New peer package alongside `agent-core-discord` and `agent-core-briefs`. WebcamEndpoint implements the standard bus `Endpoint` protocol but `deliver` is a no-op — webcam is tool-only. Pluggy plugin (`register_endpoint_types` + `reserved_endpoint_params` + `wire_endpoints_after_registration`) mounts the two MCP tools onto any `ClaudeCodeMCPEndpoint` whose yaml params name a webcam endpoint. Camera access is mediated by a `CameraBackend` Protocol so `FakeCameraBackend` can drive unit tests deterministically while `OpenCVCameraBackend` does the real thing.

**Tech Stack:** Python 3.12+, `opencv-python` (real backend only), `pluggy`, `pydantic`, `fastmcp`, `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"` already configured at workspace root).

**Spec:** `docs/superpowers/specs/2026-05-06-pepper-webcam-design.md`

**Related issues:** [#39](https://github.com/jeffrichley/agent_core/issues/39) (generic MCP audit) — webcam ships its own local audit log; #39 is the cross-cutting fix.

---

## File Map

```
packages/agent-core-webcam/
├── pyproject.toml                              # Task 1
├── src/agent_core_webcam/
│   ├── __init__.py                             # Task 1
│   ├── protocol.py                             # Task 2 — CameraBackend Protocol, CameraInfo, exceptions
│   ├── fake.py                                 # Task 3 — FakeCameraBackend (test backend)
│   ├── audit.py                                # Task 4 — AuditEvent + AuditLog
│   ├── endpoint.py                             # Tasks 5-9 — WebcamEndpoint
│   ├── mcp.py                                  # Task 10 — register_webcam_tools
│   ├── plugin.py                               # Task 11 — pluggy hookimpls
│   └── opencv_backend.py                       # Task 12 — OpenCVCameraBackend (real)
└── tests/
    ├── __init__.py                             # Task 1
    ├── conftest.py                             # Task 5 (fixtures)
    ├── test_protocol.py                        # Task 2
    ├── test_fake_backend.py                    # Task 3
    ├── test_audit.py                           # Task 4
    ├── test_endpoint_lifecycle.py              # Task 5
    ├── test_endpoint_capture_happy.py          # Task 6
    ├── test_endpoint_capture_errors.py         # Task 7
    ├── test_endpoint_capture_save_false.py     # Task 8
    ├── test_endpoint_concurrency.py            # Task 9
    ├── test_endpoint_list_cameras.py           # Task 9 (same file, separate tests)
    ├── test_mcp_register.py                    # Task 10
    ├── test_mcp_wiring.py                      # Task 11
    └── test_real_opencv.py                     # Task 12 (gated)

# Modifications outside the package:
pyproject.toml                                  # Task 1 (workspace member registration)
```

---

## Task 1: Scaffold package skeleton

**Files:**
- Create: `packages/agent-core-webcam/pyproject.toml`
- Create: `packages/agent-core-webcam/src/agent_core_webcam/__init__.py`
- Create: `packages/agent-core-webcam/tests/__init__.py`
- Modify: `pyproject.toml` (workspace root) — add `agent-core-webcam` to `[tool.uv.sources]`

- [ ] **Step 1.1: Create the package pyproject.toml**

Create `packages/agent-core-webcam/pyproject.toml`:

```toml
[project]
name = "agent-core-webcam"
version = "0.1.0"
description = "Webcam endpoint for agent_core — agent-driven on-demand frame capture"
requires-python = ">=3.12"
dependencies = [
    "agent-core",
    "fastmcp>=2.0",
    "opencv-python>=4.10",
    "pluggy>=1.6",
    "pydantic>=2.7",
]

[project.entry-points."agent_core"]
webcam_aliases = "agent_core_webcam.plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_webcam"]
```

- [ ] **Step 1.2: Create empty `__init__.py` files**

Create `packages/agent-core-webcam/src/agent_core_webcam/__init__.py` with content:

```python
"""agent-core-webcam — Webcam capture endpoint for agent_core."""
```

Create `packages/agent-core-webcam/tests/__init__.py` (empty file).

- [ ] **Step 1.3: Register package in workspace root**

Modify `pyproject.toml` at repo root. Find the `[tool.uv.sources]` block and add:

```toml
agent-core-webcam = { workspace = true }
```

Place it alphabetically with the other entries (after `agent-core-credentials`, before `agent-core-discord`).

- [ ] **Step 1.4: Sync the workspace**

Run: `uv sync`
Expected: succeeds, `agent-core-webcam` appears in the resolved package list.

- [ ] **Step 1.5: Verify the package imports**

Run: `uv run python -c "import agent_core_webcam; print(agent_core_webcam.__doc__)"`
Expected output: `agent-core-webcam — Webcam capture endpoint for agent_core.`

- [ ] **Step 1.6: Commit**

```bash
git add packages/agent-core-webcam/pyproject.toml packages/agent-core-webcam/src/agent_core_webcam/__init__.py packages/agent-core-webcam/tests/__init__.py pyproject.toml
git commit -m "feat(webcam): scaffold agent-core-webcam package"
```

---

## Task 2: Camera backend protocol + exceptions

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/protocol.py`
- Test: `packages/agent-core-webcam/tests/test_protocol.py`

The protocol defines what a `CameraBackend` looks like — what `WebcamEndpoint` calls into. Real `OpenCVCameraBackend` and `FakeCameraBackend` (both implemented later) satisfy this protocol. Exceptions carry the failure-mode taxonomy from the spec's error-handling table.

- [ ] **Step 2.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_protocol.py`:

```python
"""Camera backend protocol shape + exception taxonomy.

These tests pin the surface that fake and real backends must implement.
"""
from __future__ import annotations

from agent_core_webcam.protocol import (
    CameraBackend,
    CameraBusyError,
    CameraInfo,
    CameraNotFoundError,
    ReadTimeoutError,
    WebcamError,
)


def test_camera_info_is_a_simple_dataclass():
    info = CameraInfo(index=0, name="Integrated Camera", available=True)
    assert info.index == 0
    assert info.name == "Integrated Camera"
    assert info.available is True


def test_exception_hierarchy_descends_from_webcam_error():
    assert issubclass(CameraBusyError, WebcamError)
    assert issubclass(CameraNotFoundError, WebcamError)
    assert issubclass(ReadTimeoutError, WebcamError)


def test_camera_backend_is_runtime_checkable():
    class _MinimalBackend:
        def list_cameras(self): return []
        def capture(self, index, resolution): return b""

    assert isinstance(_MinimalBackend(), CameraBackend)


def test_camera_backend_rejects_object_missing_methods():
    class _NotABackend:
        def list_cameras(self): return []
        # missing capture()

    assert not isinstance(_NotABackend(), CameraBackend)
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_protocol.py -v`
Expected: FAIL — `ImportError: cannot import name 'CameraBackend' from 'agent_core_webcam.protocol'`

- [ ] **Step 2.3: Implement the protocol module**

Create `packages/agent-core-webcam/src/agent_core_webcam/protocol.py`:

```python
"""Camera backend protocol + exception taxonomy.

The ``CameraBackend`` Protocol is the seam that lets ``WebcamEndpoint``
work against either ``OpenCVCameraBackend`` (real) or ``FakeCameraBackend``
(deterministic for tests). All failure modes the endpoint maps to
agent-readable error messages descend from ``WebcamError`` so the
endpoint's exception handling stays simple.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class WebcamError(Exception):
    """Base for every error a CameraBackend may raise."""


class CameraNotFoundError(WebcamError):
    """The requested camera index does not exist on this host."""


class CameraBusyError(WebcamError):
    """The camera opened but is in use by another process."""


class ReadTimeoutError(WebcamError):
    """The camera opened but did not return a frame within the timeout."""


@dataclass(frozen=True)
class CameraInfo:
    """One enumerated camera on the host."""

    index: int
    name: str
    available: bool


@runtime_checkable
class CameraBackend(Protocol):
    """Hardware-access seam used by WebcamEndpoint."""

    def list_cameras(self) -> list[CameraInfo]:
        """Enumerate cameras the host can see right now."""

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        """Capture one frame from camera ``index`` at ``resolution``.

        Returns PNG-encoded bytes (sRGB, RGB-ordered).

        Raises:
            CameraNotFoundError: index not present on this host.
            CameraBusyError: device opened but is in use elsewhere.
            ReadTimeoutError: device opened but read() returned no frame.
        """


__all__ = [
    "CameraBackend",
    "CameraBusyError",
    "CameraInfo",
    "CameraNotFoundError",
    "ReadTimeoutError",
    "WebcamError",
]
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `uv run pytest packages/agent-core-webcam/tests/test_protocol.py -v`
Expected: 4 passed.

- [ ] **Step 2.5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/protocol.py packages/agent-core-webcam/tests/test_protocol.py
git commit -m "feat(webcam): add CameraBackend protocol + exception taxonomy"
```

---

## Task 3: FakeCameraBackend

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/fake.py`
- Test: `packages/agent-core-webcam/tests/test_fake_backend.py`

The fake is the test-time backend. Per the project's "fakes mirror real strictly" memory: it must refuse argument shapes the real OpenCV backend would refuse. The fake produces a deterministic 1×1 RGB-ordered PNG when capturing — small, valid PNG bytes, no real pixel work.

- [ ] **Step 3.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_fake_backend.py`:

```python
"""FakeCameraBackend behavior tests.

The fake is the workhorse for endpoint unit tests. Its modes
(with_busy, with_missing, with_read_timeout) drive every error path
in the endpoint's failure-mode table.
"""
from __future__ import annotations

import struct

import pytest
from agent_core_webcam.fake import FakeCameraBackend
from agent_core_webcam.protocol import (
    CameraBackend,
    CameraBusyError,
    CameraNotFoundError,
    ReadTimeoutError,
)


def test_fake_satisfies_protocol():
    assert isinstance(FakeCameraBackend(), CameraBackend)


def test_default_fake_has_one_camera():
    fake = FakeCameraBackend()
    cams = fake.list_cameras()
    assert len(cams) == 1
    assert cams[0].index == 0
    assert cams[0].available is True


def test_with_cameras_lists_each():
    fake = FakeCameraBackend.with_cameras([0, 1, 2])
    cams = fake.list_cameras()
    assert [c.index for c in cams] == [0, 1, 2]
    assert all(c.available for c in cams)


def test_capture_returns_valid_png_bytes():
    fake = FakeCameraBackend()
    data = fake.capture(0, (1280, 720))
    # PNG magic number: 89 50 4E 47 0D 0A 1A 0A
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # IHDR chunk follows; width/height are big-endian u32 at bytes 16–24
    width, height = struct.unpack(">II", data[16:24])
    assert width == 1280
    assert height == 720


def test_with_missing_raises_not_found():
    fake = FakeCameraBackend.with_cameras([0]).with_missing(3)
    with pytest.raises(CameraNotFoundError, match="3"):
        fake.capture(3, (640, 480))


def test_with_busy_raises_busy():
    fake = FakeCameraBackend.with_busy(0)
    with pytest.raises(CameraBusyError, match="0"):
        fake.capture(0, (640, 480))


def test_with_read_timeout_raises_timeout():
    fake = FakeCameraBackend.with_read_timeout(0)
    with pytest.raises(ReadTimeoutError):
        fake.capture(0, (640, 480))


def test_capture_unknown_index_raises_not_found_by_default():
    """Asking for a camera not in the configured set raises like real OpenCV."""
    fake = FakeCameraBackend.with_cameras([0])
    with pytest.raises(CameraNotFoundError, match="5"):
        fake.capture(5, (640, 480))


def test_modes_compose():
    """with_cameras, then with_busy on a configured camera."""
    fake = FakeCameraBackend.with_cameras([0, 1]).with_busy(0)
    # Camera 1 still works
    data = fake.capture(1, (320, 240))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # Camera 0 is busy
    with pytest.raises(CameraBusyError):
        fake.capture(0, (320, 240))


def test_partial_failure_lists_unavailable_cameras():
    """A camera marked busy still appears in list_cameras with available=False."""
    fake = FakeCameraBackend.with_cameras([0, 1]).with_busy(1)
    cams = fake.list_cameras()
    by_idx = {c.index: c for c in cams}
    assert by_idx[0].available is True
    assert by_idx[1].available is False
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_fake_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core_webcam.fake'`

- [ ] **Step 3.3: Implement FakeCameraBackend**

Create `packages/agent-core-webcam/src/agent_core_webcam/fake.py`:

```python
"""Deterministic camera backend for tests.

Mirrors ``OpenCVCameraBackend``'s behavior strictly — including refusing
argument shapes the real backend refuses (unknown camera index →
``CameraNotFoundError``). Produces minimal valid PNGs without any
imaging work, so tests stay fast.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from agent_core_webcam.protocol import (
    CameraBusyError,
    CameraInfo,
    CameraNotFoundError,
    ReadTimeoutError,
)


@dataclass
class FakeCameraBackend:
    """In-memory camera backend with composable failure modes.

    Use the classmethods (``with_cameras``, ``with_busy``, etc.) to
    configure modes. Each returns a new ``FakeCameraBackend`` so test
    setup reads naturally.
    """

    available_indices: list[int] = field(default_factory=lambda: [0])
    _busy: set[int] = field(default_factory=set)
    _missing: set[int] = field(default_factory=set)
    _timeout: set[int] = field(default_factory=set)

    @classmethod
    def with_cameras(cls, indices: list[int]) -> "FakeCameraBackend":
        return cls(available_indices=list(indices))

    def with_busy(self, index: int) -> "FakeCameraBackend":
        self._busy.add(index)
        return self

    def with_missing(self, index: int) -> "FakeCameraBackend":
        self._missing.add(index)
        return self

    def with_read_timeout(self, index: int) -> "FakeCameraBackend":
        self._timeout.add(index)
        return self

    def list_cameras(self) -> list[CameraInfo]:
        out: list[CameraInfo] = []
        for idx in self.available_indices:
            available = idx not in self._busy and idx not in self._missing
            out.append(CameraInfo(index=idx, name=f"Fake Camera {idx}", available=available))
        return out

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        if index in self._missing:
            raise CameraNotFoundError(f"camera {index} marked missing")
        if index not in self.available_indices:
            raise CameraNotFoundError(f"camera {index} not in configured indices")
        if index in self._busy:
            raise CameraBusyError(f"camera {index} is busy")
        if index in self._timeout:
            raise ReadTimeoutError(f"camera {index} did not return a frame in time")
        return _make_minimal_png(resolution)


def _make_minimal_png(resolution: tuple[int, int]) -> bytes:
    """Produce a valid solid-gray PNG of the requested resolution.

    Uses pure-Python PNG construction (no PIL/cv2 dependency) so the
    fake stays light. The image is solid mid-gray RGB; tests only need
    the bytes to be a valid PNG with the right dimensions.
    """
    width, height = resolution
    # IHDR: width, height, bit depth=8, color type=2 (RGB), compression, filter, interlace
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    row_bytes = width * 3
    for _ in range(height):
        raw += b"\x00" + (b"\x80" * row_bytes)  # filter byte + gray pixels
    idat = zlib.compress(raw, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return length + tag + data + crc


__all__ = ["FakeCameraBackend"]
```

- [ ] **Step 3.4: Run test to verify it passes**

Run: `uv run pytest packages/agent-core-webcam/tests/test_fake_backend.py -v`
Expected: 10 passed.

- [ ] **Step 3.5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/fake.py packages/agent-core-webcam/tests/test_fake_backend.py
git commit -m "feat(webcam): add FakeCameraBackend for deterministic tests"
```

---

## Task 4: AuditEvent + AuditLog

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/audit.py`
- Test: `packages/agent-core-webcam/tests/test_audit.py`

JSONL append-only audit log. Mirrors `agent_core_briefs.audit` design (asyncio.to_thread writes, swallow disk failures so audit never breaks the tool path, mkdir parents on first write). Schema matches the spec's audit log section.

- [ ] **Step 4.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_audit.py`:

```python
"""Tests for the webcam audit log (JSONL append).

Pins the on-disk schema so log readers (Pepper, operators, future
log-aggregation tooling) can rely on the field shape, and confirms a
disk failure never breaks the surrounding capture flow.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_core_webcam.audit import AuditEvent, AuditLog


def _event(**overrides) -> AuditEvent:
    base = {
        "timestamp": datetime(2026, 5, 6, 14, 23, 7, 481000, tzinfo=UTC),
        "tool": "capture_webcam_frame",
        "result": "ok",
        "data": {"camera_index": 0, "resolution": [1280, 720]},
    }
    base.update(overrides)
    return AuditEvent(**base)


async def test_write_appends_one_jsonl_line(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    await log.write(_event())
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert text.endswith("\n")
    parsed = json.loads(text.strip())
    assert parsed["timestamp"] == "2026-05-06T14:23:07.481000+00:00"
    assert parsed["tool"] == "capture_webcam_frame"
    assert parsed["result"] == "ok"
    assert parsed["data"] == {"camera_index": 0, "resolution": [1280, 720]}


async def test_write_appends_multiple_lines(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    await log.write(_event(tool="capture_webcam_frame"))
    await log.write(_event(tool="list_cameras"))
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["tool"] == "capture_webcam_frame"
    assert json.loads(lines[1])["tool"] == "list_cameras"


async def test_write_creates_parent_directory(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "audit.jsonl"
    log = AuditLog(target)
    await log.write(_event())
    assert target.exists()


async def test_write_swallows_disk_failure(tmp_path: Path):
    """A path that cannot be written must not raise — audit is observability,
    not the critical path of capture."""
    # Point at a directory we can't write to: use a path that has a file
    # where the parent should be a directory.
    blocking_file = tmp_path / "blocker"
    blocking_file.write_text("blocks the parent dir", encoding="utf-8")
    impossible = blocking_file / "audit.jsonl"
    log = AuditLog(impossible)
    # Must not raise.
    await log.write(_event())


def test_default_path_returns_endpoint_scoped_path():
    p = AuditLog.default_path("webcam-pepper")
    assert p.name == "audit.jsonl"
    assert p.parent.name == "webcam-pepper"
    assert p.parent.parent.name == "webcam"
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core_webcam.audit'`

- [ ] **Step 4.3: Implement audit.py**

Create `packages/agent-core-webcam/src/agent_core_webcam/audit.py`:

```python
"""Append-only JSONL audit log for webcam tool invocations.

Each ``capture_webcam_frame`` and ``list_cameras`` call writes one line.
Schema is documented in the design spec. Failures are swallowed so an
audit failure never breaks a capture.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEvent:
    """One line in the webcam audit log."""

    timestamp: datetime
    tool: str
    result: str  # "ok" | "error"
    data: dict[str, Any]


class AuditLog:
    """Append-only JSONL audit log."""

    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def default_path(endpoint_name: str) -> Path:
        """Returns ``~/.agent-core/webcam/<endpoint_name>/audit.jsonl``."""
        return Path.home() / ".agent-core" / "webcam" / endpoint_name / "audit.jsonl"

    async def write(self, event: AuditEvent) -> None:
        try:
            line = self._serialize(event)
            await asyncio.to_thread(self._append_line, self._path, line)
        except Exception as exc:
            msg = f"agent_core_webcam.audit: write failed for {self._path}: {exc}"
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
            "timestamp": event.timestamp.isoformat(),
            "tool": event.tool,
            "result": event.result,
            "data": event.data,
        }
        return json.dumps(payload, default=str, ensure_ascii=False)


__all__ = ["AuditEvent", "AuditLog"]
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `uv run pytest packages/agent-core-webcam/tests/test_audit.py -v`
Expected: 5 passed.

- [ ] **Step 4.5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/audit.py packages/agent-core-webcam/tests/test_audit.py
git commit -m "feat(webcam): add AuditEvent + AuditLog for capture tracking"
```

---

## Task 5: WebcamEndpoint scaffold (lifecycle only)

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py`
- Create: `packages/agent-core-webcam/tests/conftest.py`
- Test: `packages/agent-core-webcam/tests/test_endpoint_lifecycle.py`

Stand up the endpoint class with the standard `Endpoint` protocol shape (`name`, `start`, `deliver`, `stop`). No capture logic yet; just construction, config handling, defaults, and a no-op `deliver`. This is the first task that exercises the bus `Endpoint` protocol.

- [ ] **Step 5.1: Write the conftest with shared fixtures**

Create `packages/agent-core-webcam/tests/conftest.py`:

```python
"""Shared fixtures for webcam endpoint tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend


@pytest.fixture
def fake_backend() -> FakeCameraBackend:
    """Default fake — one camera at index 0, all calls succeed."""
    return FakeCameraBackend.with_cameras([0])


@pytest.fixture
def endpoint(tmp_path: Path, fake_backend: FakeCameraBackend) -> WebcamEndpoint:
    """A fresh WebcamEndpoint pointed at tmp_path with the default fake backend."""
    return WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=fake_backend,
    )
```

- [ ] **Step 5.2: Write the failing test**

Create `packages/agent-core-webcam/tests/test_endpoint_lifecycle.py`:

```python
"""WebcamEndpoint lifecycle tests — construction, defaults, start/stop, deliver no-op."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend

from agent_core.bus.envelope import Envelope, EventPayload
from agent_core.bus.protocol import Endpoint


def test_endpoint_satisfies_bus_endpoint_protocol(endpoint: WebcamEndpoint):
    assert isinstance(endpoint, Endpoint)


def test_endpoint_has_name(endpoint: WebcamEndpoint):
    assert endpoint.name == "webcam-test"


def test_endpoint_defaults_when_paths_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ep = WebcamEndpoint(
        name="webcam-pepper",
        camera_backend=FakeCameraBackend(),
    )
    assert ep.captures_root == tmp_path / ".agent-core" / "webcam" / "webcam-pepper"
    assert ep.audit_log.path == ep.captures_root / "audit.jsonl"
    assert ep.default_camera_index == 0
    assert ep.default_resolution == (1280, 720)
    assert ep.max_resolution == (3840, 2160)
    assert ep.capture_timeout_seconds == 3.0
    assert ep.enabled is True


def test_endpoint_accepts_explicit_overrides(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "shots",
        audit_log_path=tmp_path / "log.jsonl",
        default_camera_index=1,
        default_resolution=[640, 480],
        max_resolution=[1920, 1080],
        capture_timeout_seconds=5.0,
        enabled=False,
        camera_backend=FakeCameraBackend(),
    )
    assert ep.captures_root == tmp_path / "shots"
    assert ep.audit_log.path == tmp_path / "log.jsonl"
    assert ep.default_camera_index == 1
    assert ep.default_resolution == (640, 480)
    assert ep.max_resolution == (1920, 1080)
    assert ep.capture_timeout_seconds == 5.0
    assert ep.enabled is False


async def test_start_stores_bus_handle_and_stop_clears_it(endpoint: WebcamEndpoint):
    class _FakeHandle:
        pass

    handle = _FakeHandle()
    await endpoint.start(handle)
    assert endpoint._handle is handle
    await endpoint.stop()
    assert endpoint._handle is None


async def test_deliver_is_a_noop(endpoint: WebcamEndpoint):
    """Webcam is tool-only; receiving an envelope is unexpected but must not crash."""
    env = Envelope(
        id="env-1",
        correlation_id="corr-1",
        in_reply_to=None,
        to="webcam-test",
        kind="Event",
        payload=EventPayload(type="Stray", data={}),
    )
    # Must not raise.
    await endpoint.deliver(env)


def test_resolution_lists_are_normalized_to_tuples(tmp_path):
    """YAML deserializes lists; the endpoint stores tuples for hashability + ordering clarity."""
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "x",
        audit_log_path=tmp_path / "y.jsonl",
        default_resolution=[800, 600],
        max_resolution=[2000, 1500],
        camera_backend=FakeCameraBackend(),
    )
    assert isinstance(ep.default_resolution, tuple)
    assert isinstance(ep.max_resolution, tuple)
```

- [ ] **Step 5.3: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_lifecycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core_webcam.endpoint'`

- [ ] **Step 5.4: Implement WebcamEndpoint scaffold**

Create `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py`:

```python
"""WebcamEndpoint — bus endpoint that exposes capture tools via MCP.

Implements the standard Endpoint protocol but ``deliver`` is a no-op:
webcam is tool-only, no inbox, no agent-to-agent envelopes. The
endpoint exists so MCP tools have somewhere to live and config to read.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_core_webcam.audit import AuditLog
from agent_core_webcam.protocol import CameraBackend

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


def _to_tuple(value: tuple[int, int] | list[int]) -> tuple[int, int]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    raise ValueError(f"resolution must be [width, height], got {value!r}")


class WebcamEndpoint:
    """Tool-only bus endpoint backing the webcam MCP tool surface."""

    def __init__(
        self,
        *,
        name: str,
        captures_root: Path | str | None = None,
        audit_log_path: Path | str | None = None,
        default_camera_index: int = 0,
        default_resolution: tuple[int, int] | list[int] = (1280, 720),
        max_resolution: tuple[int, int] | list[int] = (3840, 2160),
        capture_timeout_seconds: float = 3.0,
        enabled: bool = True,
        camera_backend: CameraBackend | None = None,
    ):
        self.name = name
        self.captures_root = (
            Path(captures_root)
            if captures_root is not None
            else Path.home() / ".agent-core" / "webcam" / name
        )
        audit_path = (
            Path(audit_log_path)
            if audit_log_path is not None
            else self.captures_root / "audit.jsonl"
        )
        self.audit_log = AuditLog(audit_path)
        self.default_camera_index = default_camera_index
        self.default_resolution = _to_tuple(default_resolution)
        self.max_resolution = _to_tuple(max_resolution)
        self.capture_timeout_seconds = capture_timeout_seconds
        self.enabled = enabled
        if camera_backend is None:
            from agent_core_webcam.opencv_backend import OpenCVCameraBackend
            camera_backend = OpenCVCameraBackend(timeout_seconds=capture_timeout_seconds)
        self._backend: CameraBackend = camera_backend
        self._handle: "BusHandle | None" = None

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus
        log.info("WebcamEndpoint(name=%s) started; captures=%s", self.name, self.captures_root)

    async def deliver(self, envelope: "Envelope") -> None:
        # Webcam is tool-only — envelopes addressed to us are unexpected
        # but we don't crash. Log at debug; nothing to ack (no bus handle
        # required to discard).
        log.debug("WebcamEndpoint(name=%s) ignoring delivered envelope %s", self.name, envelope.id)

    async def stop(self) -> None:
        self._handle = None
        log.info("WebcamEndpoint(name=%s) stopped", self.name)


__all__ = ["WebcamEndpoint"]
```

> Note: importing `OpenCVCameraBackend` lazily inside `__init__` means tests that pass `camera_backend=` never trigger the OpenCV import. Task 12 implements that module; until then, `WebcamEndpoint` only works in tests that inject a backend (which is exactly the path Tasks 5–11 use).

- [ ] **Step 5.5: Stub the OpenCVCameraBackend module so tests can import**

To keep the lazy import path clean before Task 12, create a placeholder `packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py`:

```python
"""Real-OpenCV CameraBackend.

Stub until Task 12 implements the cv2 adapter. Importing this module
without instantiating ``OpenCVCameraBackend`` is safe — the cv2 import
lives inside the constructor.
"""

from __future__ import annotations

from agent_core_webcam.protocol import CameraBackend, CameraInfo


class OpenCVCameraBackend:
    """Real backend (cv2). Implementation lands in Task 12."""

    def __init__(self, *, timeout_seconds: float = 3.0):
        self._timeout = timeout_seconds
        # Defer cv2 import until first use so tests that inject a fake
        # backend never need cv2 installed at the import phase.

    def list_cameras(self) -> list[CameraInfo]:
        raise NotImplementedError("OpenCVCameraBackend.list_cameras lands in Task 12")

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        raise NotImplementedError("OpenCVCameraBackend.capture lands in Task 12")


# Runtime check: protocol satisfaction (the stub raises on use, but
# the shape is intact so isinstance(...) succeeds).
_: CameraBackend = OpenCVCameraBackend()  # noqa: F841
```

- [ ] **Step 5.6: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_lifecycle.py -v`
Expected: 7 passed.

- [ ] **Step 5.7: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/endpoint.py packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py packages/agent-core-webcam/tests/conftest.py packages/agent-core-webcam/tests/test_endpoint_lifecycle.py
git commit -m "feat(webcam): WebcamEndpoint scaffold (lifecycle only, deliver no-op)"
```

---

## Task 6: capture_frame happy path (image + path + audit)

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py` (add `capture_frame` method)
- Test: `packages/agent-core-webcam/tests/test_endpoint_capture_happy.py`

Add the `capture_frame` method (Python-side; the MCP wrapper comes in Task 10). It returns a tuple of (PNG bytes, file path or None, metadata dict). The MCP wrapper turns that into `ImageContent + TextContent` later.

- [ ] **Step 6.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_endpoint_capture_happy.py`:

```python
"""capture_frame happy path: PNG bytes + disk write + audit entry."""
from __future__ import annotations

import json
import struct
from pathlib import Path

from agent_core_webcam.endpoint import WebcamEndpoint


async def test_capture_returns_png_bytes_and_path_and_metadata(endpoint: WebcamEndpoint):
    png_bytes, file_path, meta = await endpoint.capture_frame(
        camera_index=0,
        resolution=(1280, 720),
        save=True,
        note=None,
    )
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png_bytes[16:24])
    assert (width, height) == (1280, 720)
    assert file_path is not None
    assert file_path.exists()
    assert file_path.read_bytes() == png_bytes
    assert meta["camera_index"] == 0
    assert meta["resolution"] == (1280, 720)
    assert meta["filesize"] == len(png_bytes)
    assert "timestamp" in meta


async def test_capture_writes_to_date_bucketed_directory(endpoint: WebcamEndpoint):
    _, file_path, _ = await endpoint.capture_frame(camera_index=0)
    # date bucket is YYYY-MM-DD
    date_bucket = file_path.parent.name
    assert len(date_bucket) == 10
    assert date_bucket[4] == "-" and date_bucket[7] == "-"
    # filename is HHMMSS-millis.png
    assert file_path.suffix == ".png"
    stem = file_path.stem
    assert "-" in stem and stem.replace("-", "").isdigit()


async def test_capture_uses_default_camera_when_omitted(endpoint: WebcamEndpoint):
    _, _, meta = await endpoint.capture_frame()
    assert meta["camera_index"] == endpoint.default_camera_index


async def test_capture_uses_default_resolution_when_omitted(endpoint: WebcamEndpoint):
    _, _, meta = await endpoint.capture_frame()
    assert meta["resolution"] == endpoint.default_resolution


async def test_capture_appends_audit_entry(endpoint: WebcamEndpoint):
    await endpoint.capture_frame(
        camera_index=0,
        resolution=(640, 480),
        note="checking the desk",
    )
    audit_text = endpoint.audit_log.path.read_text(encoding="utf-8")
    line = audit_text.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["tool"] == "capture_webcam_frame"
    assert parsed["result"] == "ok"
    assert parsed["data"]["camera_index"] == 0
    assert parsed["data"]["resolution"] == [640, 480]
    assert parsed["data"]["note"] == "checking the desk"
    assert parsed["data"]["save"] is True
    assert "file_path" in parsed["data"]
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_capture_happy.py -v`
Expected: FAIL — `AttributeError: 'WebcamEndpoint' object has no attribute 'capture_frame'`

- [ ] **Step 6.3: Implement `capture_frame` (happy path only)**

Modify `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py`. Add to imports near the top:

```python
import asyncio
from datetime import datetime, timezone
from agent_core_webcam.audit import AuditEvent
```

Add this method on `WebcamEndpoint` (after `stop`):

```python
    async def capture_frame(
        self,
        *,
        camera_index: int | None = None,
        resolution: tuple[int, int] | list[int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> tuple[bytes, Path | None, dict]:
        """Capture one frame; return (png_bytes, file_path, metadata).

        Returns ``file_path=None`` when ``save=False``. Always appends an
        audit entry. Errors raise — Task 7 maps them to user-facing
        messages at the MCP boundary.
        """
        idx = camera_index if camera_index is not None else self.default_camera_index
        res = _to_tuple(resolution) if resolution is not None else self.default_resolution
        png_bytes = await asyncio.to_thread(self._backend.capture, idx, res)
        timestamp = datetime.now(timezone.utc).astimezone()
        file_path: Path | None = None
        if save:
            file_path = self.captures_root / timestamp.strftime("%Y-%m-%d") / (
                timestamp.strftime("%H%M%S-") + f"{timestamp.microsecond // 1000:03d}.png"
            )
            await asyncio.to_thread(self._write_png, file_path, png_bytes)
        meta = {
            "camera_index": idx,
            "resolution": res,
            "timestamp": timestamp.isoformat(),
            "filesize": len(png_bytes),
            "file_path": str(file_path) if file_path else None,
        }
        await self.audit_log.write(
            AuditEvent(
                timestamp=timestamp,
                tool="capture_webcam_frame",
                result="ok",
                data={
                    "camera_index": idx,
                    "resolution": list(res),
                    "save": save,
                    "note": note,
                    "file_path": str(file_path) if file_path else None,
                    "filesize": len(png_bytes),
                },
            )
        )
        return png_bytes, file_path, meta

    @staticmethod
    def _write_png(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
```

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_capture_happy.py -v`
Expected: 5 passed.

- [ ] **Step 6.5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/endpoint.py packages/agent-core-webcam/tests/test_endpoint_capture_happy.py
git commit -m "feat(webcam): WebcamEndpoint.capture_frame happy path with audit"
```

---

## Task 7: capture_frame error mapping

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py` (introduce error-result type)
- Test: `packages/agent-core-webcam/tests/test_endpoint_capture_errors.py`

Define a result type that distinguishes success from error so the MCP wrapper (Task 10) can return clean text content on failure without raising. Each error gets a user-readable message + an audit entry with `result: "error"`.

- [ ] **Step 7.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_endpoint_capture_errors.py`:

```python
"""Error-path mapping for capture_frame.

Each failure mode in the spec's error-handling table maps to:
- A ``CaptureError`` result with a user-readable message
- An audit entry with ``result: "error"`` plus structured detail
"""
from __future__ import annotations

import json

from agent_core_webcam.endpoint import (
    CaptureError,
    CaptureSuccess,
    WebcamEndpoint,
)
from agent_core_webcam.fake import FakeCameraBackend


async def test_disabled_endpoint_returns_kill_switch_error(tmp_path, fake_backend):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        enabled=False,
        camera_backend=fake_backend,
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureError)
    assert "disabled" in result.message
    assert "enabled=false" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["result"] == "error"
    assert line["data"]["error"] == "endpoint disabled"


async def test_camera_not_found_returns_clean_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]),
    )
    result = await ep.capture_frame_safe(camera_index=5)
    assert isinstance(result, CaptureError)
    assert "no camera at index 5" in result.message
    assert "list_cameras" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "camera 5 not found"


async def test_camera_busy_returns_clean_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0]).with_busy(0),
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureError)
    assert "busy" in result.message.lower()
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "camera busy"


async def test_read_timeout_returns_clean_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        capture_timeout_seconds=2.5,
        camera_backend=FakeCameraBackend.with_cameras([0]).with_read_timeout(0),
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureError)
    assert "no frame" in result.message.lower()
    assert "2.5" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "read timeout"


async def test_resolution_exceeds_max_returns_error(tmp_path, fake_backend):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        max_resolution=(1920, 1080),
        camera_backend=fake_backend,
    )
    result = await ep.capture_frame_safe(camera_index=0, resolution=(7680, 4320))
    assert isinstance(result, CaptureError)
    assert "exceeds configured max" in result.message
    assert "7680x4320" in result.message
    assert "1920x1080" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "resolution capped"


async def test_disk_write_failure_returns_error(tmp_path, fake_backend, monkeypatch):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=fake_backend,
    )

    def _broken_write(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(WebcamEndpoint, "_write_png", staticmethod(_broken_write))
    result = await ep.capture_frame_safe(camera_index=0, save=True)
    assert isinstance(result, CaptureError)
    assert "disk full" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"].startswith("disk write failed")


async def test_success_path_returns_capture_success(tmp_path, fake_backend):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=fake_backend,
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureSuccess)
    assert result.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.file_path is not None
```

- [ ] **Step 7.2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_capture_errors.py -v`
Expected: FAIL — `ImportError: cannot import name 'CaptureError'`

- [ ] **Step 7.3: Implement `CaptureSuccess`/`CaptureError` + `capture_frame_safe`**

Modify `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py`. Add to imports at the top:

```python
from dataclasses import dataclass

from agent_core_webcam.protocol import (
    CameraBusyError,
    CameraNotFoundError,
    ReadTimeoutError,
)
```

Add these dataclasses above the `WebcamEndpoint` class definition:

```python
@dataclass(frozen=True)
class CaptureSuccess:
    """Successful capture result."""

    png_bytes: bytes
    file_path: Path | None
    metadata: dict


@dataclass(frozen=True)
class CaptureError:
    """Failed capture — message is the agent-readable string."""

    message: str
```

Add this method on `WebcamEndpoint` (next to `capture_frame`):

```python
    async def capture_frame_safe(
        self,
        *,
        camera_index: int | None = None,
        resolution: tuple[int, int] | list[int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> "CaptureSuccess | CaptureError":
        """Like ``capture_frame`` but maps every error to a CaptureError + audit.

        The MCP tool wrapper uses this — it never wants to raise into FastMCP.
        """
        if not self.enabled:
            return await self._error(
                user_message=(
                    "error: webcam endpoint is disabled (enabled=false in config). "
                    "Ask the operator if this is unexpected."
                ),
                audit_error="endpoint disabled",
                camera_index=camera_index,
                save=save,
                note=note,
            )

        # Resolution cap.
        idx = camera_index if camera_index is not None else self.default_camera_index
        res = _to_tuple(resolution) if resolution is not None else self.default_resolution
        if res[0] > self.max_resolution[0] or res[1] > self.max_resolution[1]:
            return await self._error(
                user_message=(
                    f"error: requested resolution {res[0]}x{res[1]} exceeds "
                    f"configured max {self.max_resolution[0]}x{self.max_resolution[1]}."
                ),
                audit_error="resolution capped",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )

        try:
            png_bytes, file_path, meta = await self.capture_frame(
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except CameraNotFoundError:
            available = [c.index for c in self._backend.list_cameras()]
            return await self._error(
                user_message=(
                    f"error: no camera at index {idx} "
                    f"(host has {len(available)} cameras: indices {available}). "
                    f"Call list_cameras to see what's available."
                ),
                audit_error=f"camera {idx} not found",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except CameraBusyError:
            cam_name = next(
                (c.name for c in self._backend.list_cameras() if c.index == idx),
                f"camera {idx}",
            )
            return await self._error(
                user_message=(
                    f"error: camera {idx} ({cam_name}) is busy. "
                    f"Likely in use by another application (Zoom, browser, etc.). "
                    f"Try again in a moment."
                ),
                audit_error="camera busy",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except ReadTimeoutError:
            return await self._error(
                user_message=(
                    f"error: camera opened but returned no frame within "
                    f"{self.capture_timeout_seconds}s. Camera may be initializing or obstructed."
                ),
                audit_error="read timeout",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except OSError as exc:
            return await self._error(
                user_message=(
                    f"error: capture succeeded but failed to write to disk: {exc}. "
                    f"Image is unavailable; retry or set save=false."
                ),
                audit_error=f"disk write failed: {exc}",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        return CaptureSuccess(png_bytes=png_bytes, file_path=file_path, metadata=meta)

    async def _error(
        self,
        *,
        user_message: str,
        audit_error: str,
        camera_index: int | None = None,
        resolution: tuple[int, int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> "CaptureError":
        timestamp = datetime.now(timezone.utc).astimezone()
        await self.audit_log.write(
            AuditEvent(
                timestamp=timestamp,
                tool="capture_webcam_frame",
                result="error",
                data={
                    "camera_index": camera_index,
                    "resolution": list(resolution) if resolution else None,
                    "save": save,
                    "note": note,
                    "error": audit_error,
                },
            )
        )
        return CaptureError(message=user_message)
```

Update the `__all__` at the bottom of `endpoint.py`:

```python
__all__ = ["CaptureError", "CaptureSuccess", "WebcamEndpoint"]
```

- [ ] **Step 7.4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_capture_errors.py -v`
Expected: 7 passed.

- [ ] **Step 7.5: Run all webcam tests to confirm no regressions**

Run: `uv run pytest packages/agent-core-webcam/tests/ -v`
Expected: all green (Tasks 1–7 tests).

- [ ] **Step 7.6: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/endpoint.py packages/agent-core-webcam/tests/test_endpoint_capture_errors.py
git commit -m "feat(webcam): map capture errors to CaptureError + audit"
```

---

## Task 8: capture_frame save=False

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py` (already supports it from Task 6)
- Test: `packages/agent-core-webcam/tests/test_endpoint_capture_save_false.py`

`save=False` returns the PNG bytes inline but writes nothing to disk and emits an audit entry where `file_path` is null. The endpoint already implements this in Task 6; this task just locks it in with explicit tests.

- [ ] **Step 8.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_endpoint_capture_save_false.py`:

```python
"""save=False — capture returns bytes inline but does not touch disk."""
from __future__ import annotations

import json

from agent_core_webcam.endpoint import CaptureSuccess, WebcamEndpoint


async def test_save_false_returns_png_with_no_file(endpoint: WebcamEndpoint):
    result = await endpoint.capture_frame_safe(camera_index=0, save=False)
    assert isinstance(result, CaptureSuccess)
    assert result.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.file_path is None
    # The captures dir might exist from a previous test run; the important
    # assertion is that THIS capture wrote nothing under it.
    if endpoint.captures_root.exists():
        files = [p for p in endpoint.captures_root.rglob("*.png")]
        assert files == []


async def test_save_false_audit_records_save_flag_and_null_file_path(endpoint: WebcamEndpoint):
    await endpoint.capture_frame_safe(camera_index=0, save=False)
    line = json.loads(endpoint.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["result"] == "ok"
    assert line["data"]["save"] is False
    assert line["data"]["file_path"] is None
```

- [ ] **Step 8.2: Run tests to verify they pass (already implemented in Task 6)**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_capture_save_false.py -v`
Expected: 2 passed.

> If a test fails here, the Task 6 implementation has a bug — fix it before moving on. The expected case is that `save=False` already works because Task 6's `capture_frame` returns `file_path=None` when `save` is False.

- [ ] **Step 8.3: Commit**

```bash
git add packages/agent-core-webcam/tests/test_endpoint_capture_save_false.py
git commit -m "test(webcam): pin save=False contract for capture_frame"
```

---

## Task 9: Concurrency lock + list_cameras

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py` (add `asyncio.Lock` per camera + `list_cameras_safe`)
- Test: `packages/agent-core-webcam/tests/test_endpoint_concurrency.py`
- Test: `packages/agent-core-webcam/tests/test_endpoint_list_cameras.py`

Two concerns in one task because they're both small. Concurrency: serialize same-camera captures via a lock keyed by `camera_index`; allow parallel captures across different cameras. List_cameras: thin wrapper over the backend's `list_cameras` with audit + kill-switch handling.

- [ ] **Step 9.1: Write the concurrency test**

Create `packages/agent-core-webcam/tests/test_endpoint_concurrency.py`:

```python
"""Concurrency: same camera serializes; different cameras parallel."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend


@dataclass
class _SlowFake:
    delay: float
    available_indices: list[int]
    busy_calls: dict[int, list[tuple[float, float]]] = None  # type: ignore

    def __post_init__(self):
        self.busy_calls = {i: [] for i in self.available_indices}

    def list_cameras(self):
        return FakeCameraBackend.with_cameras(self.available_indices).list_cameras()

    def capture(self, index, resolution):
        if index not in self.available_indices:
            from agent_core_webcam.protocol import CameraNotFoundError
            raise CameraNotFoundError(f"camera {index} not configured")
        start = time.monotonic()
        time.sleep(self.delay)  # blocking sleep — runs in to_thread
        end = time.monotonic()
        self.busy_calls[index].append((start, end))
        return FakeCameraBackend.with_cameras([index]).capture(index, resolution)


async def test_same_camera_calls_serialize(tmp_path):
    backend = _SlowFake(delay=0.10, available_indices=[0])
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=backend,
    )
    # Two parallel capture calls on the SAME camera should NOT overlap.
    await asyncio.gather(
        ep.capture_frame_safe(camera_index=0),
        ep.capture_frame_safe(camera_index=0),
    )
    intervals = backend.busy_calls[0]
    assert len(intervals) == 2
    # Second call's start must be >= first call's end (no overlap).
    assert intervals[1][0] >= intervals[0][1] - 0.005  # 5ms slop


async def test_different_cameras_run_in_parallel(tmp_path):
    backend = _SlowFake(delay=0.10, available_indices=[0, 1])
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=backend,
    )
    start = time.monotonic()
    await asyncio.gather(
        ep.capture_frame_safe(camera_index=0),
        ep.capture_frame_safe(camera_index=1),
    )
    elapsed = time.monotonic() - start
    # If serialized, would take ~0.20s. Parallel should be ~0.10s.
    # Use 0.18 as the threshold to guard against system jitter.
    assert elapsed < 0.18
```

- [ ] **Step 9.2: Run the test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_concurrency.py -v`
Expected: FAIL — `test_same_camera_calls_serialize` (intervals overlap because no lock yet).

- [ ] **Step 9.3: Add per-camera lock to capture_frame**

Modify `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py`. In `WebcamEndpoint.__init__` (at the end), add:

```python
        self._camera_locks: dict[int, asyncio.Lock] = {}
        self._camera_locks_guard = asyncio.Lock()
```

Add a helper method on `WebcamEndpoint`:

```python
    async def _lock_for(self, camera_index: int) -> asyncio.Lock:
        async with self._camera_locks_guard:
            if camera_index not in self._camera_locks:
                self._camera_locks[camera_index] = asyncio.Lock()
            return self._camera_locks[camera_index]
```

Wrap the existing `capture_frame` body with the lock. Replace the `png_bytes = await asyncio.to_thread(...)` line with:

```python
        lock = await self._lock_for(idx)
        async with lock:
            png_bytes = await asyncio.to_thread(self._backend.capture, idx, res)
```

- [ ] **Step 9.4: Run the test to verify it passes**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_concurrency.py -v`
Expected: 2 passed.

- [ ] **Step 9.5: Write the list_cameras test**

Create `packages/agent-core-webcam/tests/test_endpoint_list_cameras.py`:

```python
"""list_cameras_safe: enumeration + kill switch + audit."""
from __future__ import annotations

import json

from agent_core_webcam.endpoint import (
    ListCamerasError,
    ListCamerasSuccess,
    WebcamEndpoint,
)
from agent_core_webcam.fake import FakeCameraBackend


async def test_list_returns_enumeration(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]),
    )
    result = await ep.list_cameras_safe()
    assert isinstance(result, ListCamerasSuccess)
    assert [c.index for c in result.cameras] == [0, 1]
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["tool"] == "list_cameras"
    assert line["result"] == "ok"
    assert line["data"]["camera_count"] == 2


async def test_list_returns_partial_failure_subset(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]).with_busy(1),
    )
    result = await ep.list_cameras_safe()
    assert isinstance(result, ListCamerasSuccess)
    by_idx = {c.index: c for c in result.cameras}
    assert by_idx[0].available is True
    assert by_idx[1].available is False


async def test_list_disabled_returns_kill_switch_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        enabled=False,
        camera_backend=FakeCameraBackend(),
    )
    result = await ep.list_cameras_safe()
    assert isinstance(result, ListCamerasError)
    assert "disabled" in result.message
```

- [ ] **Step 9.6: Run the test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_endpoint_list_cameras.py -v`
Expected: FAIL — `ImportError: cannot import name 'ListCamerasSuccess'`.

- [ ] **Step 9.7: Implement list_cameras_safe**

Modify `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py`. Add new dataclasses next to `CaptureSuccess`/`CaptureError`:

```python
@dataclass(frozen=True)
class ListCamerasSuccess:
    """list_cameras_safe success."""

    cameras: list  # list[CameraInfo] — avoid forward-ref import dance


@dataclass(frozen=True)
class ListCamerasError:
    message: str
```

Add the method on `WebcamEndpoint`:

```python
    async def list_cameras_safe(self) -> "ListCamerasSuccess | ListCamerasError":
        """Enumerate cameras, return list with audit. Best-effort — never raises."""
        if not self.enabled:
            timestamp = datetime.now(timezone.utc).astimezone()
            await self.audit_log.write(
                AuditEvent(
                    timestamp=timestamp,
                    tool="list_cameras",
                    result="error",
                    data={"error": "endpoint disabled"},
                )
            )
            return ListCamerasError(
                message=(
                    "error: webcam endpoint is disabled (enabled=false in config). "
                    "Ask the operator if this is unexpected."
                )
            )
        cams = await asyncio.to_thread(self._backend.list_cameras)
        timestamp = datetime.now(timezone.utc).astimezone()
        await self.audit_log.write(
            AuditEvent(
                timestamp=timestamp,
                tool="list_cameras",
                result="ok",
                data={"camera_count": len(cams)},
            )
        )
        return ListCamerasSuccess(cameras=list(cams))
```

Update `__all__`:

```python
__all__ = [
    "CaptureError",
    "CaptureSuccess",
    "ListCamerasError",
    "ListCamerasSuccess",
    "WebcamEndpoint",
]
```

- [ ] **Step 9.8: Run all webcam tests**

Run: `uv run pytest packages/agent-core-webcam/tests/ -v`
Expected: all green.

- [ ] **Step 9.9: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/endpoint.py packages/agent-core-webcam/tests/test_endpoint_concurrency.py packages/agent-core-webcam/tests/test_endpoint_list_cameras.py
git commit -m "feat(webcam): per-camera asyncio.Lock + list_cameras_safe"
```

---

## Task 10: MCP tool registration

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/mcp.py`
- Test: `packages/agent-core-webcam/tests/test_mcp_register.py`

`register_webcam_tools(mcp, endpoint)` registers the two tools on a FastMCP server. Each tool returns a list of MCP content blocks: `ImageContent + TextContent` on success, just `TextContent` on error.

- [ ] **Step 10.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_mcp_register.py`:

```python
"""register_webcam_tools mounts the two tools on a FastMCP server."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend
from agent_core_webcam.mcp import register_webcam_tools
from fastmcp import FastMCP


def _new_endpoint(tmp_path: Path, **overrides) -> WebcamEndpoint:
    return WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]),
        **overrides,
    )


async def test_registers_both_tools(tmp_path: Path):
    mcp = FastMCP("test-server")
    register_webcam_tools(mcp=mcp, endpoint=_new_endpoint(tmp_path))
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "capture_webcam_frame" in names
    assert "list_cameras" in names


async def test_capture_tool_returns_image_and_text(tmp_path: Path):
    mcp = FastMCP("test-server")
    ep = _new_endpoint(tmp_path)
    register_webcam_tools(mcp=mcp, endpoint=ep)
    # Call the tool through FastMCP's in-process invoke path.
    result = await mcp._mcp_call_tool("capture_webcam_frame", {"camera_index": 0})
    types = [getattr(b, "type", None) for b in result]
    assert "image" in types
    assert "text" in types


async def test_capture_tool_error_returns_text_only(tmp_path: Path):
    mcp = FastMCP("test-server")
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        enabled=False,
        camera_backend=FakeCameraBackend.with_cameras([0]),
    )
    register_webcam_tools(mcp=mcp, endpoint=ep)
    result = await mcp._mcp_call_tool("capture_webcam_frame", {"camera_index": 0})
    types = [getattr(b, "type", None) for b in result]
    # Error path: only text, no image.
    assert "image" not in types
    assert "text" in types
    text_block = next(b for b in result if getattr(b, "type", None) == "text")
    assert "disabled" in text_block.text


async def test_list_cameras_tool_returns_json_text(tmp_path: Path):
    mcp = FastMCP("test-server")
    register_webcam_tools(mcp=mcp, endpoint=_new_endpoint(tmp_path))
    result = await mcp._mcp_call_tool("list_cameras", {})
    text_block = next(b for b in result if getattr(b, "type", None) == "text")
    assert "0" in text_block.text  # at minimum, camera index 0 in the JSON


async def test_capture_tool_resolution_validation_at_mcp_boundary(tmp_path: Path):
    """Pydantic should reject a malformed resolution before reaching the endpoint."""
    mcp = FastMCP("test-server")
    register_webcam_tools(mcp=mcp, endpoint=_new_endpoint(tmp_path))
    with pytest.raises(Exception):
        # A dict where a [w, h] list is expected — should fail validation.
        await mcp._mcp_call_tool(
            "capture_webcam_frame",
            {"camera_index": 0, "resolution": "not-a-list"},
        )
```

- [ ] **Step 10.2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_mcp_register.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core_webcam.mcp'`.

- [ ] **Step 10.3: Implement register_webcam_tools**

Create `packages/agent-core-webcam/src/agent_core_webcam/mcp.py`:

```python
"""Agent-facing MCP tool surface for the webcam endpoint.

Two tools: ``capture_webcam_frame`` and ``list_cameras``. Each returns
a list of MCP content blocks. On error, only TextContent is returned —
no image. The endpoint's ``capture_frame_safe`` / ``list_cameras_safe``
methods do the error mapping; this module just adapts the result type
to MCP content blocks.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

from mcp.types import ImageContent, TextContent

from agent_core_webcam.endpoint import (
    CaptureError,
    CaptureSuccess,
    ListCamerasError,
    ListCamerasSuccess,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from agent_core_webcam.endpoint import WebcamEndpoint


def register_webcam_tools(*, mcp: "FastMCP", endpoint: "WebcamEndpoint") -> None:
    """Register the two webcam tools on a FastMCP server."""

    @mcp.tool(
        name="capture_webcam_frame",
        description=(
            "Capture a single frame from a connected webcam. Returns the image "
            "inline (so you can see it immediately) plus a saved file path you "
            "can later attach to a Discord message, archive, or revisit. Use "
            "camera_index=0 for the default camera; call list_cameras to "
            "enumerate other devices."
        ),
    )
    async def _capture(
        camera_index: int = 0,
        resolution: list[int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> list[Any]:
        """Capture one frame; return [ImageContent, TextContent] on success."""
        res_tuple = tuple(resolution) if resolution else None
        result = await endpoint.capture_frame_safe(
            camera_index=camera_index,
            resolution=res_tuple,
            save=save,
            note=note,
        )
        if isinstance(result, CaptureError):
            return [TextContent(type="text", text=result.message)]
        assert isinstance(result, CaptureSuccess)
        meta = result.metadata
        cam_name = next(
            (c.name for c in endpoint._backend.list_cameras() if c.index == meta["camera_index"]),
            f"camera {meta['camera_index']}",
        )
        text = (
            f"Captured frame from camera {meta['camera_index']} ({cam_name}) "
            f"at {meta['resolution'][0]}x{meta['resolution'][1]}.\n"
            f"Path: {meta['file_path']}\n"
            f"Timestamp: {meta['timestamp']}\n"
            f"Filesize: {meta['filesize']} bytes"
        )
        return [
            ImageContent(
                type="image",
                data=base64.b64encode(result.png_bytes).decode("ascii"),
                mimeType="image/png",
            ),
            TextContent(type="text", text=text),
        ]

    @mcp.tool(
        name="list_cameras",
        description=(
            "List all webcams detected on this host. Use the returned `index` "
            "as the `camera_index` argument to capture_webcam_frame."
        ),
    )
    async def _list_cameras() -> list[Any]:
        result = await endpoint.list_cameras_safe()
        if isinstance(result, ListCamerasError):
            return [TextContent(type="text", text=result.message)]
        assert isinstance(result, ListCamerasSuccess)
        payload = [
            {"index": c.index, "name": c.name, "available": c.available}
            for c in result.cameras
        ]
        return [TextContent(type="text", text=json.dumps(payload))]


__all__ = ["register_webcam_tools"]
```

- [ ] **Step 10.4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-webcam/tests/test_mcp_register.py -v`
Expected: 5 passed.

> If `_mcp_call_tool` is not the right invoker name in the FastMCP version installed, replace with whatever the in-process invoke method is. Try `await mcp.call_tool(...)` as the fallback. Both forms exist in the broader FastMCP lineage; use whichever the installed version exposes.

- [ ] **Step 10.5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/mcp.py packages/agent-core-webcam/tests/test_mcp_register.py
git commit -m "feat(webcam): register_webcam_tools — capture_webcam_frame + list_cameras"
```

---

## Task 11: Plugin hookimpls + cross-endpoint wiring

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/plugin.py`
- Test: `packages/agent-core-webcam/tests/test_mcp_wiring.py`

The pluggy plugin: registers the endpoint type, declares the reserved param, and implements `wire_endpoints_after_registration` so any `ClaudeCodeMCPEndpoint` whose yaml says `webcam: <name>` gets a deferred mounter that registers the webcam tools at `bus.start()`. Mirrors `agent-core-briefs/plugin.py` exactly.

- [ ] **Step 11.1: Write the failing test**

Create `packages/agent-core-webcam/tests/test_mcp_wiring.py`:

```python
"""Cross-endpoint MCP wiring — Pepper's MCP session gets the two webcam tools.

Mirrors agent-core-briefs/tests/test_mcp_wiring.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend
from agent_core_webcam.plugin import wire_endpoints_after_registration

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.plugins.specs import RunnerServices


_EXPECTED = {"capture_webcam_frame", "list_cameras"}


async def test_pepper_mcp_gets_two_webcam_tools_after_bus_start(tmp_path: Path):
    webcam_ep = WebcamEndpoint(
        name="webcam-pepper",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0]),
    )
    mcp_ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    assert mcp_ep.deferred_tool_mounters == []

    bus = Bus(
        BusConfig(
            storage_path=tmp_path / "bus.sqlite",
            redelivery_timeout_seconds=60,
            max_delivery_attempts=2,
            ttl_sweep_seconds=3600,
            redelivery_sweep_seconds=3600,
        )
    )
    bus.register(EndpointSpec(endpoint=webcam_ep, description="webcam"))
    bus.register(EndpointSpec(endpoint=mcp_ep, description="pepper MCP"))

    services = RunnerServices(notify_broker=NotificationBroker())
    wire_endpoints_after_registration(
        endpoints={"webcam-pepper": webcam_ep, "pepper": mcp_ep},
        raw_endpoint_configs={
            "webcam-pepper": {"name": "webcam-pepper", "type": "builtin.webcam", "params": {}},
            "pepper": {
                "name": "pepper",
                "type": "builtin.claude_code_mcp",
                "params": {"mount": "/mcp/pepper", "webcam": "webcam-pepper"},
            },
        },
        services=services,
    )
    assert len(mcp_ep.deferred_tool_mounters) == 1

    try:
        await bus.start()
        tools = await mcp_ep._mcp.list_tools()
        names = {t.name for t in tools}
        missing = _EXPECTED - names
        assert not missing, f"missing webcam tools: {sorted(missing)}, registered: {sorted(names)}"
    finally:
        await bus.stop()


async def test_wire_skips_mcp_without_webcam_param(tmp_path: Path):
    mcp_ep = ClaudeCodeMCPEndpoint(name="other-agent", mount="/mcp/other-agent")
    services = RunnerServices(notify_broker=NotificationBroker())
    wire_endpoints_after_registration(
        endpoints={"other-agent": mcp_ep},
        raw_endpoint_configs={
            "other-agent": {
                "name": "other-agent",
                "type": "builtin.claude_code_mcp",
                "params": {"mount": "/mcp/other-agent"},
            },
        },
        services=services,
    )
    assert mcp_ep.deferred_tool_mounters == []


async def test_wire_raises_on_unknown_webcam_name(tmp_path: Path):
    mcp_ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    services = RunnerServices(notify_broker=NotificationBroker())
    with pytest.raises(
        ValueError,
        match=r"webcam='not\.real'.*Available WebcamEndpoint names: \[\]",
    ):
        wire_endpoints_after_registration(
            endpoints={"pepper": mcp_ep},
            raw_endpoint_configs={
                "pepper": {
                    "name": "pepper",
                    "type": "builtin.claude_code_mcp",
                    "params": {"mount": "/mcp/pepper", "webcam": "not.real"},
                },
            },
            services=services,
        )


async def test_wire_raises_when_webcam_name_points_at_wrong_type(tmp_path: Path):
    mcp_ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    other_ep = ClaudeCodeMCPEndpoint(name="not-a-webcam", mount="/mcp/other")
    services = RunnerServices(notify_broker=NotificationBroker())
    with pytest.raises(
        ValueError,
        match=r"not a WebcamEndpoint.*Available WebcamEndpoint names: \[\]",
    ):
        wire_endpoints_after_registration(
            endpoints={"pepper": mcp_ep, "not-a-webcam": other_ep},
            raw_endpoint_configs={
                "pepper": {
                    "name": "pepper",
                    "type": "builtin.claude_code_mcp",
                    "params": {"mount": "/mcp/pepper", "webcam": "not-a-webcam"},
                },
                "not-a-webcam": {
                    "name": "not-a-webcam",
                    "type": "builtin.claude_code_mcp",
                    "params": {"mount": "/mcp/other"},
                },
            },
            services=services,
        )
```

- [ ] **Step 11.2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-webcam/tests/test_mcp_wiring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core_webcam.plugin'`.

- [ ] **Step 11.3: Implement the plugin**

Create `packages/agent-core-webcam/src/agent_core_webcam/plugin.py`:

```python
"""Agent_core entry-point hook surface for the webcam framework.

Three hookimpls:

* ``register_endpoint_types`` — exposes ``builtin.webcam`` so the bus
  runner can construct a ``WebcamEndpoint`` from a yaml entry.
* ``reserved_endpoint_params`` — declares ``webcam`` so the runner pops
  it from claude_code_mcp's params before constructing.
* ``wire_endpoints_after_registration`` — pairs every
  ``ClaudeCodeMCPEndpoint`` whose yaml params name a webcam endpoint
  with that endpoint instance, appending a deferred mounter that
  registers the two webcam tools on the FastMCP server at
  ``bus.start()`` time.
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
    """Register ``builtin.webcam`` as a bus endpoint type."""
    from agent_core_webcam.endpoint import WebcamEndpoint

    return {"builtin.webcam": WebcamEndpoint}


@hookimpl
def reserved_endpoint_params() -> list[str]:
    """The runner pops these keys from each endpoint's params before constructing."""
    return ["webcam"]


@hookimpl
def wire_endpoints_after_registration(
    endpoints: dict[str, "Endpoint"],
    raw_endpoint_configs: dict[str, dict[str, Any]],
    services: "RunnerServices",
) -> None:
    """Mount webcam tools on every MCP endpoint that names a webcam endpoint."""
    del services  # unused
    from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
    from agent_core_webcam.endpoint import WebcamEndpoint
    from agent_core_webcam.mcp import register_webcam_tools

    for name, endpoint in endpoints.items():
        if not isinstance(endpoint, ClaudeCodeMCPEndpoint):
            continue
        raw = raw_endpoint_configs.get(name) or {}
        params = raw.get("params") or {}
        webcam_name = params.get("webcam")
        if not webcam_name:
            continue
        webcam = endpoints.get(webcam_name)
        available = sorted(n for n, e in endpoints.items() if isinstance(e, WebcamEndpoint))
        if webcam is None:
            raise ValueError(
                f"endpoint {name!r} names webcam={webcam_name!r} but no endpoint with "
                f"that name is registered. Available WebcamEndpoint names: {available}"
            )
        if not isinstance(webcam, WebcamEndpoint):
            raise ValueError(
                f"endpoint {name!r} names webcam={webcam_name!r}, but that endpoint is "
                f"a {type(webcam).__name__}, not a WebcamEndpoint. "
                f"Available WebcamEndpoint names: {available}"
            )

        def _mounter(
            bus_handle,
            *,
            webcam: WebcamEndpoint = webcam,
            mcp_endpoint: ClaudeCodeMCPEndpoint = endpoint,
        ) -> None:
            del bus_handle  # webcam tools don't publish onto the bus
            register_webcam_tools(mcp=mcp_endpoint._mcp, endpoint=webcam)

        endpoint.deferred_tool_mounters.append(_mounter)
```

- [ ] **Step 11.4: Run test to verify it passes**

Run: `uv run pytest packages/agent-core-webcam/tests/test_mcp_wiring.py -v`
Expected: 4 passed.

- [ ] **Step 11.5: Run full webcam test suite**

Run: `uv run pytest packages/agent-core-webcam/tests/ -v`
Expected: all green.

- [ ] **Step 11.6: Verify the plugin is discoverable via entry-points**

Run: `uv run python -c "import pluggy; pm = pluggy.PluginManager('agent_core'); pm.load_setuptools_entrypoints('agent_core'); names = [p.__name__ for p in pm.get_plugins()]; print(names); assert 'agent_core_webcam.plugin' in names"`
Expected: prints a list including `agent_core_webcam.plugin`.

- [ ] **Step 11.7: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/plugin.py packages/agent-core-webcam/tests/test_mcp_wiring.py
git commit -m "feat(webcam): pluggy plugin + cross-endpoint MCP wiring"
```

---

## Task 12: OpenCVCameraBackend (real implementation)

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py`
- Test: `packages/agent-core-webcam/tests/test_real_opencv.py`

Replace the stub from Task 5 with a real `cv2`-backed implementation. Capture flow: open `cv2.VideoCapture(index)`, set width/height, `read()`, release, BGR→RGB convert, encode PNG. Map cv2's failure shapes onto our exception taxonomy.

- [ ] **Step 12.1: Write the gated integration test**

Create `packages/agent-core-webcam/tests/test_real_opencv.py`:

```python
"""Real-OpenCV integration test (gated).

Skipped by default. Set WEBCAM_INTEGRATION_TEST=1 to enable. Verifies
the cv2 adapter compiles and behaves consistently with the Fake backend
for the no-camera-present case.
"""
from __future__ import annotations

import os

import pytest
from agent_core_webcam.opencv_backend import OpenCVCameraBackend
from agent_core_webcam.protocol import CameraNotFoundError


pytestmark = pytest.mark.skipif(
    os.environ.get("WEBCAM_INTEGRATION_TEST") != "1",
    reason="set WEBCAM_INTEGRATION_TEST=1 to run real cv2 tests",
)


def test_capture_unknown_index_raises_not_found():
    """Asking for a clearly absent camera index raises CameraNotFoundError.
    Uses index=99 which essentially never exists. If a host actually has 100
    cameras, switch to a higher number. If THAT also fails, we'll laugh."""
    backend = OpenCVCameraBackend(timeout_seconds=2.0)
    with pytest.raises(CameraNotFoundError):
        backend.capture(99, (640, 480))


def test_list_cameras_returns_a_list():
    """Just verifies the call returns without raising. Result count is
    host-dependent; at minimum it should be a list."""
    backend = OpenCVCameraBackend(timeout_seconds=2.0)
    cams = backend.list_cameras()
    assert isinstance(cams, list)
```

- [ ] **Step 12.2: Verify the test is currently skipped**

Run: `uv run pytest packages/agent-core-webcam/tests/test_real_opencv.py -v`
Expected: 2 skipped.

- [ ] **Step 12.3: Replace the OpenCV stub with the real implementation**

Replace the contents of `packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py`:

```python
"""Real-OpenCV CameraBackend.

Imports cv2 lazily inside method bodies so test environments that inject
a fake backend never need cv2 at module-import time. cv2's failure modes
are mapped onto our protocol exception taxonomy.
"""

from __future__ import annotations

import logging

from agent_core_webcam.protocol import (
    CameraBackend,
    CameraBusyError,
    CameraInfo,
    CameraNotFoundError,
    ReadTimeoutError,
)

log = logging.getLogger(__name__)

# Probe up to this many camera indices when listing. Most consumer hosts
# have 0–2 cameras; 8 is a generous ceiling without making list_cameras
# pay 100ms-each-failed-open across hundreds of indices.
_MAX_PROBE_INDEX = 8


class OpenCVCameraBackend:
    """cv2-backed CameraBackend.

    Open + release per call. No long-lived state — the OS hardware LED
    reflects "agent is looking right now" honestly.
    """

    def __init__(self, *, timeout_seconds: float = 3.0):
        self._timeout = timeout_seconds

    def list_cameras(self) -> list[CameraInfo]:
        import cv2  # lazy import so cv2 isn't required by importing this module

        out: list[CameraInfo] = []
        for idx in range(_MAX_PROBE_INDEX):
            cap = cv2.VideoCapture(idx)
            if cap is None:
                continue
            opened = cap.isOpened()
            if not opened:
                cap.release()
                # No camera at this index — stop probing further indices.
                # On Windows DirectShow, indices are dense from 0 upward.
                if idx == 0:
                    return []
                break
            name = self._best_effort_name(cap, idx)
            cap.release()
            out.append(CameraInfo(index=idx, name=name, available=True))
        return out

    @staticmethod
    def _best_effort_name(cap, idx: int) -> str:
        # cv2 doesn't expose device names cross-platform. On Windows
        # CAP_DSHOW we could probe via WMI but that's a rabbit hole.
        # Fall back to a generic label.
        return f"Camera {idx}"

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        import cv2

        cap = cv2.VideoCapture(index)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            raise CameraNotFoundError(f"camera {index} could not be opened")
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
            # Honor the timeout via cv2's own read; cv2 doesn't expose a
            # blocking timeout per se, so we rely on cv2 returning False
            # when no frame is available. A truly hung driver would
            # require process-level handling — out of scope for v1.
            ok, frame = cap.read()
            if not ok or frame is None:
                # On Windows, "device opened but read failed" most
                # commonly means another process holds the video pin
                # exclusively (Zoom, browser camera, etc.).
                raise CameraBusyError(f"camera {index} opened but read failed")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ok2, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            if not ok2:
                raise ReadTimeoutError(f"camera {index} returned a frame but PNG encode failed")
            return bytes(buf.tobytes())
        finally:
            cap.release()


# Static protocol satisfaction check (running the constructor is fine —
# cv2 is lazy-loaded inside method bodies).
_: CameraBackend = OpenCVCameraBackend()  # noqa: F841


__all__ = ["OpenCVCameraBackend"]
```

> Notes for implementers:
> - cv2 stores frames in BGR; we convert to RGB then back to BGR before encoding so `cv2.imencode(".png", ...)` produces a sRGB-correct PNG (cv2 stores BGR in PNG by convention; the round-trip canonicalizes).
> - `_MAX_PROBE_INDEX = 8` is a guess. Bump if Jeff's setup grows past that; document if so.

- [ ] **Step 12.4: Verify all webcam tests still pass**

Run: `uv run pytest packages/agent-core-webcam/tests/ -v`
Expected: all green; the gated integration test is still skipped.

- [ ] **Step 12.5: (Optional, manual) Run the gated integration test**

Run: `WEBCAM_INTEGRATION_TEST=1 uv run pytest packages/agent-core-webcam/tests/test_real_opencv.py -v` (PowerShell: `$env:WEBCAM_INTEGRATION_TEST=1; uv run pytest ...; Remove-Item Env:WEBCAM_INTEGRATION_TEST`).
Expected: 2 passed (`test_capture_unknown_index_raises_not_found` + `test_list_cameras_returns_a_list`).

- [ ] **Step 12.6: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py packages/agent-core-webcam/tests/test_real_opencv.py
git commit -m "feat(webcam): OpenCVCameraBackend (real cv2 implementation)"
```

---

## Task 13: End-to-end smoke + Pepper rollout instructions

**Files:**
- Modify: `~/.agent-core/agent_core.yaml` (operator config; documented here, not committed)
- Manual smoke test against running daemon

Plug the new endpoint into Pepper's daemon config and verify end-to-end. This task is operator-side; it doesn't change source files but does land the rollout.

- [ ] **Step 13.1: Add yaml entries to Pepper's daemon config**

Edit `C:\Users\jeffr\.agent-core\agent_core.yaml`. Locate the existing `pepper` MCP endpoint and add `webcam: webcam-pepper` under its `params`. Then add a new endpoint entry below it:

```yaml
  - type: builtin.claude_code_mcp
    name: pepper
    description: "Pepper's MCP endpoint — flipped to the bus 2026-05-06."
    params:
      mount: /mcp/pepper
      briefs_orchestrator: briefs.pepper
      webcam: webcam-pepper                  # ← new

  - type: builtin.webcam
    name: webcam-pepper
    description: "Pepper's webcam endpoint."
    params:
      enabled: true
      captures_root: "C:\\Users\\jeffr\\.agent-core\\webcam\\pepper"
      audit_log_path: "C:\\Users\\jeffr\\.agent-core\\webcam\\pepper\\audit.jsonl"
      default_camera_index: 0
      default_resolution: [1280, 720]
      max_resolution: [3840, 2160]
      capture_timeout_seconds: 3.0
```

- [ ] **Step 13.2: Restart the bus daemon**

Stop the running bus daemon and restart. Check daemon log: there should be a line `WebcamEndpoint(name=webcam-pepper) started` near the other endpoint-startup entries.

- [ ] **Step 13.3: Probe the MCP surface directly to confirm tools registered**

Run a one-off Python probe (paste into a `uv run python -` REPL):

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def probe():
    async with streamablehttp_client("http://127.0.0.1:8789/mcp/pepper") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"Pepper has {len(names)} tools.")
            assert "capture_webcam_frame" in names, names
            assert "list_cameras" in names, names
            print("✅ Both webcam tools present.")

asyncio.run(probe())
```

Expected: prints the tool count and `✅ Both webcam tools present.`

- [ ] **Step 13.4: Relaunch Pepper's session so her MCP cache picks up the new tools**

`/exit` Pepper's Claude Code session, relaunch with `--continue`. Per [issue #37](https://github.com/jeffrichley/agent_core/issues/37), this is required until generic `tools/list_changed` lands.

- [ ] **Step 13.5: Smoke test — ask Pepper to look at her environment**

Send Pepper a Discord DM: "What do you see right now?" or similar. Verify in her response:

1. The image content block came through (her vision model commented on the actual scene).
2. A PNG file landed under `C:\Users\jeffr\.agent-core\webcam\pepper\<today>\`.
3. The audit log at `C:\Users\jeffr\.agent-core\webcam\pepper\audit.jsonl` has at least one `capture_webcam_frame` entry with `result: "ok"`.

- [ ] **Step 13.6: Document the rollout in the cutover log (optional)**

If you want a record of this rollout alongside the 2026-05-06 cutover docs, add a brief entry to `docs/cutover/pepper-flip-2026-05-06.md` under a new "Post-flip additions" section. Otherwise skip — the design doc + plan + commits are the durable record.

- [ ] **Step 13.7: Final clean-state commit (if anything outside the package changed)**

If you modified anything beyond the package (e.g., the cutover doc), commit it:

```bash
git add docs/cutover/pepper-flip-2026-05-06.md
git commit -m "docs(cutover): record webcam endpoint rollout"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Plan task(s) |
|---|---|
| Goal — capture frame, see immediately, file path | Task 6 (capture_frame), Task 10 (MCP returns ImageContent + TextContent) |
| Architecture / package layout | Task 1 (scaffold) |
| Pluggy plugin hooks | Task 11 |
| Endpoint config (yaml) | Task 5 (defaults), Task 13 (rollout) |
| Per-agent (not shared) | Task 5 (per-instance config) |
| Bus envelope behavior — deliver no-op | Task 5 |
| Tool surface — capture_webcam_frame | Tasks 6, 7, 8, 10 |
| Tool surface — list_cameras | Tasks 9, 10 |
| Data flow — open/release per call | Task 12 (OpenCVCameraBackend); Task 6 implements the call shape |
| Storage layout — date-bucketed | Task 6 |
| Color space — BGR→RGB convert | Task 12 |
| Downstream re-use — paths | Task 6 returns path; Task 10 surfaces it in TextContent |
| Kill switch | Task 7 (capture), Task 9 (list_cameras) |
| Audit log | Task 4 (writer), Tasks 6/7/9 (event emission) |
| Retention — none in v1 | n/a — explicitly not implemented |
| Privacy posture — LED, no pre-warm, append-only audit, local-disk | Task 12 (no pre-warm), Task 4 (append-only), Task 6 (local-disk) |
| Error handling — every failure mode | Task 7 (capture), Task 9 (list_cameras) |
| Concurrency — asyncio.Lock per camera_index | Task 9 |
| Testing — Tier 1 fake backend | Tasks 3, 5–9 |
| Testing — Tier 2 real OpenCV (gated) | Task 12 |
| Testing — Tier 3 plugin discovery + MCP wiring | Task 11 |
| Migration / rollout (5 steps) | Task 13 |
| Open future work | n/a — explicitly out of scope |

No gaps.

**Placeholder scan:** No "TBD", "TODO", "implement later", or "similar to Task N" patterns. Every step has either a code block or an exact command.

**Type consistency:** `WebcamEndpoint`, `FakeCameraBackend`, `OpenCVCameraBackend`, `CameraBackend`, `CameraInfo`, `CaptureSuccess`/`CaptureError`, `ListCamerasSuccess`/`ListCamerasError`, `AuditEvent`/`AuditLog`, `register_webcam_tools` — all spelled identically across the tasks where they're referenced.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-06-pepper-webcam.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
