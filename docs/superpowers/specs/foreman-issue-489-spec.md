# Spec: add agent-core-briefs to mypy --strict (issue #489)

## Goal

Add `packages/agent-core-briefs/src` to `[tool.mypy] files` under a per-module `--strict` override and fix every annotation gap until `mypy --strict` exits 0 on the package. Part of epic #262 · Theme F #269 Track B, sub-ticket B5a, governed by `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md`.

## Acceptance criteria

- `packages/agent-core-briefs/src` appears in `[tool.mypy] files` in the root `pyproject.toml`.
- A `[[tool.mypy.overrides]]` block for `module = ["agent_core_briefs.*"]` carries the same individual strict flags as the existing `agent_core_discord.*` block (no `strict = true` umbrella — listed individually to avoid leaking flags to other packages in `files`).
- `[[tool.mypy.overrides]]` blocks for `simpleeval` / `simpleeval.*` and `fastmcp` / `fastmcp.*` suppress `ignore_missing_imports` (both libraries are build-dependencies of the package but neither ships bundled stubs; blocking on upstreams is out of scope).
- `uv run mypy packages/agent-core-briefs/src` exits 0 on the merged branch.
- No `# type: ignore` comment in `agent_core_briefs` is without a bracketed error code and a short prose reason.
- Existing two `# type: ignore` lines already present (`[attr-defined]` in `protocol.py` and `[assignment]` in `submit.py`) gain an inline prose justification comment if they don't have one.
- No production-logic change — this is annotation-only; function bodies, algorithm, and public API surfaces are unchanged.

## Approach

No design pattern required — this is straightforward annotation-only work. The pattern follows Decision D4 from the design doc: `--strict` directly, no relaxed bar committed.

**Adding the strict override.** The project already has one per-module strict block (for `agent_core_discord.*` at `pyproject.toml:119`). The `agent_core_briefs.*` block is identical in structure. The individual-flag form (rather than `strict = true`) keeps the flags scoped to the target module, which is the repo's established convention.

**Third-party import suppression.** Two packages used by `agent_core_briefs` lack bundled stubs:
- `simpleeval` — imported at runtime in `playbook.py`; no stub package is available.
- `fastmcp` — imported only under `TYPE_CHECKING` in `mcp.py`; stubs may or may not be present depending on the installed version. The `ignore_missing_imports` override is safe in either case: if stubs exist they are used; if not, the override suppresses the missing-import error and `FastMCP` types as `Any`.

**`disallow_untyped_decorators` and `@mcp.tool(...)`.** If `fastmcp` stubs are absent, `mcp.tool(...)` resolves to `Any`, making the seven `@mcp.tool(...)` decorator applications in `mcp.py` untyped. The fix is to add `# type: ignore[misc]  # fastmcp: decorator resolves to Any without stubs` after the `@mcp.tool(` opening on each of the seven inner functions. Do this only if the `disallow_untyped_decorators` error actually fires (step 9 of sub-requests checks).

**Annotation fixes — two mechanical classes:**

1. **Bare `dict` / `list[dict]` → `dict[str, Any]` / `list[dict[str, Any]]`** throughout. The `disallow_any_generics` flag catches every unparametrised generic. The `Any` value type is correct here: playbook context dicts, section dicts, config dicts, and YAML-parsed dicts all hold heterogeneous values. No invariant is strengthened by using a narrower type.

2. **Untyped function parameters** — two occurrences:
   - `submit._audit_deliver`: parameter `session` is missing its `ComposeSession` annotation.
   - `plugin.wire_endpoints_after_registration` inner `_mounter`: parameter `bus_handle` is missing its `BusHandle` annotation. `BusHandle` is already available under `TYPE_CHECKING` in other files; it needs to be added to `plugin.py`'s `TYPE_CHECKING` guard.

**`re.Match` type parameter.** `config._replace` uses bare `re.Match`; must be `re.Match[str]` under `disallow_any_generics`.

**`_AttrDict.__iter__` return type.** `playbook._AttrDict.__iter__` lacks a return annotation; must be `-> Iterator[str]` (add `from collections.abc import Iterator`).

**Existing `# type: ignore` lines are kept.** Both carry error codes and remain necessary under strict:
- `protocol.py`: `sig = inspect.signature(destination.deliver)  # type: ignore[attr-defined]` — `destination: object` has no `deliver` attribute statically.
- `submit.py`: `color = resolved_spec.color  # type: ignore[assignment]` — `SectionSpec.color` is `str | dict`; the int narrowing after resolution is not expressible without `reveal_type`-level gymnastics.

Add a brief prose comment alongside each if not already present: `# mypy: object has no deliver attr; validated by validate_destination_signature at load time` and `# mypy: resolve_colors_for_sections always returns int; non-int color raises PlaybookParseError` respectively.

## Sub-requests (topologically sorted)

1. **`pyproject.toml`: add `simpleeval` and `fastmcp` missing-import overrides.**
   Append two new `[[tool.mypy.overrides]]` blocks before the `agent_core_briefs.*` block:
   ```toml
   [[tool.mypy.overrides]]
   module = ["simpleeval", "simpleeval.*"]
   ignore_missing_imports = true

   [[tool.mypy.overrides]]
   module = ["fastmcp", "fastmcp.*"]
   ignore_missing_imports = true
   ```

2. **`pyproject.toml`: add `agent_core_briefs.*` strict override.** Append:
   ```toml
   [[tool.mypy.overrides]]
   module = ["agent_core_briefs.*"]
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

3. **`pyproject.toml`: add `packages/agent-core-briefs/src` to `[tool.mypy] files`.** Change:
   ```toml
   files = [
       "packages/core/src",
       "packages/agent-core-channel/src",
       "packages/agent-core-discord/src",
   ]
   ```
   to:
   ```toml
   files = [
       "packages/core/src",
       "packages/agent-core-channel/src",
       "packages/agent-core-discord/src",
       "packages/agent-core-briefs/src",
   ]
   ```

4. **Fix `packages/agent-core-briefs/src/agent_core_briefs/protocol.py`.**
   - Add `Any` to the typing import: `from typing import TYPE_CHECKING, Protocol, runtime_checkable` → `from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable`
   - `Fetcher.fetch(self, config: dict, when: datetime) -> dict` → `config: dict[str, Any], -> dict[str, Any]`
   - `Destination.deliver` parameters: `sections: list[dict]` → `list[dict[str, Any]]`; `config: dict` → `dict[str, Any]`
   - Annotate the `# type: ignore[attr-defined]` line with prose: `# type: ignore[attr-defined]  # destination: object has no deliver attr; shape validated at load time by validate_destination_signature`

5. **Fix `packages/agent-core-briefs/src/agent_core_briefs/config.py`.**
   - `def _replace(match: re.Match) -> str:` → `def _replace(match: re.Match[str]) -> str:`

6. **Fix `packages/agent-core-briefs/src/agent_core_briefs/engine.py`.**
   - `FetcherInvocation.config: dict` (dataclass field) → `dict[str, Any]`
   - `async def _run_one(...) -> tuple[bool, str, dict]:` → `-> tuple[bool, str, dict[str, Any]]:`
   - `_merge_into_namespace(context: dict, namespace: str, payload: dict) -> None` → `context: dict[str, Any]`, `payload: dict[str, Any]`

7. **Fix `packages/agent-core-briefs/src/agent_core_briefs/playbook.py`.**
   - Add `from collections.abc import Iterator` to imports.
   - `Playbook.destinations: list[dict]` (dataclass field) → `list[dict[str, Any]]`
   - `destinations: list[dict] = []` (local variable in `parse_playbook`) → `list[dict[str, Any]]`
   - `resolve_colors_for_sections(..., *, context: dict)` → `context: dict[str, Any]`
   - `resolve_conditional_sections(conditional_sections: list[SectionSpec], context: dict)` → `context: dict[str, Any]`
   - `_classify_block(block: dict) -> str:` → `block: dict[str, Any]`
   - `_to_section_spec(block: dict) -> SectionSpec:` → `block: dict[str, Any]`
   - `_resolve_color_value(section: SectionSpec, palette: dict[str, int], context: dict)` → `context: dict[str, Any]`
   - `_eval_expr(expr: str, context: dict) -> Any` → `context: dict[str, Any]`
   - `_wrap_context(ctx: dict) -> dict:` → `ctx: dict[str, Any]) -> dict[str, Any]:`
   - `_AttrDict.__init__(self, data: dict):` → `data: dict[str, Any]) -> None:`
   - `_AttrDict.__iter__(self):` → `def __iter__(self) -> Iterator[str]:`

8. **Fix `packages/agent-core-briefs/src/agent_core_briefs/session.py`.**
   - `destinations: list[dict]` field → `list[dict[str, Any]]`

9. **Fix `packages/agent-core-briefs/src/agent_core_briefs/validators.py`.**
   - Add `from typing import Any` import (the file currently has no `from typing import ...` line).
   - `sections: list[dict]` (function parameter in `validate_submission`) → `list[dict[str, Any]]`
   - `submitted_by_id: dict[str, dict] = {}` (local variable) → `dict[str, dict[str, Any]]`

10. **Fix `packages/agent-core-briefs/src/agent_core_briefs/submit.py`.**
    - Add `Any` to the typing import: `from typing import TYPE_CHECKING` → `from typing import TYPE_CHECKING, Any`
    - Add `ComposeSession` to the import from `agent_core_briefs.session` (currently only `SessionRegistry` is imported).
    - `_audit_deliver(audit_log: AuditLog, session, ...)` → `session: ComposeSession`
    - `submit_brief(*, ..., sections: list[dict], ...)` → `sections: list[dict[str, Any]]`
    - `_enrich_sections_with_spec(submitted: list[dict], *, ..., context: dict,) -> list[dict]:` → `submitted: list[dict[str, Any]]`, `context: dict[str, Any]`, `-> list[dict[str, Any]]:`
    - `enriched: list[dict] = []` (local variable inside `_enrich_sections_with_spec`) → `list[dict[str, Any]]`
    - Annotate the `# type: ignore[assignment]` line with prose: `# type: ignore[assignment]  # resolve_colors_for_sections always returns int; PlaybookParseError raised otherwise`

11. **Fix `packages/agent-core-briefs/src/agent_core_briefs/tools.py`.**
    - `list_sections(...) -> dict:` → `-> dict[str, Any]:`
    - `get_section_spec(...) -> dict:` → `-> dict[str, Any]:`
    - `validate_section(..., fields: list[dict],) -> dict:` → `fields: list[dict[str, Any]]`, `-> dict[str, Any]:`
    - `compress_sections(...) -> dict:` → `-> dict[str, Any]:`
    - `add_extension_section(..., fields: list[dict],) -> dict:` → `fields: list[dict[str, Any]]`, `-> dict[str, Any]:`

12. **Fix `packages/agent-core-briefs/src/agent_core_briefs/fetchers/cli.py`.**
    - `CliFetcher.fetch(self, config: dict, when: datetime) -> dict:` → `config: dict[str, Any]`, `-> dict[str, Any]:`
    - Add `from typing import Any` import.

13. **Fix `packages/agent-core-briefs/src/agent_core_briefs/fetchers/filesystem_read.py`.**
    - `FilesystemReadFetcher.fetch(self, config: dict, when: datetime) -> dict:` → `config: dict[str, Any]`, `-> dict[str, Any]:`
    - Add `from typing import Any` import.

14. **Fix `packages/agent-core-briefs/src/agent_core_briefs/fetchers/now.py`.**
    - `NowFetcher.fetch(self, config: dict, when: datetime) -> dict:` → `config: dict[str, Any]`, `-> dict[str, Any]:`
    - `result: dict = {…}` → `result: dict[str, Any] = {…}`
    - Add `from typing import Any` import.

15. **Fix `packages/agent-core-briefs/src/agent_core_briefs/destinations/discord_embed.py`.**
    - `DiscordEmbedDestination.deliver(self, sections: list[dict], …, config: dict, …) -> DeliveryResult:` → `sections: list[dict[str, Any]]`, `config: dict[str, Any]`
    - `_render_section_to_embed_dict(section: dict) -> dict[str, Any]:` → `section: dict[str, Any]`
    - Add `Any` to the `from typing import TYPE_CHECKING, Any` import (already present).

16. **Fix `packages/agent-core-briefs/src/agent_core_briefs/destinations/markdown_file.py`.**
    - `MarkdownFileDestination.deliver(self, sections: list[dict], …, config: dict, …) -> DeliveryResult:` → `sections: list[dict[str, Any]]`, `config: dict[str, Any]`
    - `_render_markdown(sections: list[dict], …) -> str:` → `sections: list[dict[str, Any]]`
    - Add `from typing import Any` import.

17. **Fix `packages/agent-core-briefs/src/agent_core_briefs/plugin.py`.**
    - Add `from agent_core.bus.handle import BusHandle` to the `TYPE_CHECKING` guard (currently the guard imports `typer`, `agent_core.bus.protocol.Endpoint`, `agent_core.plugins.specs.RunnerServices`).
    - Inner closure `_mounter(bus_handle, *, …) -> None:` → `bus_handle: BusHandle`

18. **Verify: run `uv run mypy packages/agent-core-briefs/src` and fix any remaining errors.**
    - After completing sub-requests 1–17, run `uv run mypy packages/agent-core-briefs/src`. The above sub-requests enumerate all known annotation gaps; if mypy surfaces additional bare `dict` / `list[dict]` errors not listed above (e.g., further dataclass fields or local variables that were missed during spec review), fix them following the same pattern (`dict` → `dict[str, Any]`, `list[dict]` → `list[dict[str, Any]]`), adding `from typing import Any` to the file's import block if it is not already present. If `disallow_untyped_decorators` fires on `@mcp.tool(…)` calls in `mcp.py` (indicating fastmcp stubs are unavailable), add `# type: ignore[misc]  # fastmcp: mcp.tool() resolves to Any without stubs` immediately after each `@mcp.tool(` opening (7 occurrences). Do not add these pre-emptively; check the actual mypy output first.
    - Confirm with: `uv run mypy packages/agent-core-briefs/src`; expected: `Success: no issues found in N source files`.
    - Confirm the overall gate still passes: `just check`.

## File-level changes

| File | Action | What changes |
|---|---|---|
| `pyproject.toml` | **Modify** | Add `packages/agent-core-briefs/src` to `[tool.mypy] files`; add `simpleeval`, `fastmcp`, and `agent_core_briefs.*` override blocks |
| `src/agent_core_briefs/protocol.py` | **Modify** | Add `Any` to typing import; `dict` → `dict[str, Any]`, `list[dict]` → `list[dict[str, Any]]` in `Fetcher` and `Destination` protocols; annotate existing `# type: ignore[attr-defined]` with prose |
| `src/agent_core_briefs/config.py` | **Modify** | `re.Match` → `re.Match[str]` |
| `src/agent_core_briefs/engine.py` | **Modify** | `dict` → `dict[str, Any]` in `FetcherInvocation.config` field, `_run_one` return type, and `_merge_into_namespace` |
| `src/agent_core_briefs/playbook.py` | **Modify** | Add `Iterator` import; `dict` → `dict[str, Any]` in `Playbook.destinations` field, `parse_playbook` local, six functions, `_classify_block`, and `_to_section_spec`; `_AttrDict.__iter__` return type; `__init__` `-> None` |
| `src/agent_core_briefs/session.py` | **Modify** | `destinations: list[dict]` → `list[dict[str, Any]]` |
| `src/agent_core_briefs/validators.py` | **Modify** | Add `Any` import; `sections: list[dict]` → `list[dict[str, Any]]`; `submitted_by_id: dict[str, dict]` → `dict[str, dict[str, Any]]` |
| `src/agent_core_briefs/submit.py` | **Modify** | Add `Any` to typing import; import `ComposeSession`; type `session` param in `_audit_deliver`; `dict` → `dict[str, Any]` in three functions and one local variable; annotate existing `# type: ignore[assignment]` with prose |
| `src/agent_core_briefs/tools.py` | **Modify** | `-> dict:` → `-> dict[str, Any]:` on five public functions; `list[dict]` → `list[dict[str, Any]]` in two functions |
| `src/agent_core_briefs/fetchers/cli.py` | **Modify** | `dict` → `dict[str, Any]` in `fetch`; add `Any` import |
| `src/agent_core_briefs/fetchers/filesystem_read.py` | **Modify** | `dict` → `dict[str, Any]` in `fetch`; add `Any` import |
| `src/agent_core_briefs/fetchers/now.py` | **Modify** | `dict` → `dict[str, Any]` in `fetch` and local variable; add `Any` import |
| `src/agent_core_briefs/destinations/discord_embed.py` | **Modify** | `dict` → `dict[str, Any]` in `deliver` and `_render_section_to_embed_dict` |
| `src/agent_core_briefs/destinations/markdown_file.py` | **Modify** | `dict` → `dict[str, Any]` in `deliver` and `_render_markdown`; add `Any` import |
| `src/agent_core_briefs/plugin.py` | **Modify** | Add `BusHandle` to `TYPE_CHECKING` block; annotate `bus_handle` in `_mounter` |
| `src/agent_core_briefs/mcp.py` | **Conditionally modify** | If fastmcp stubs absent: add `# type: ignore[misc]` to 7 `@mcp.tool(…)` decorators |

No changes to test files, `justfile`, CI workflows, or any other packages.

## Alternatives considered

1. **`strict = true` umbrella in the overrides block instead of individual flags.** Consistent with `[tool.mypy]` global config but the repo explicitly chose individual flags for per-module overrides after observing that the umbrella leaks flags to sibling packages in `files`. See the existing `agent_core_discord.*` comment at `pyproject.toml:113`. Ruled out.

2. **Narrow config/context dicts with `TypedDict`.** Several `dict[str, Any]` could be `TypedDict` subclasses (e.g., `FetcherConfig`, `SectionDict`). This is a larger refactor with no requirement in the issue; the issue asks for `--strict` clean, not for typed-dict schemas. Adding `TypedDict`s would also be a behavior-adjacent change (serialisation paths reference keys by name). Ruled out as scope creep.

3. **Suppress the entire module via `# type: ignore` at file level.** Would achieve `--strict` clean without any annotation work. Violates the issue's acceptance criterion ("fix annotations until clean") and the project's approach to strict typing. Ruled out.

## Open questions

None that block implementation. One runtime-condition caveat: whether `fastmcp` ships usable stubs in the version installed in the dev venv determines whether sub-request 18's conditional `mcp.py` fix is needed. The Worker discovers this by running mypy in sub-request 18; the fix is a straightforward `# type: ignore[misc]` if needed.

## Out of scope

- Adding `agent-core-briefs` to `[tool.pytest.ini_options] testpaths` (separate ticket; it's not part of B5a).
- Annotating test files under `packages/agent-core-briefs/tests/` (test files are excluded from mypy's `files` list and the coverage config).
- Changing any runtime logic, error messages, or public API shapes.
- Adding `TypedDict` schemas for section dicts, config dicts, or playbook context dicts.
- Fixing type annotations in any other package (B5 covers the remaining packages in subsequent sub-tickets).
- Adding `fastmcp` or `simpleeval` type stubs upstream; the `ignore_missing_imports` overrides are the project-local resolution.
