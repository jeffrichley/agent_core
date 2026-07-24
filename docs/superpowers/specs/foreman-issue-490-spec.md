# Spec: mypy --strict for agent-core-busproxy (issue #490)

## Goal

Add `packages/agent-core-busproxy/src` to the root `[tool.mypy].files` list,
apply the same `--strict` per-module override already established for
`agent_core_discord.*`, and fix any annotation gaps until `uv run mypy` exits 0.
Part of Track B sub-ticket B5b, scoped in
`docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md`
(Decision D4, ticket B5).

---

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/agent-core-busproxy/src"` as a fourth entry.
- `pyproject.toml` has a `[[tool.mypy.overrides]]` block with `module = ["agent_core_busproxy.*"]` and the same set of individual strict flags used for `agent_core_discord.*`.
- If `fastmcp` or `mcp` lack an inline `py.typed` marker, `pyproject.toml` has the appropriate `[[tool.mypy.overrides]]` block(s) with `ignore_missing_imports = true`; if either class definition in busproxy subclasses an `Any`-typed base, it carries a `# type: ignore[misc]` comment citing the reason.
- `uv run mypy` exits 0 (`Success: no issues found`).
- `just check` exits 0 on the resulting branch (lint, typecheck, contracts, tests, patch-cov all pass).
- No logic changes in any busproxy source file — annotation and config changes only.

---

## Approach

No GoF pattern fits. This is a typing-discipline closure: "make the right thing easy" (Google's engineering canon) — once the package is in `[tool.mypy].files` under strict flags, type regressions cannot be introduced silently.

**Why individual flags, not `strict = true`.** The existing `pyproject.toml` comment (lines 118–119) explains the mechanic: `strict = true` in a `[[tool.mypy.overrides]]` block leaks flags such as `disallow_any_generics` to the *other* packages in `files` (`packages/core/src`, `packages/agent-core-channel/src`), which are on a lighter flag set. The individual-boolean-flag pattern already used for `agent_core_discord.*` scopes correctly. Follow that pattern exactly.

**Third-party stubs.** All busproxy runtime dependencies except `fastmcp` and `mcp` ship with `py.typed` (`anyio`, `httpx`, `typer` are all typed). The Worker must verify whether `fastmcp` (>=3.2) and `mcp` (>=1.0) ship `py.typed` in the installed wheel. If either lacks stubs, two things follow: (a) add an `ignore_missing_imports` override for that package; (b) because mypy then treats the imported names as `Any`, any class that *subclasses* an imported fastmcp type (`ProxyProvider` in `proxy.py`, `Middleware` in `transient.py`) triggers `disallow_subclassing_any` — the fix is a `# type: ignore[misc]` comment on the class definition line citing "fastmcp class has no stubs".

**Expected annotation fixes in `proxy.py`.** `_NoOutputSchemaProxyProvider` overrides two private fastmcp methods (`_list_tools`, `_get_tool`) and annotates both as `-> Any`, with an in-code comment explaining the intent ("avoid annotating with fastmcp's non-public Tool internals"). If fastmcp *is* typed and those parent methods have concrete return types, mypy will flag the `-> Any` widening as an `[override]` error; the fix is `# type: ignore[override]` on each method header, referencing the existing explanatory comment. `_strip(tool: Any) -> Any` and `client_factory() -> Any` use explicit `Any` throughout, so `warn_return_any` will not fire on them.

**Expected annotation fixes in `transient.py`.** `TransientErrorMiddleware.on_call_tool(self, context: Any, call_next: Any) -> ToolResult` uses `Any` for both parameters because fastmcp's middleware protocol types are non-public. If fastmcp's `Middleware` base is typed and declares `on_call_tool` with concrete parameter types, the override will trigger `[override]`; fix with `# type: ignore[override]`. `disallow_untyped_calls` does *not* fire when calling an `Any`-typed callable (`call_next(context)` is safe). All other functions in `transient.py` are already fully annotated.

**`__main__.py` and `__init__.py`.** Both files are fully annotated. `__main__.py` has `-> None` on `main` and `_run`; `__init__.py` is a one-line docstring. No fixes expected.

**Sequencing.** Make the `pyproject.toml` change first (sub-request 1), check stubs (sub-request 2), then run mypy and fix issues in a single annotation commit (sub-request 3). No test file is modified.

---

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` — files list and strict override block.**

   In `[tool.mypy]`, add `"packages/agent-core-busproxy/src"` as the fourth `files` entry:
   ```toml
   [tool.mypy]
   python_version = "3.12"
   files = [
       "packages/core/src",
       "packages/agent-core-channel/src",
       "packages/agent-core-discord/src",
       "packages/agent-core-busproxy/src",
   ]
   ```

   Add a new `[[tool.mypy.overrides]]` block immediately after the existing
   `agent_core_discord.*` block, following the same individual-flag pattern:
   ```toml
   # agent-core-busproxy held to full --strict (issue #490). Individual flags
   # for the same reason as discord above.
   [[tool.mypy.overrides]]
   module = ["agent_core_busproxy.*"]
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

2. **Check `fastmcp` and `mcp` for `py.typed` and add `ignore_missing_imports` if absent.**

   Run:
   ```bash
   uv run --no-sync python -c "
   import pathlib, fastmcp, mcp
   for pkg in (fastmcp, mcp):
       p = pathlib.Path(pkg.__file__).parent
       print(pkg.__name__, (p / 'py.typed').exists())
   "
   ```

   For any package that prints `False` (no `py.typed`), add a `[[tool.mypy.overrides]]`
   block in `pyproject.toml` before the `agent_core_busproxy.*` block:
   ```toml
   # fastmcp publishes no type stubs — silence missing-import noise.
   [[tool.mypy.overrides]]
   module = ["fastmcp", "fastmcp.*"]
   ignore_missing_imports = true
   ```
   (Same pattern for `mcp`/`mcp.*` if needed.)

3. **Run `uv run mypy` and fix annotation gaps.**

   ```bash
   uv run --no-sync mypy
   ```

   Fix every error reported for `packages/agent-core-busproxy/src`. Based on code
   review, the expected issues are (apply only what mypy actually flags):

   - **`proxy.py` — `_list_tools` and `_get_tool` override widening** (fires if fastmcp
     is typed and the parent methods have concrete return types):
     ```python
     async def _list_tools(self) -> Any:  # type: ignore[override]
         return [self._strip(t) for t in await super()._list_tools()]

     async def _get_tool(self, name: str, version: Any = None) -> Any:  # type: ignore[override]
         return self._strip(await super()._get_tool(name, version))
     ```

   - **`proxy.py` — `_NoOutputSchemaProxyProvider(ProxyProvider)` subclassing** (fires
     if fastmcp is untyped and `ignore_missing_imports` was added, making `ProxyProvider`
     become `Any`):
     ```python
     class _NoOutputSchemaProxyProvider(ProxyProvider):  # type: ignore[misc]
     ```

   - **`transient.py` — `on_call_tool` override** (fires if fastmcp's `Middleware` base
     declares typed parameters):
     ```python
     async def on_call_tool(self, context: Any, call_next: Any) -> ToolResult:  # type: ignore[override]
     ```

   - **`transient.py` — `TransientErrorMiddleware(Middleware)` subclassing** (fires if
     `Middleware` is `Any` after `ignore_missing_imports`):
     ```python
     class TransientErrorMiddleware(Middleware):  # type: ignore[misc]
     ```

   After fixing all errors, confirm:
   ```bash
   uv run --no-sync mypy
   # Expected: Success: no issues found (in N source files)
   ```

4. **Verify the full gate.**

   ```bash
   just check
   ```

   Expected: green (lint, typecheck, contracts, tests, patch-cov all pass).

---

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/agent-core-busproxy/src"` to `[tool.mypy].files`; add `[[tool.mypy.overrides]]` block for `agent_core_busproxy.*` with individual strict flags; optionally add `ignore_missing_imports` override(s) for `fastmcp`/`mcp` if they lack `py.typed` |
| `packages/agent-core-busproxy/src/agent_core_busproxy/proxy.py` | Add `# type: ignore[override]` to `_list_tools` and/or `_get_tool` if fastmcp is typed and the overrides widen the return type; add `# type: ignore[misc]` to `_NoOutputSchemaProxyProvider` class line if fastmcp is untyped |
| `packages/agent-core-busproxy/src/agent_core_busproxy/transient.py` | Add `# type: ignore[override]` to `on_call_tool` if `Middleware` is typed and parameters conflict; add `# type: ignore[misc]` to `TransientErrorMiddleware` class line if fastmcp is untyped |
| Other busproxy source files (if mypy surfaces gaps) | Minor annotation fixes as discovered by running mypy |

No test files are modified. No logic changes to any source file.

---

## Alternatives considered

1. **Use `strict = true` in the override block instead of listing individual flags.** The `strict = true` umbrella in a `[[tool.mypy.overrides]]` section leaks `disallow_any_generics` and other flags to the other packages in `[tool.mypy].files`, breaking typecheck for `packages/core/src` and `packages/agent-core-channel/src` which are on a lighter flag set. Ruled out; the individual-boolean pattern already established in pyproject.toml (with explanatory comment) is correct.

2. **Bundle B5b with other B5 sub-tickets in a single PR.** B5 covers 8 packages. Bundling all produces a large annotation diff that is hard to review, increases blast radius, and contradicts the explicit sub-ticket decomposition the Track B spec prescribes. Ruled out.

3. **Annotate busproxy source with full concrete fastmcp types from non-public internals.** `proxy.py`'s comment explicitly chose `Any` to avoid coupling to non-public fastmcp types. Replacing those `Any` annotations with concrete fastmcp-private types would create a fragile dependency on the library's internal API surface. Ruled out; `Any` with `# type: ignore` comments is the correct boundary for non-public third-party types.

---

## Open questions

None. All four busproxy source files (`__init__.py`, `__main__.py`, `proxy.py`,
`transient.py`) were read directly. The `pyproject.toml` mypy config and the discord
override pattern were verified from the file. The only runtime unknown — whether
`fastmcp` and `mcp` ship `py.typed` in the installed wheel — is resolved at
implementation time by the one-liner in sub-request 2.

---

## Out of scope

- Enabling `--strict` for the other B5 packages (briefs, hatchery, inbound, voice, webcam, qa, credentials) — each is a separate sub-ticket.
- Enabling `--strict` (or upgrading the flag set) for `packages/core/src` and `packages/agent-core-channel/src` — on a lighter flag set by design; a separate decision.
- Adding `packages/agent-core-busproxy/tests` to mypy's `files` list — test files are not in the `src/` tree and not part of the production package.
- Any change to `justfile` — `typecheck` already delegates to `uv run --no-sync mypy`, which reads `pyproject.toml` automatically.
- Any change to `.github/workflows/ci.yml` — the `check` job runs `just check` which runs `uv run --no-sync mypy`; adding busproxy to `files` is picked up automatically.
