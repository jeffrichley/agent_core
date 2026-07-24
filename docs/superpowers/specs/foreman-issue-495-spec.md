# Spec: mypy --strict for agent-core-qa (issue #495)

## Goal

Enable `mypy --strict` enforcement for `packages/agent-core-qa/src` by adding it to the root `[tool.mypy] files` list, wiring a per-module strict-flags override (the individual-boolean pattern established for discord in issue #444), and fixing the type errors in `client.py` that strict mode surfaces. Part of Theme F Track B ticket B5 sub-ticket "g" covering `qa`. See the parent spec at `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (section B5).

`just check` must exit 0 on the resulting branch.

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/agent-core-qa/src"`.
- `pyproject.toml` has a `[[tool.mypy.overrides]]` block with `module = ["agent_core_qa.*"]` and all individual strict flags (`disallow_any_generics`, `disallow_subclassing_any`, `disallow_untyped_calls`, `disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_untyped_decorators`, `warn_return_any`, `no_implicit_reexport`, `strict_equality`, `extra_checks`) — the same set used for `agent_core_discord.*` and `agent_core_hatchery.*`.
- No `ignore_missing_imports` overrides added for `fastmcp` or `mcp`: both packages ship `py.typed` and are already installed as hard dependencies.
- `packages/agent-core-qa/src/agent_core_qa/client.py` imports `TextContent` from `mcp.types`.
- All four methods in `DaemonClient` that call `mcp.call_tool()` use `result.content` (not `result`) to access the list of content blocks, and use `isinstance(result.content[0], TextContent)` (not `hasattr`) to narrow before accessing `.text`.
- `uv run mypy` exits 0 with no errors or new suppressions.
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern fits. This is a typing-discipline closure: once `agent_core_qa.*` is under `--strict`, type regressions cannot be introduced silently.

**Why individual flags rather than `strict = true`.** The existing pyproject.toml comment (above the discord override block) explains the scoping problem: `strict = true` in a `[[tool.mypy.overrides]]` block leaks flags like `disallow_any_generics` to the other packages in `[tool.mypy] files`. The individual-boolean approach already used for `agent_core_discord.*`, `agent_core_hatchery.*` is the correct pattern and is followed here.

**No `ignore_missing_imports` needed.** Both `fastmcp` (3.2.4, `fastmcp>=3.0` in `agent-core-qa`'s `pyproject.toml`) and `mcp` (the underlying MCP SDK, a transitive dependency) ship `py.typed` markers and full type stubs. This is in contrast to `discord.py` (no stubs) or `questionary` (no stubs) which required `ignore_missing_imports` overrides. No such override is needed here.

**The primary annotation gap is an API mismatch against fastmcp 3.x.** The comment at `client.py:171` says "fastmcp returns a list of content items; unwrap the first text block." This was accurate for older fastmcp. As of fastmcp 3.x, `Client.call_tool()` returns a `CallToolResult` dataclass (not a list). `CallToolResult.content: list[mcp.types.ContentBlock]` holds the content items. The current code accesses `result[0]` directly on the `CallToolResult` instance — which is a subscript error (`CallToolResult` has no `__getitem__`) and the primary `[index]` error that `mypy --strict` will surface.

**Why the tests didn't catch this.** All qa scenarios are protected by the `daemon_liveness_required` autouse fixture in `conftest.py`, which skips every scenario unless the daemon is reachable at `http://127.0.0.1:8787`. In CI (pre-B3), the daemon is never started, so all seven scenarios skip. The `result[0]` runtime `TypeError` is never reached in practice.

**Fix pattern.** In all four affected methods (`send_envelope`, `poll_envelopes`, `list_pending`, `call_tool`):
1. Change `if result and hasattr(result[0], "text"):` → `if result.content and isinstance(result.content[0], TextContent):`
2. Change `result[0].text` → `result.content[0].text` (or a named local for clarity; after `isinstance` narrowing, `.text` is properly typed as `str`)
3. Drop `AttributeError` from the `except` tuples: `TextContent.text` is typed `str` and can never raise `AttributeError`; removing the dead except arm avoids a potential `warn_unused_ignores` or `extra_checks` flag
4. Add `from mcp.types import TextContent` to `client.py` imports

**`__init__.py` is a no-op.** The file is a single docstring with no imports or code; mypy has nothing to check.

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` mypy configuration.** In the `[tool.mypy]` table, add `"packages/agent-core-qa/src"` as a fifth entry in `files` (after `"packages/agent-core-hatchery/src"`). Then add one new `[[tool.mypy.overrides]]` section after the existing hatchery override block:

   ```toml
   [[tool.mypy.overrides]]
   module = ["agent_core_qa.*"]
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

2. **Fix `client.py` — add `TextContent` import.** In `packages/agent-core-qa/src/agent_core_qa/client.py`, add the `TextContent` import after the existing `try/except ImportError` block (keeping the conditional import guard intact). Place the import near the top of the module, after the stdlib imports and before the `_FakeTCPResponse` class:

   ```python
   from mcp.types import TextContent
   ```

   `mcp` is available as a transitive dependency of `fastmcp`; both ship `py.typed`.

3. **Fix `send_envelope`.** Replace the result-unwrapping block at approximately lines 171–177:

   Current:
   ```python
   # fastmcp returns a list of content items; unwrap the first text block.
   data: Any = None
   if result and hasattr(result[0], "text"):
       try:
           data = json.loads(result[0].text)
       except (json.JSONDecodeError, AttributeError):
           data = result[0].text if hasattr(result[0], "text") else str(result)
   ```

   Replacement:
   ```python
   # fastmcp 3.x returns a CallToolResult; text content lives in .content.
   data: Any = None
   if result.content and isinstance(result.content[0], TextContent):
       first = result.content[0]
       try:
           data = json.loads(first.text)
       except json.JSONDecodeError:
           data = first.text
   ```

4. **Fix `poll_envelopes`.** Replace the result-unwrapping block at approximately lines 212–219:

   Current:
   ```python
   data: Any = None
   if result and hasattr(result[0], "text"):
       try:
           data = json.loads(result[0].text)
       except (json.JSONDecodeError, AttributeError):
           # Justified: a malformed tool payload is treated as "no
           # data" and the poll loop retries on the next tick.
           data = None
   ```

   Replacement:
   ```python
   data: Any = None
   if result.content and isinstance(result.content[0], TextContent):
       try:
           data = json.loads(result.content[0].text)
       except json.JSONDecodeError:
           # Justified: a malformed tool payload is treated as "no
           # data" and the poll loop retries on the next tick.
           data = None
   ```

5. **Fix `list_pending`.** Replace the result-unwrapping block at approximately lines 246–252:

   Current:
   ```python
   if result and hasattr(result[0], "text"):
       try:
           return json.loads(result[0].text)
       except (json.JSONDecodeError, AttributeError):
           # Justified: a malformed payload falls through to the empty
           # snapshot returned below.
           pass
   ```

   Replacement:
   ```python
   if result.content and isinstance(result.content[0], TextContent):
       try:
           return json.loads(result.content[0].text)
       except json.JSONDecodeError:
           # Justified: a malformed payload falls through to the empty
           # snapshot returned below.
           pass
   ```

6. **Fix `call_tool`.** Replace the result-unwrapping block at approximately lines 287–291:

   Current:
   ```python
   data: Any = None
   if result and hasattr(result[0], "text"):
       try:
           data = json.loads(result[0].text)
       except (json.JSONDecodeError, AttributeError):
           data = result[0].text if hasattr(result[0], "text") else str(result)
   ```

   Replacement:
   ```python
   data: Any = None
   if result.content and isinstance(result.content[0], TextContent):
       first = result.content[0]
       try:
           data = json.loads(first.text)
       except json.JSONDecodeError:
           data = first.text
   ```

7. **Run mypy and fix any remaining issues.** After sub-requests 1–6:

   ```bash
   uv run mypy
   ```

   Expected output: `Success: no issues found`. If mypy surfaces additional issues (e.g. from the `_FastMCPClient` try/except import scope or generic type parameters on `Client[Any]` that `disallow_any_generics` flags), fix them in the same commit. Common fallbacks:
   - If mypy flags `_FastMCPClient` as possibly undefined (unlikely, since fastmcp ships py.typed and mypy resolves the import), annotate the variable with a type alias: `_FastMCPClient: type[Any]` in the except branch.
   - If `disallow_any_generics` flags `Client` usage (the class is `Generic[ClientTransportT]`), the usage `_FastMCPClient(...)` passes a `str` as the transport; mypy should infer the type parameter. If it doesn't, use `cast`.

   Commit all config + annotation changes together:
   ```bash
   git add pyproject.toml \
     packages/agent-core-qa/src/agent_core_qa/client.py
   git commit -m "feat: enable mypy --strict for agent-core-qa"
   ```

8. **Verify the full gate.**
   ```bash
   just check
   ```
   Expected: green (lint, typecheck, contracts, test suite with coverage, patch-cov all pass). The qa scenarios remain skip-by-default (daemon_liveness_required autouse fixture — no daemon is started in the test suite) so test counts are unchanged.

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/agent-core-qa/src"` to `[tool.mypy].files`; add `[[tool.mypy.overrides]]` for `agent_core_qa.*` with individual strict flags |
| `packages/agent-core-qa/src/agent_core_qa/client.py` | Add `from mcp.types import TextContent`; replace all four `result[0]`/`hasattr` patterns with `result.content[0]`/`isinstance(…, TextContent)` in `send_envelope`, `poll_envelopes`, `list_pending`, and `call_tool` |
| `packages/agent-core-qa/src/agent_core_qa/__init__.py` | No change (docstring only) |

No new files are created. No public interfaces change (the `DaemonClient` public surface — method names, signatures, `_FakeTCPResponse`, `_MCPToolResult` — is unchanged).

## Alternatives considered

1. **Use `strict = true` in the per-module override instead of individual flags.** The existing `pyproject.toml` comment explains why: `strict = true` in a `[[tool.mypy.overrides]]` block leaks `disallow_any_generics` and other flags to all packages in `[tool.mypy] files`, breaking the lighter baseline for `core` and `channel`. The individual-flag pattern used by `agent_core_discord.*` and `agent_core_hatchery.*` is the repo convention. Ruled out.

2. **Add `# type: ignore[index]` at each `result[0]` call site.** Suppression is the last resort; the underlying fix (switching to `result.content[0]`) is both correct and reveals the API mismatch so that future callers use the right fastmcp 3.x surface. Ruled out.

3. **Remove the `try/except ImportError` guard entirely** (fastmcp is a hard dependency, always available; `_HAS_FASTMCP` is dead code marked `# pragma: no cover`). This would simplify the module but is a behavioral change beyond the "fix annotations" scope of this ticket. B3 (release-gate overhaul) is the right place to restructure the client. Ruled out as out of scope.

## Open questions

None. Both source files were read. The fastmcp 3.2.4 `CallToolResult` dataclass and `mcp.types.TextContent` were verified in the installed `.venv`. All four call sites in `client.py` were identified. The `py.typed` markers for `fastmcp` and `mcp` were confirmed present.

## Out of scope

- Enabling mypy `--strict` for any other workspace package — each is its own sub-ticket.
- Adding `packages/agent-core-qa/tests` to mypy — test files are not in the `src/` tree.
- Fixing the `daemon_liveness_required` autouse fixture or wiring the qa scenarios into CI — that is B3.
- Removing the `_HAS_FASTMCP` optional-import guard — that is a behavioral change owned by B3.
- Any changes to `DaemonClient`'s public method signatures, `_FakeTCPResponse`, or `_MCPToolResult` shapes.
