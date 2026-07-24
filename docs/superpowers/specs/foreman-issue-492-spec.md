# Spec: mypy --strict for agent-core-inbound (issue #492)

## Goal

Enable `mypy --strict` enforcement for `packages/agent-core-inbound/src` by adding the package to the root `[tool.mypy] files` list, wiring a per-module strict-flags override (following the pattern established for discord in issue #444 and hatchery in issue #491), and fixing all annotation gaps in the inbound source tree until `uv run mypy` exits 0. Part of the Track B B5 multi-package strict-mypy effort (#405).

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/agent-core-inbound/src"`.
- `pyproject.toml` has `[[tool.mypy.overrides]]` with `module = ["agent_core_inbound.*"]` and all individual strict flags (`disallow_any_generics`, `disallow_subclassing_any`, `disallow_untyped_calls`, `disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_untyped_decorators`, `warn_return_any`, `no_implicit_reexport`, `strict_equality`, `extra_checks`), following the existing discord/hatchery pattern.
- `uv run mypy` exits 0 with no errors.
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern fits — this is a typing-discipline closure ("make the right thing easy": strict mypy makes type regressions impossible to introduce silently once the package is gated).

**Why individual flags rather than `strict = true`.** The root `[tool.mypy]` section covers other packages with a lighter base set of flags. The pyproject.toml comment (above the discord override block) explains the leakage problem: `strict = true` in a per-module override leaks flags like `disallow_any_generics` to all packages in `[tool.mypy] files`. Using the same individual-flag pattern that discord and hatchery use is the correct scoping mechanism.

**No third-party stub overrides needed.** The inbound package's runtime dependencies (`fastapi>=0.110`, `uvicorn>=0.27`, `pydantic>=2.0`) all ship `py.typed` markers. `pluggy` (used in `plugin.py`) also ships typed stubs. Scanning the source tree shows that `watchdog` (a declared dependency) is not imported in any current module, so no `ignore_missing_imports` override is needed for it. If a future module introduces `import watchdog`, an override would be required at that point.

**Known annotation gaps.** The inbound source tree is already well-annotated. Two gaps are confirmed by reading the source files:

1. **`endpoint.py` line 93**: `self._serve_task: asyncio.Task | None = None` is a bare generic — `asyncio.Task` without a type parameter trips `disallow_any_generics`. The correct annotation is `asyncio.Task[None] | None`: `uvicorn.Server.serve()` is a coroutine returning `None`, so `asyncio.create_task(self._server.serve())` produces `asyncio.Task[None]`.

2. **`router.py` `_extract_rule_id` (line ≈222–227)**: `rule_id_for = getattr(connector, "rule_id_for", None)` assigns type `Any` (because `getattr` returns `Any`). The subsequent `return rule_id_for(event_id=event_id, target_being=target_being)` then returns `Any` from a function declared `-> str`, which trips `warn_return_any`. Fix: wrap the return value with `cast(str, ...)` — add `from typing import cast` to the imports (note: `typing` is already imported in this file) and change the return to `return cast(str, rule_id_for(event_id=event_id, target_being=target_being))`.

**`testing/__init__.py` is already clean.** The file defines `__all__ = ["FakeConnector"]` and imports `FakeConnector` explicitly. The `no_implicit_reexport` flag is satisfied — no changes needed there.

**Additional gaps found at run time.** After the two known fixes, run `uv run mypy` to catch any remaining issues. The source is tightly annotated, so the output is expected to be clean; but mypy may surface edge cases in `funnel_handler.py`'s inner route function or in protocol stub interactions with fastapi's decorator typing.

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` mypy configuration.** In the `[tool.mypy]` table, add `"packages/agent-core-inbound/src"` to the `files` list. Then add a new `[[tool.mypy.overrides]]` section after the existing discord override block:

   ```toml
   [[tool.mypy.overrides]]
   module = ["agent_core_inbound.*"]
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

2. **Fix `endpoint.py` bare generic.** In `InboundEndpoint.__init__`, change the `_serve_task` instance variable annotation:

   ```python
   # Before (line ≈93):
   self._serve_task: asyncio.Task | None = None

   # After:
   self._serve_task: asyncio.Task[None] | None = None
   ```

   No import changes needed — `asyncio` is already imported at the top of the file.

3. **Fix `router.py` `warn_return_any` in `_extract_rule_id`.** Add `cast` to the existing `from typing import Any` import line, then wrap the `getattr`-derived call:

   ```python
   # Change the import (line ≈12):
   from typing import Any, cast

   # Change the return inside _extract_rule_id (line ≈225):
   # Before:
               return rule_id_for(event_id=event_id, target_being=target_being)
   # After:
               return cast(str, rule_id_for(event_id=event_id, target_being=target_being))
   ```

4. **Run mypy to verify no remaining errors.** After sub-requests 1–3:

   ```bash
   uv run mypy
   ```

   Expected output: `Success: no issues found`. If mypy surfaces additional issues (e.g., implicit `Any` from fastapi's decorator typing in `funnel_handler.py`, or any bare dict/list generics missed in the reading pass), fix them in this same commit.

5. **Commit and verify the full gate.**

   ```bash
   git add pyproject.toml \
     packages/agent-core-inbound/src/agent_core_inbound/endpoint.py \
     packages/agent-core-inbound/src/agent_core_inbound/router.py
   # (add any other files patched in step 4)
   git commit -m "feat: enable mypy --strict for agent-core-inbound"
   just check
   ```

   Expected: green (lint, typecheck, contracts, test, patch-cov all pass).

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/agent-core-inbound/src"` to `[tool.mypy].files`; add `[[tool.mypy.overrides]]` for `agent_core_inbound.*` (individual strict flags matching the discord/hatchery pattern) |
| `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py` | Change `self._serve_task: asyncio.Task | None` to `asyncio.Task[None] | None` |
| `packages/agent-core-inbound/src/agent_core_inbound/router.py` | Add `cast` to the `from typing import Any` import; wrap `getattr`-derived call site in `_extract_rule_id` with `cast(str, ...)` |
| Other `packages/agent-core-inbound/src/` files (if mypy surfaces gaps) | Minor annotation fixes as discovered by running `uv run mypy` after sub-requests 1–3 |

No new files are created. No public interfaces (method signatures, class names, protocol shapes, Pydantic model fields) change.

## Alternatives considered

1. **Use `strict = true` in the per-module override.** The existing pyproject.toml comment explains why this is incorrect: `strict = true` in a `[[tool.mypy.overrides]]` block leaks flags like `disallow_any_generics` to the other packages in `[tool.mypy] files` (core, channel, discord). The individual-flag approach used for discord and hatchery is the correct pattern and must be followed here. Ruled out.

2. **Use `# type: ignore[no-any-return]` in `_extract_rule_id` instead of `cast`.** Silencing mypy with `type: ignore` is always the last resort; a `cast` documents the invariant ("we know this returns str") and survives future type-stub improvements to `getattr` without masking real regressions. Ruled out.

3. **Pre-emptively add `ignore_missing_imports` for `watchdog`.** Watchdog 4.x does not ship a `py.typed` marker. However, no current source file in `packages/agent-core-inbound/src` imports `watchdog`, so adding an override for it now adds noise without benefit. If a future module introduces `import watchdog`, the override belongs in that PR. Ruled out.

## Open questions

None. The issue is unambiguous ("add inbound to `[tool.mypy] files` at `--strict` and fix annotations until clean"); the repo conventions are clear (follow the individual-flags pattern from discord/hatchery); the two confirmed source gaps have precise fixes; `testing/__init__.py` already satisfies `no_implicit_reexport` with its explicit `__all__`.

## Out of scope

- Enabling mypy `--strict` for any other package in the workspace — each is its own sub-ticket under B5 (#405).
- Adding `packages/agent-core-inbound/tests` to mypy — test files are not in the `src/` tree and are not part of the production package; adding them is a separate decision.
- Restructuring, renaming, or splitting any inbound module — this is a typing-discipline ticket only; no behavioral or structural changes.
- Changing the `watchdog` dependency or removing it from `pyproject.toml`.
