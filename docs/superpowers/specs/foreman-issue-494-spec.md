# Spec: mypy --strict for agent-core-webcam (issue #494)

## Goal

Enable `mypy --strict` enforcement for `packages/agent-core-webcam/src` by adding the package to the root `[tool.mypy] files` list, wiring a per-module strict override, adding a `cv2` missing-import override, and fixing the handful of annotation gaps in the source files. Part of Theme F Track B ticket B5 (the sub-ticket "f" covering `webcam`). See issue #494 and the parent spec at `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (section B5).

`just check` must exit 0 on the resulting branch.

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/agent-core-webcam/src"`.
- `pyproject.toml` has a `[[tool.mypy.overrides]]` with `module = ["cv2", "cv2.*"]` and `ignore_missing_imports = true` (opencv-python ships no type stubs).
- `pyproject.toml` has a `[[tool.mypy.overrides]]` with `module = ["agent_core_webcam.*"]` and the individual strict-flag booleans (same set as the `agent_core_discord.*` override already present — see Approach).
- `uv run mypy` exits 0 with no errors or new suppressions.
- `CaptureSuccess.metadata` is annotated `dict[str, Any]`, not bare `dict`.
- `ListCamerasSuccess.cameras` is annotated `list[CameraInfo]`, not bare `list`.
- `WebcamEndpoint.capture_frame` return type is `tuple[bytes, Path | None, dict[str, Any]]`, not `tuple[bytes, Path | None, dict]`.
- `OpenCVCameraBackend._best_effort_name` has a `cap: Any` annotation on its first parameter.
- The `_mounter` closure in `plugin.py` has `bus_handle: BusHandle` annotation, and `BusHandle` is imported under `TYPE_CHECKING`.
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern fits. This is a typing discipline closure: once `agent_core_webcam.*` is under `--strict`, type regressions cannot be introduced silently.

**Why individual strict-flag booleans rather than `strict = true`.** The root `[tool.mypy]` section covers `packages/core/src`, `packages/agent-core-channel/src`, and `packages/agent-core-discord/src` with a lighter baseline. The comment in the existing config explains: `strict = true` in a per-module override leaks several flags (including `disallow_any_generics`) to the other packages in `files`. The individual booleans scope correctly. Use the same set already applied to `agent_core_discord.*`.

**Why a `cv2` override.** `opencv-python>=5.0.0.93` ships no PEP-561 stubs; mypy cannot find type info for `cv2`. The `[[tool.mypy.overrides]]` with `ignore_missing_imports = true` silences the missing-import noise so the strict override can focus on our own code. This is the same pattern used for `discord.*`.

**Annotation gaps found by reading the source** (all small; the package is well-typed overall):

1. `endpoint.py` — three bare generics:
   - `CaptureSuccess.metadata: dict` (line 48) → `dict[str, Any]`. `Any` is already imported at line 16 in this file via `from typing import TYPE_CHECKING` — check whether `Any` is already present in the imports; if not, add it to the `from typing import` line.
   - `ListCamerasSuccess.cameras: list` (line 63, comment says "avoid forward-ref import dance") → `list[CameraInfo]`. `CameraInfo` is defined in `agent_core_webcam.protocol` (a sibling module, no circular-import risk) but not yet imported in `endpoint.py`. Add it to the existing `from agent_core_webcam.protocol import (...)` block.
   - `WebcamEndpoint.capture_frame` return type (line 143) `-> tuple[bytes, Path | None, dict]` → `-> tuple[bytes, Path | None, dict[str, Any]]`.

2. `opencv_backend.py` — one unannotated parameter:
   - `_best_effort_name(cap, idx: int) -> str` (line 56): `cap` has no type. It is a `cv2.VideoCapture` object, but since cv2 has no stubs, the correct annotation is `cap: Any`. Add `from typing import Any` (currently the file only imports from `agent_core_webcam.protocol`).

3. `plugin.py` — one unannotated closure parameter:
   - `_mounter(bus_handle, ...)` (line 79): `bus_handle` has no type annotation; `disallow_untyped_defs` will flag it. `deferred_tool_mounters` is typed `list[Callable[[BusHandle], None]]` in `ClaudeCodeMCPEndpoint` (`_endpoint.py:176`), so the correct annotation is `bus_handle: BusHandle`. Add `from agent_core.bus.handle import BusHandle` under the existing `if TYPE_CHECKING:` block in `plugin.py`.

**Running mypy after the config change** will surface any issues beyond the above three. The Worker should fix any additional findings in the same commit as the annotation/config changes.

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` mypy configuration.** In the `[tool.mypy]` table, add `"packages/agent-core-webcam/src"` as a fourth entry in `files`. Then add two new `[[tool.mypy.overrides]]` sections after the existing discord overrides:

   ```toml
   [[tool.mypy.overrides]]
   module = ["cv2", "cv2.*"]
   ignore_missing_imports = true

   [[tool.mypy.overrides]]
   module = ["agent_core_webcam.*"]
   disallow_any_generics = true
   disallow_subclassing_any = true
   disallow_untyped_calls = true
   disallow_untyped_defs = true
   disallow_incomplete_defs = true
   disallow_untyped_decorators = true
   warn_return_any = true
   no_implicit_reexport = true
   strict_equality = true
   extra_checks = true
   ```

2. **Fix annotation gaps in `endpoint.py`** (`packages/agent-core-webcam/src/agent_core_webcam/endpoint.py`):

   a. Confirm `Any` is available in the imports (currently the file has `from typing import TYPE_CHECKING`; add `Any` to that import: `from typing import TYPE_CHECKING, Any`).

   b. Add `CameraInfo` to the protocol import block:
   ```python
   from agent_core_webcam.protocol import (
       CameraBackend,
       CameraBusyError,
       CameraInfo,
       CameraNotFoundError,
       ReadTimeoutError,
   )
   ```

   c. Update the three bare generics:
   - `CaptureSuccess.metadata: dict` → `metadata: dict[str, Any]`
   - `ListCamerasSuccess.cameras: list  # list[CameraInfo] — avoid forward-ref import dance` → `cameras: list[CameraInfo]` (remove the comment; `CameraInfo` is now imported)
   - `capture_frame` return type `-> tuple[bytes, Path | None, dict]` → `-> tuple[bytes, Path | None, dict[str, Any]]`

3. **Fix annotation gap in `opencv_backend.py`** (`packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py`):

   a. Add `from typing import Any` after the existing `from __future__ import annotations` line.

   b. Annotate `_best_effort_name`:
   ```python
   @staticmethod
   def _best_effort_name(cap: Any, idx: int) -> str:
   ```

4. **Fix annotation gap in `plugin.py`** (`packages/agent-core-webcam/src/agent_core_webcam/plugin.py`):

   a. Add `BusHandle` to the `TYPE_CHECKING` block:
   ```python
   if TYPE_CHECKING:
       from agent_core.bus.handle import BusHandle
       from agent_core.bus.protocol import Endpoint
       from agent_core.plugins.specs import RunnerServices
   ```

   b. Annotate `_mounter`'s `bus_handle` parameter:
   ```python
   def _mounter(
       bus_handle: BusHandle,
       *,
       webcam: WebcamEndpoint = webcam,
       mcp_endpoint: ClaudeCodeMCPEndpoint = endpoint,
   ) -> None:
   ```

5. **Run mypy and fix any remaining issues.** After sub-requests 1–4, run:
   ```bash
   uv run --no-sync mypy
   ```
   Expected output: `Success: no issues found`. If mypy surfaces additional issues (e.g., from `mcp.py` decorators, `fake.py` descriptor internals, or `__init__.py`), fix them in this same commit. Commit all annotation + config changes together:
   ```bash
   git add pyproject.toml \
     packages/agent-core-webcam/src/agent_core_webcam/endpoint.py \
     packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py \
     packages/agent-core-webcam/src/agent_core_webcam/plugin.py
   git commit -m "feat: enable mypy --strict for agent-core-webcam"
   ```

6. **Verify the full gate.**
   ```bash
   just check
   ```
   Expected: green (lint, typecheck, contracts, test, patch-cov all pass).

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/agent-core-webcam/src"` to `[tool.mypy].files`; add two `[[tool.mypy.overrides]]` blocks (`cv2` missing-import silence + `agent_core_webcam` strict flags) |
| `packages/agent-core-webcam/src/agent_core_webcam/endpoint.py` | Add `Any` to `from typing import` line; add `CameraInfo` to protocol import; fix three bare generics (`metadata`, `cameras`, `capture_frame` return type) |
| `packages/agent-core-webcam/src/agent_core_webcam/opencv_backend.py` | Add `from typing import Any`; annotate `_best_effort_name` `cap` parameter as `Any` |
| `packages/agent-core-webcam/src/agent_core_webcam/plugin.py` | Add `BusHandle` to `TYPE_CHECKING` imports; annotate `_mounter` `bus_handle` parameter |
| Other source files (if mypy surfaces additional gaps) | Minor annotation fixes as discovered by running mypy |

No new files are created. No methods are moved between files.

## Alternatives considered

1. **Enable `strict = true` at the top-level `[tool.mypy]` table.** Would immediately break the currently-passing typecheck step for `packages/core/src` and `packages/agent-core-channel/src`, which have not been prepared for strict mode. Per the existing config comment, the per-module override is the correct scoping mechanism. Ruled out.

2. **Use `strict = true` in the per-module override instead of individual flags.** The existing config comment explains why the individual flags are used: `strict = true` in a per-module override leaks certain flags to other packages in `files`. The individual boolean list is the established convention in this repo (the `agent_core_discord.*` override already uses it). Ruled out for consistency.

3. **Use `cv2-stubs` (third-party stub package) instead of `ignore_missing_imports`.** `cv2-stubs` is unmaintained and incomplete; `opencv-python 5.x` is a recent major version whose stubs would likely be missing or wrong. `ignore_missing_imports` is the pragmatic, correct fix — identical to what the repo does for `discord.*`. Ruled out.

## Open questions

None. All annotation gaps were found by reading the six source files. The `deferred_tool_mounters` type was verified at `packages/core/src/agent_core/endpoints/claude_code_mcp/_endpoint.py:176`. The `JsonlAuditLog` generic base (introduced by #404) is already used correctly in `audit.py`.

## Out of scope

- Enabling mypy `--strict` for `packages/core/src`, `packages/agent-core-channel/src`, or any other workspace package — separate tickets (B5 sibling sub-tickets and B6).
- Wiring `packages/agent-core-webcam/tests` into mypy — test files are not in `src/` and are not part of the production package.
- Any behavioral changes to webcam endpoint logic, audit policy, or MCP tool surface.
- Installing `cv2-stubs` — `ignore_missing_imports` is the correct fix given the stubs' maintenance state.
