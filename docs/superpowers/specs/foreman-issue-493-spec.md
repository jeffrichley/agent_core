# Spec: mypy --strict for agent-core-voice (issue #493)

## Goal

Enable `mypy --strict` enforcement for `packages/agent-core-voice/src` by adding the package to the root `[tool.mypy] files` list, wiring `ignore_missing_imports` overrides for the two untyped third-party dependencies (`madrigal`, `soundfile`), adding a per-module strict-flag override for `agent_core_voice.*`, and fixing the small set of annotation gaps in the source files. Part of Theme F Track B ticket B5 (the sub-ticket "e" covering `voice`). See issue #493 and the parent spec at `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (section B5).

`just check` must exit 0 on the resulting branch.

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/agent-core-voice/src"`.
- `pyproject.toml` has a `[[tool.mypy.overrides]]` with `module = ["madrigal", "madrigal.*"]` and `ignore_missing_imports = true` (madrigal 0.2.0 ships no `py.typed` marker).
- `pyproject.toml` has a `[[tool.mypy.overrides]]` with `module = ["soundfile"]` and `ignore_missing_imports = true` (soundfile 0.13.1 ships as a single `.py` file with no stubs).
- `pyproject.toml` has a `[[tool.mypy.overrides]]` with `module = ["agent_core_voice.*"]` and the individual strict-flag booleans (same set as the `agent_core_discord.*` override already present — see Approach).
- `uv run mypy` exits 0 with no errors or new suppressions.
- `plugin.py`'s `_mounter` closure has `bus_handle: BusHandle` and `mcp_endpoint: ClaudeCodeMCPEndpoint` annotations, with both imported under `TYPE_CHECKING`.
- `mcp.py`'s `_synthesize` inner function has `options: dict[str, Any] | None`, not bare `options: dict | None`.
- `endpoint.py`'s `_wav_duration` returns a concrete `float` for the frame-count division (not a soundfile-derived `Any`): `float(f.frames) / float(f.samplerate)`.
- The existing `# type: ignore[arg-type]` suppression on `_publish_failed` remains and is NOT flagged as unused (it is genuinely needed: `reason: str` is wider than `FailureReason`).
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern fits. This is a typing-discipline closure: once `agent_core_voice.*` is under `--strict`, type regressions cannot be introduced silently.

**Why individual strict-flag booleans rather than `strict = true`.** The root `[tool.mypy]` section covers `packages/core/src`, `packages/agent-core-channel/src`, and `packages/agent-core-discord/src` with a lighter baseline. The comment in the existing config explains: `strict = true` in a per-module override leaks several flags (including `disallow_any_generics`) to the other packages in `files`. The individual booleans scope correctly. Use the same set already applied to `agent_core_discord.*`.

**Why `madrigal` and `soundfile` need `ignore_missing_imports`.** `madrigal 0.2.0` (installed in `.venv`) has no `py.typed` marker — mypy cannot follow its types, so all symbols imported from `madrigal.*` become `Any`. `soundfile 0.13.1` ships as a single `soundfile.py` file with no stubs and no `py.typed`. `fastmcp` and `pluggy` both ship `py.typed` markers and need no override. Adding missing-import silences for just these two third-party packages confines the `Any` contamination to imports, not to our own source.

**Annotation gaps found by reading the source** (small; the package is well-typed overall):

1. **`plugin.py`** — `_mounter` closure (lines 105–123) has two unannotated parameters under `disallow_untyped_defs`:
   - `bus_handle` — annotate `bus_handle: BusHandle`. Add `from agent_core.bus.handle import BusHandle` to the existing `if TYPE_CHECKING:` block.
   - `mcp_endpoint` — annotate `mcp_endpoint: ClaudeCodeMCPEndpoint`. Add `from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint` to the `TYPE_CHECKING` block. After the `isinstance(endpoint, claude_code_mcp_cls)` narrowing, `endpoint` has type `Any` (because `_resolve_claude_code_mcp_cls()` returns `type[Any]`); assigning `Any` to a `ClaudeCodeMCPEndpoint`-annotated default is valid in mypy.

2. **`mcp.py`** — `_synthesize` inner function parameter `options: dict | None = None` (line 71): bare `dict` is flagged by `disallow_any_generics`. Change to `options: dict[str, Any] | None = None`. `Any` is already imported on line 22 (`from typing import TYPE_CHECKING, Any`).

3. **`endpoint.py`** — `_wav_duration` (lines 379–381): `f.frames` is `Any` after `ignore_missing_imports` for soundfile. The expression `f.frames / float(f.samplerate)` produces `Any`, but the declared return type is `tuple[float, int]`, triggering `warn_return_any`. Fix: make the numerator a concrete `float` by writing `float(f.frames) / float(f.samplerate)`. `float(Any)` resolves to `float` in mypy.

**Running mypy after the config change** will surface any issues beyond the above three. The Worker should fix any additional findings in the same commit as the annotation/config changes.

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` mypy configuration.** In the `[tool.mypy]` table, add `"packages/agent-core-voice/src"` as a fourth entry in `files`. Then add three new `[[tool.mypy.overrides]]` sections after the existing discord overrides:

   ```toml
   [[tool.mypy.overrides]]
   module = ["madrigal", "madrigal.*"]
   ignore_missing_imports = true

   [[tool.mypy.overrides]]
   module = ["soundfile"]
   ignore_missing_imports = true

   [[tool.mypy.overrides]]
   module = ["agent_core_voice.*"]
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

2. **Fix annotation gaps in `plugin.py`** (`packages/agent-core-voice/src/agent_core_voice/plugin.py`):

   a. Extend the `TYPE_CHECKING` block (currently at lines 21–23) to add `BusHandle` and `ClaudeCodeMCPEndpoint`:
   ```python
   if TYPE_CHECKING:
       from agent_core.bus.handle import BusHandle
       from agent_core.bus.protocol import Endpoint
       from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
       from agent_core.plugins.specs import RunnerServices
   ```

   b. Annotate `_mounter`'s two unannotated parameters:
   ```python
   def _mounter(
       bus_handle: BusHandle,
       *,
       voice_ep: VoiceEndpoint = voice_ep,
       voice_endpoint_name: str = voice_name,
       mcp_endpoint: ClaudeCodeMCPEndpoint = endpoint,
       voice_id: str = voice_id,
       agent_name: str = name,
   ) -> None:
   ```

3. **Fix annotation gap in `mcp.py`** (`packages/agent-core-voice/src/agent_core_voice/mcp.py`):

   Change the `options` parameter of `_synthesize` from bare `dict` to parameterized:
   ```python
   async def _synthesize(
       text: str,
       timeout_s: float | None = None,
       retain_s: float | None = None,
       options: dict[str, Any] | None = None,
       format: str = "wav",
   ) -> list[Any]:
   ```

4. **Fix annotation gap in `endpoint.py`** (`packages/agent-core-voice/src/agent_core_voice/endpoint.py`):

   In `_wav_duration` (the `@staticmethod` near line 379), replace the frame-rate computation with explicit `float()` wrapping:
   ```python
   @staticmethod
   def _wav_duration(wav_bytes: bytes) -> tuple[float, int]:
       with sf.SoundFile(io.BytesIO(wav_bytes)) as f:
           return float(f.frames) / float(f.samplerate), int(f.samplerate)
   ```

5. **Run mypy and fix any remaining issues.** After sub-requests 1–4, run:
   ```bash
   uv run --no-sync mypy
   ```
   Expected output: `Success: no issues found`. If mypy surfaces additional issues (e.g., from `lifecycle.py`'s subprocess `result.stdout` typing, or any `Any`-bleeding from madrigal/soundfile usages), fix them in this same commit. Commit all annotation + config changes together:
   ```bash
   git add pyproject.toml \
     packages/agent-core-voice/src/agent_core_voice/plugin.py \
     packages/agent-core-voice/src/agent_core_voice/mcp.py \
     packages/agent-core-voice/src/agent_core_voice/endpoint.py
   git commit -m "feat: enable mypy --strict for agent-core-voice"
   ```

6. **Verify the full gate.**
   ```bash
   just check
   ```
   Expected: green (lint, typecheck, contracts, test, patch-cov all pass).

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/agent-core-voice/src"` to `[tool.mypy].files`; add three `[[tool.mypy.overrides]]` blocks: `madrigal` missing-import silence, `soundfile` missing-import silence, `agent_core_voice.*` strict flags |
| `packages/agent-core-voice/src/agent_core_voice/plugin.py` | Add `BusHandle` and `ClaudeCodeMCPEndpoint` to `TYPE_CHECKING` imports; annotate `_mounter`'s `bus_handle` and `mcp_endpoint` parameters |
| `packages/agent-core-voice/src/agent_core_voice/mcp.py` | Fix bare `dict` → `dict[str, Any]` for the `options` parameter of `_synthesize` |
| `packages/agent-core-voice/src/agent_core_voice/endpoint.py` | Fix `_wav_duration` frame-count division: `f.frames` → `float(f.frames)` to avoid `warn_return_any` |
| Other source files (if mypy surfaces additional gaps) | Minor annotation fixes as discovered by running mypy |

No new files are created. No methods are moved between files.

## Alternatives considered

1. **Enable `strict = true` at the top-level `[tool.mypy]` table.** Would immediately break the currently-passing typecheck step for `packages/core/src` and `packages/agent-core-channel/src`, which have not been prepared for strict mode. Per the existing config comment, the per-module override is the correct scoping mechanism. Ruled out.

2. **Use `strict = true` in the per-module override instead of individual flags.** The existing config comment explains why the individual flags are used: `strict = true` in a per-module override leaks certain flags to other packages in `files`. The individual boolean list is the established convention in this repo (the `agent_core_discord.*` override already uses it). Ruled out for consistency.

3. **Add stubs for `madrigal` instead of `ignore_missing_imports`.** `madrigal` 0.2.0 is a local library whose source is typed but lacks a `py.typed` marker. Writing stub files for it is unnecessary overhead given that `ignore_missing_imports` is already the established repo pattern for stub-less dependencies (used for `discord.*`). Ruled out.

4. **Add stubs for `soundfile` via third-party `soundfile-stubs`.** No maintained stub package exists for soundfile 0.13.x. `ignore_missing_imports` is the pragmatic correct fix — identical to what the repo does for `discord.*`. Ruled out.

## Open questions

None. All annotation gaps were found by reading all seven source files (`__init__.py`, `protocol.py`, `audit.py`, `envelopes.py`, `lifecycle.py`, `mcp.py`, `endpoint.py`, `plugin.py`). Third-party library stub states were verified in the installed `.venv` (madrigal 0.2.0: no `py.typed`; soundfile 0.13.1: no stubs; fastmcp: `py.typed` present; pluggy: `py.typed` present, `HookimplMarker.__call__` is properly typed). The `JsonlAuditLog` generic base (introduced by B4 / issue #404) is already used correctly in `audit.py`. The existing `# type: ignore[arg-type]` on `_publish_failed` is legitimately needed and will remain.

## Out of scope

- Enabling mypy `--strict` for `packages/core/src`, `packages/agent-core-channel/src`, or any other workspace package — separate tickets.
- Wiring `packages/agent-core-voice/tests` into mypy — test files are not in `src/` and are not part of the production package.
- Any behavioral changes to voice endpoint logic, synthesis routing, audit policy, or MCP tool surface.
- Splitting `endpoint.py` or any structural refactor — separate ticket (B7 is for `claude_code_mcp.py`; there is no corresponding split planned for voice's endpoint).
- Adding stubs for `madrigal` — out of scope; `ignore_missing_imports` is the correct pragmatic fix.
