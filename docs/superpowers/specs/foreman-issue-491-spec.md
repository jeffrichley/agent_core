# Spec: mypy --strict for agent-core-hatchery (issue #491)

## Goal

Enable `mypy --strict` enforcement for `packages/agent-core-hatchery/src` by adding the package to the root `[tool.mypy] files` list, wiring a per-module strict-flags override (following the pattern established for discord in issue #444), adding an `ignore_missing_imports` override for `questionary` (which ships no type stubs), and fixing all annotation gaps in the hatchery source tree until `uv run mypy` exits 0. Part of the Track B B5 multi-package strict-mypy effort (#405).

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/agent-core-hatchery/src"`.
- `pyproject.toml` has `[[tool.mypy.overrides]]` with `module = ["questionary", "questionary.*"]` and `ignore_missing_imports = true`.
- `pyproject.toml` has `[[tool.mypy.overrides]]` with `module = ["agent_core_hatchery.*"]` and all individual strict flags (`disallow_any_generics`, `disallow_subclassing_any`, `disallow_untyped_calls`, `disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_untyped_decorators`, `warn_return_any`, `no_implicit_reexport`, `strict_equality`, `extra_checks`), following the existing discord pattern.
- `uv run mypy` exits 0 with no errors.
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern fits — this is a typing-discipline closure ("make the right thing easy": strict mypy makes type regressions impossible to introduce silently once the package is gated).

**Why individual flags rather than `strict = true`.** The root `[tool.mypy]` section covers `packages/core/src`, `packages/agent-core-channel/src`, and `packages/agent-core-discord/src` with a lighter base set of flags. The pyproject.toml comment (above the discord override block) explains the leakage problem: `strict = true` in a per-module override leaks flags like `disallow_any_generics` to all packages in `files`. Using the same individual-flag pattern that discord uses is the correct scoping mechanism.

**Why `questionary` needs `ignore_missing_imports`.** `questionary` ships no `py.typed` marker and no type stubs on PyPI. This is analogous to `discord.py` needing its own `ignore_missing_imports` override. With `ignore_missing_imports = true`, mypy treats the module as `Any` and stops emitting "Cannot find implementation or library stub" noise, allowing the strict flags to focus on our own code in `wizard.py`.

**Known annotation gaps in the hatchery source tree.** All source files use `from __future__ import annotations` and are reasonably well-annotated. The gaps that will trip strict mode are:

1. **`hatcher.py` — bare `Callable` in `__init__`.** `_venv_builder: Callable | None` and `_mcp_json_gen: Callable | None` are bare generics; `disallow_any_generics` requires `Callable[..., Any]`. Import `Any` from `typing` and `Callable` from `collections.abc` (both already implicitly available via `from __future__ import annotations`; add explicit imports).

2. **`daemon_config.py` — bare `dict`.** `merged: dict = yaml.safe_load(...) or {}` appears twice (in `_write_endpoints_fragment` and `_write_jobs_fragment`); change to `dict[str, Any]`.

3. **`daemon_probe.py` — untyped `runner` parameters and `endpoint_name` narrowing.**
   - `_stop_daemon(runner=subprocess.run)`, `_start_daemon(runner=subprocess.run)`, and `reload_and_probe(..., runner=subprocess.run)` all lack type annotations on `runner`. Annotate as `runner: Callable[..., Any] = subprocess.run`. Add `from collections.abc import Callable` and `from typing import Any` to imports.
   - `config.endpoint_name` is typed `str | None` on `HatchConfig` (because Pydantic field annotations don't reflect model_validator effects). The `reload_and_probe` function passes it to `_probe_endpoint(endpoint_name: str, ...)`. Add a local narrowing assertion: `endpoint_name = config.endpoint_name; assert endpoint_name is not None  # guaranteed by HatchConfig model_validator`.

4. **`renderer.py` — `str | None` in `dict[str, str]`.** `_substitution_dict` is annotated `-> dict[str, str]` but includes `"endpoint_name": cfg.endpoint_name` where `cfg.endpoint_name` is typed `str | None`. Use `cfg.endpoint_name or cfg.being_name_lower` (both are `str`; `being_name_lower` is a `@computed_field` that always returns `str`; the model_validator guarantees `endpoint_name` is already set, so `or` never falls back in practice).

5. **Additional gaps discovered by running mypy.** After the known fixes, run `uv run mypy` and address any further issues. Likely candidates: `wizard.py` call-sites that feed `questionary` return values (which will be `Any`) into functions expecting `str`; `report.py` `datetime.now()` return used directly in `f-string` (likely fine); any implicit `Any` flows in `file_classes.py` or `elder_letters.py`.

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` mypy configuration.** In the `[tool.mypy]` table, add `"packages/agent-core-hatchery/src"` as a fourth entry in `files`. Then add two new `[[tool.mypy.overrides]]` sections after the existing discord override block:

   ```toml
   [[tool.mypy.overrides]]
   module = ["questionary", "questionary.*"]
   ignore_missing_imports = true

   [[tool.mypy.overrides]]
   module = ["agent_core_hatchery.*"]
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

2. **Fix `hatcher.py`.** Add `from typing import Any` to imports (it likely already imports `Callable` from `collections.abc` via the `from __future__ import annotations` + existing `from collections.abc import Callable`). Change the `__init__` signature:

   ```python
   from collections.abc import Callable
   from typing import Any

   class Hatcher:
       def __init__(
           self,
           config: HatchConfig,
           templates_dir: Path | None = None,
           *,
           _venv_builder: Callable[..., Any] | None = None,
           _mcp_json_gen: Callable[..., Any] | None = None,
       ) -> None:
   ```

3. **Fix `daemon_config.py`.** Change both bare `dict` annotations to `dict[str, Any]`. Add `from typing import Any` to imports. In `_write_endpoints_fragment`:

   ```python
   merged: dict[str, Any] = yaml.safe_load(always_on_yaml) or {}
   ```

   In `_write_jobs_fragment`:

   ```python
   merged: dict[str, Any] = yaml.safe_load(always_on_yaml) or {}
   ```

4. **Fix `daemon_probe.py`.** Add `from collections.abc import Callable` and `from typing import Any` to imports. Annotate the three `runner` parameters:

   ```python
   def _stop_daemon(runner: Callable[..., Any] = subprocess.run) -> None:

   def _start_daemon(runner: Callable[..., Any] = subprocess.run) -> bool:

   def reload_and_probe(
       config: HatchConfig,
       *,
       timeout: float = 15.0,
       runner: Callable[..., Any] = subprocess.run,
   ) -> DaemonCheckStatus:
   ```

   Inside `reload_and_probe`, narrow `config.endpoint_name` before the `_probe_endpoint` call:

   ```python
   endpoint_name = config.endpoint_name
   assert endpoint_name is not None  # guaranteed by HatchConfig model_validator
   return _probe_endpoint(
       host,
       port,
       endpoint_name,
       timeout=timeout,
       poll_interval=0.5,
   )
   ```

5. **Fix `renderer.py`.** In `_substitution_dict`, replace `"endpoint_name": cfg.endpoint_name` (which is `str | None`) with a value that is always `str`:

   ```python
   "endpoint_name": cfg.endpoint_name or cfg.being_name_lower,
   ```

   `cfg.being_name_lower` is a `@computed_field` returning `str`, so this expression is `str`. The model_validator guarantees `endpoint_name` is always set after construction; the `or` fallback is unreachable in practice but satisfies mypy.

6. **Run mypy and fix any remaining issues.** After sub-requests 1–5:

   ```bash
   uv run mypy
   ```

   Expected: `Success: no issues found`. If mypy surfaces additional issues (e.g., `Any`-typed questionary return values flowing into `str`-typed positions in `wizard.py`, or any implicit generics in `file_classes.py`), fix them in this same commit. Common patterns:
   - `questionary.text(...).ask()` returns `Any`; if assigned to a variable later used as `str`, add an explicit cast: `being_name: str = questionary.text(...).ask() or ""` (or use `assert being_name is not None` + the existing check already there).
   - Any `dict` or `list` without type parameters elsewhere — add `[str, Any]` or `[str]` as appropriate.

7. **Commit and verify the full gate.**

   ```bash
   git add pyproject.toml \
     packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py \
     packages/agent-core-hatchery/src/agent_core_hatchery/daemon_config.py \
     packages/agent-core-hatchery/src/agent_core_hatchery/daemon_probe.py \
     packages/agent-core-hatchery/src/agent_core_hatchery/renderer.py
   # (add any other files patched in step 6)
   git commit -m "feat: enable mypy --strict for agent-core-hatchery"
   just check
   ```

   Expected: green (lint, typecheck, contracts, test, patch-cov all pass).

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/agent-core-hatchery/src"` to `[tool.mypy].files`; add `[[tool.mypy.overrides]]` for `questionary.*` (`ignore_missing_imports = true`) and for `agent_core_hatchery.*` (individual strict flags) |
| `packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py` | Add `from typing import Any`; change `_venv_builder: Callable | None` and `_mcp_json_gen: Callable | None` to `Callable[..., Any] | None` |
| `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_config.py` | Add `from typing import Any`; change both `merged: dict` to `dict[str, Any]` |
| `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_probe.py` | Add `from collections.abc import Callable` and `from typing import Any`; annotate `runner` in `_stop_daemon`, `_start_daemon`, and `reload_and_probe`; add `endpoint_name` narrowing assertion before `_probe_endpoint` call |
| `packages/agent-core-hatchery/src/agent_core_hatchery/renderer.py` | Replace `cfg.endpoint_name` with `cfg.endpoint_name or cfg.being_name_lower` in `_substitution_dict` |
| Other `packages/agent-core-hatchery/src/` files (if mypy surfaces gaps) | Minor annotation fixes as discovered by running `uv run mypy` after sub-requests 1–5 |

No new files are created. No public interfaces (method signatures, class names, config shapes) change.

## Alternatives considered

1. **Use `strict = true` in the per-module override.** The existing pyproject.toml comment explains why this is incorrect: `strict = true` in a `[[tool.mypy.overrides]]` block leaks flags like `disallow_any_generics` to the other packages in `[tool.mypy] files` (core, channel, discord). The individual-flag approach used for discord is the correct pattern and must be followed here. Ruled out.

2. **Change `HatchConfig.endpoint_name` field type from `str | None` to `str`.** This would require changing the model to raise in the validator if `endpoint_name` is somehow still None, and would need careful testing to ensure YAML-loaded configs still work. The narrowing assertion in `daemon_probe.py` is a one-line change that achieves the same mypy satisfaction with zero risk to the Pydantic model's existing behavior. Ruled out (scope creep; behaviour-preserving principle).

3. **Add a `# type: ignore[union-attr]` comment at the `config.endpoint_name` call site.** Silencing mypy is always the last resort. The narrowing assertion documents the model_validator contract explicitly, making the invariant readable to future maintainers. Ruled out.

## Open questions

1. **Does `questionary` ship `py.typed` in the version pinned by the workspace?** If `questionary>=2.0` happens to ship a `py.typed` marker, the `ignore_missing_imports` override is unnecessary (though harmless). The Worker should run `uv run mypy` after sub-request 1 and check whether mypy emits any "missing stubs" error for questionary — if not, skip the questionary override.

2. **Are there additional annotation gaps in `wizard.py` not yet identified?** `wizard.py` is the heaviest user of `questionary`; with the library treated as `Any`, most call-sites will type-check cleanly. However, `warn_return_any = true` may still flag assignments like `being_name = questionary.text(...).ask()` if `being_name` is then passed to a typed function. The Worker must confirm by running mypy.

## Out of scope

- Enabling mypy `--strict` for any other package in the workspace — each is its own sub-ticket under B5 (#405).
- Adding `packages/agent-core-hatchery/tests` to mypy — test files are not in the `src/` tree and are not part of the production package.
- Changing the `HatchConfig` Pydantic model's field types beyond the minimal narrowing assertions needed in `daemon_probe.py`.
- Splitting or restructuring any hatchery module — this is a typing-discipline ticket only; no behavioral or structural changes.
