# Spec: make package deps publish-clean — workspace refs → pinned Requires-Dist (issue #390)

## Goal

Add versioned constraints to the bare `agent-core-*` names that appear in the `[project] dependencies` sections of the 7 affected workspace packages, so that built wheels carry `Requires-Dist: agent-core (>=0.7,<0.8)` instead of unversioned `Requires-Dist: agent-core`. Additionally remove the `qwen-tts` dependency from `agent-core-voice` (the source code never imports `qwen_tts` directly — `madrigal` wraps it — and `qwen-tts` is a vendored non-PyPI workspace member that would cause `pip install agent-core-voice` to fail). Verify all 12 wheels build cleanly and carry correct `Requires-Dist` metadata. See issue #390 and design doc `docs/superpowers/specs/2026-07-16-theme-f-track-a-pypi-launch-design.md` §A1.2.

---

## Acceptance criteria

- Running `uv build --package <name> --out-dir dist/` for each of the 12 publishable packages (`agent-core`, `agent-core-briefs`, `agent-core-busproxy`, `agent-core-channel`, `agent-core-credentials`, `agent-core-discord`, `agent-core-hatchery`, `agent-core-inbound`, `agent-core-notify`, `agent-core-qa`, `agent-core-voice`, `agent-core-webcam`) produces a wheel without error.
- Every wheel whose package depends on a sibling has a versioned `Requires-Dist` entry: `agent-core (>=0.7,<0.8)` or `agent-core-credentials (>=0.7,<0.8)` (not the bare unversioned form).
- `agent-core-voice`'s wheel METADATA does **not** include `Requires-Dist: qwen-tts` (removed from direct deps).
- No `[tool.uv.sources]` workspace entries in any package's `pyproject.toml` are removed (they remain for local dev resolution).
- `just test-fast` passes after the changes (the `qwen-tts` removal should not affect tests, since tests use `FakeTTSBackend` and never load the real backend).

---

## Approach

No GoF pattern fits. This is configuration plumbing; the chosen design follows Google's "make the right thing easy" principle: encoding version bounds in `[project] dependencies` makes the wheel's install graph self-contained and discoverable by standard tooling, with no special index or workspace awareness required.

**Why `[tool.uv.sources]` stays.** uv's workspace source resolution is a LOCAL dev-only mechanism: the `[tool.uv.sources]` block in each member's `pyproject.toml` (and the root-level block in the workspace root) make `uv sync` resolve agent-core siblings from the local filesystem. These entries are NOT written into wheel METADATA. The built `Requires-Dist` reflects only what is in `[project] dependencies`. Adding version bounds to `[project] dependencies` is therefore the only change needed to fix the PyPI install graph; the workspace sources stay untouched.

**Version range `>=0.7,<0.8`.** The current workspace version is `0.7.0` (per `.release-please-manifest.json`). All 12 packages publish at a single synchronized version train (A1-2 / A1-3 tickets). The chosen range `>=0.7,<0.8` means "any 0.7.x patch release of the sibling is acceptable" — compatible with how the monorepo releases and consistent with the design doc's example (`agent-core>=0.2,<0.3`). When the next minor version ships (0.8.0), the same A1-3 CI pipeline that builds the new wheels will also need these constraints updated in `[project] dependencies` — that update step is out of scope here.

**Removing `qwen-tts` from `agent-core-voice` deps.** The entire `agent_core_voice` source tree was grepped for `qwen_tts` and `qwen.tts` imports — zero matches found. All TTS calls go through `madrigal` (`from madrigal import Spec, generate`, `from madrigal.engine import QwenTTSBackend`). The `qwen-tts` entry in `agent-core-voice/pyproject.toml` was added when the vendored `Qwen3-TTS` workspace member was needed directly; `madrigal` now owns that integration. Since `qwen-tts` is a workspace-only member (`packages/agent-core-voice/vendor/Qwen3-TTS`) that will never publish to PyPI, keeping it in `[project] dependencies` would cause `uv add agent-core-voice` from PyPI to fail unless `qwen-tts` resolves from PyPI separately. Removing it makes the wheel clean. If `madrigal>=0.2.0` on PyPI already declares `qwen-tts` as its own transitive dep, the net runtime result is unchanged.

**Packages with no internal deps (unchanged).** `agent-core-credentials`, `agent-core-notify`, `agent-core-channel`, `agent-core-busproxy`, and `agent-core-qa` have no `agent-core-*` siblings in their `[project] dependencies`. They require no changes and will produce clean wheels today.

---

## Sub-requests (topologically sorted)

1. **Update `packages/core/pyproject.toml`** — in `[project] dependencies`, change:
   ```toml
   "agent-core-credentials",
   ```
   to:
   ```toml
   "agent-core-credentials>=0.7,<0.8",
   ```

2. **Update `packages/agent-core-discord/pyproject.toml`** — in `[project] dependencies`, change:
   ```toml
   "agent-core",
   "agent-core-credentials",
   ```
   to:
   ```toml
   "agent-core>=0.7,<0.8",
   "agent-core-credentials>=0.7,<0.8",
   ```

3. **Update `packages/agent-core-hatchery/pyproject.toml`** — in `[project] dependencies`, change:
   ```toml
   "agent-core",
   ```
   to:
   ```toml
   "agent-core>=0.7,<0.8",
   ```
   The existing `[tool.uv.sources]` block (`agent-core = { workspace = true }`) is preserved unchanged.

4. **Update `packages/agent-core-briefs/pyproject.toml`** — in `[project] dependencies`, change:
   ```toml
   "agent-core",
   ```
   to:
   ```toml
   "agent-core>=0.7,<0.8",
   ```

5. **Update `packages/agent-core-inbound/pyproject.toml`** — in `[project] dependencies`, change:
   ```toml
   "agent-core",
   "agent-core-credentials",
   ```
   to:
   ```toml
   "agent-core>=0.7,<0.8",
   "agent-core-credentials>=0.7,<0.8",
   ```
   The existing `[tool.uv.sources]` block (`agent-core = { workspace = true }`, `agent-core-credentials = { workspace = true }`) is preserved unchanged.

6. **Update `packages/agent-core-voice/pyproject.toml`** — in `[project] dependencies`:
   - Change `"agent-core"` to `"agent-core>=0.7,<0.8"`
   - Remove the `"qwen-tts"` line entirely

   Final `dependencies` block:
   ```toml
   dependencies = [
       "agent-core>=0.7,<0.8",
       "fastmcp>=2.0",
       "madrigal >= 0.2.0",
       "pluggy>=1.6",
       "soundfile>=0.13",
   ]
   ```
   The existing `[tool.uv.sources]` block (torch GPU index entries) is preserved unchanged.

7. **Update `packages/agent-core-webcam/pyproject.toml`** — in `[project] dependencies`, change:
   ```toml
   "agent-core",
   ```
   to:
   ```toml
   "agent-core>=0.7,<0.8",
   ```

8. **Build all 12 packages and verify METADATA** — run:
   ```bash
   mkdir -p dist
   for pkg in agent-core agent-core-briefs agent-core-busproxy agent-core-channel \
               agent-core-credentials agent-core-discord agent-core-hatchery \
               agent-core-inbound agent-core-notify agent-core-qa \
               agent-core-voice agent-core-webcam; do
     uv build --package "$pkg" --out-dir dist/
   done
   ```
   Then inspect `Requires-Dist` in each wheel:
   ```bash
   for whl in dist/*.whl; do
     echo "=== $whl ==="
     unzip -p "$whl" '*/METADATA' | grep -E 'Requires-Dist|Name:'
   done
   ```
   Expected: every `agent-core-*` sibling reference shows a versioned constraint (e.g., `Requires-Dist: agent-core (>=0.7,<0.8)`). No `Requires-Dist: qwen-tts` appears in `agent_core_voice-*.whl`.

9. **Run the test suite** to confirm the `qwen-tts` removal does not break existing tests:
   ```bash
   just test-fast
   ```
   Expected: all tests pass (voice tests use `FakeTTSBackend`; no test loads real TTS backends).

---

## File-level changes

| File | Change |
|---|---|
| `packages/core/pyproject.toml` | **Modify** — version-pin `agent-core-credentials` dep (`>=0.7,<0.8`) |
| `packages/agent-core-discord/pyproject.toml` | **Modify** — version-pin `agent-core` and `agent-core-credentials` deps |
| `packages/agent-core-hatchery/pyproject.toml` | **Modify** — version-pin `agent-core` dep; `[tool.uv.sources]` unchanged |
| `packages/agent-core-briefs/pyproject.toml` | **Modify** — version-pin `agent-core` dep |
| `packages/agent-core-inbound/pyproject.toml` | **Modify** — version-pin `agent-core` and `agent-core-credentials` deps; `[tool.uv.sources]` unchanged |
| `packages/agent-core-voice/pyproject.toml` | **Modify** — version-pin `agent-core` dep; remove `qwen-tts` dep; `[tool.uv.sources]` (torch GPU) unchanged |
| `packages/agent-core-webcam/pyproject.toml` | **Modify** — version-pin `agent-core` dep |

---

## Alternatives considered

1. **Lower bound only (`agent-core>=0.7`, no upper bound)** — simpler, never needs updating when the version train advances. Ruled out: the design doc explicitly shows `>=MAJOR.MINOR,<MAJOR.(MINOR+1)` range syntax, and a hard upper bound prevents inadvertently mixing a 0.7.x endpoint package with a 0.8.0 core package (which may have breaking API changes). Minor-version compat is the documented intent.

2. **Keep `qwen-tts` as an optional extra in `agent-core-voice`** — e.g., `[project.optional-dependencies] gpu = ["qwen-tts"]`. Ruled out: `agent_core_voice` source code contains zero direct imports of `qwen_tts`; `madrigal` owns that integration. Keeping `qwen-tts` as even an optional dep adds confusion about who owns the dependency, and it cannot be installed from PyPI regardless. If the `madrigal` integration requires `qwen-tts`, `madrigal` should declare it.

3. **Wrap `qwen-tts` in a `[tool.uv.sources]` path-source entry per package** — give per-package `pyproject.toml` files a local path source pointing to `vendor/Qwen3-TTS`. Ruled out: `[tool.uv.sources]` path entries are local-only and not encoded into wheel METADATA — the built wheel would still carry `Requires-Dist: qwen-tts` without a resolvable PyPI entry, causing the same install failure.

---

## Open questions

1. **Does `madrigal>=0.2.0` on PyPI declare `qwen-tts` as a transitive dependency?** If it does not, removing `qwen-tts` from `agent-core-voice` means a production user who installs `agent-core-voice` from PyPI and tries to use the real `QwenTTSBackend` will encounter a `ModuleNotFoundError: No module named 'qwen_tts'` at runtime (specifically when `madrigal.engine.QwenTTSBackend` tries to load the model). The test suite will not catch this because tests use `FakeTTSBackend`. This risk should be caught by the A1-5 real-install round-trip gate, not this ticket. If the Worker can verify `madrigal`'s PyPI metadata during implementation, they should confirm `qwen-tts` appears in `madrigal`'s `Requires-Dist` before finalising this change.

2. **Version constraint updates for future minor releases** — when the workspace version advances to `0.8.0`, the `>=0.7,<0.8` constraints in these 7 `pyproject.toml` files will be wrong (the 0.8.0 wheel of `agent-core-hatchery` would still say it requires `agent-core<0.8`). The release CI (A1-3, foreman/impl-392) needs a step to bump these constraints as part of each minor release. This is out of scope for A1-1 but should be tracked.

---

## Out of scope

- Publishing `qwen-tts` to PyPI: explicitly excluded per the design doc §2 Non-goals.
- Adding or changing `[tool.uv.sources]` workspace entries: these are local-dev-only and do not affect wheel METADATA; no changes needed.
- Updating the version constraints for future minor releases (e.g., bumping `<0.8` to `<0.9` when 0.8.0 ships): that belongs in the A1-3 release CI pipeline.
- Per-package `[tool.uv.sources]` cleanup: `packages/agent-core-hatchery` and `packages/agent-core-inbound` declare `agent-core = { workspace = true }` locally, redundantly with the root workspace declaration. Removing that redundancy is tidy but not needed for correctness; leave it for a future cleanup.
- Track B tickets (mypy expansion, CI gate wiring beyond the PyPI round-trip, slow-test marking).
