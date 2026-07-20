# Spec: split `claude_code_mcp.py` and collapse 7× not-started guard (issue #407)

## Goal

`packages/core/src/agent_core/endpoints/claude_code_mcp.py` (1 153 lines) repeats the `if self._handle is None:` not-started guard seven times across its MCP tool closures and the `deliver()` method. This spec introduces two helpers — `_require_handle()` (raising form) and `_not_started_error()` (dict form) — to collapse all seven, then carves the file into three cohesive submodules behind an identical public surface. The file is already covered by `packages/core/src`'s `--strict` mypy scope; no new `files` entry is needed.

Addresses issue #407, part of Theme F Track B (B7 ticket in `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md`).

## Acceptance criteria

- `grep -rn "if self\._handle is None" packages/core/src/agent_core/endpoints/claude_code_mcp/ | wc -l` returns exactly `1` after the refactor. The single remaining occurrence is the implementation line inside `_require_handle()` itself (`if self._handle is None: raise RuntimeError(...)`); all seven former inline guards are collapsed into helper calls.
- `_require_handle()` exists as a private method on `ClaudeCodeMCPEndpoint`; it raises `RuntimeError(f"endpoint '{self.name}' is not started")` when `_handle is None` and returns the `BusHandle` otherwise.
- `_not_started_error()` exists as a module-level function in `_tools.py`; it returns `{"status": "error", "message": "endpoint not started"}`.
- The four `raise RuntimeError` guards (in `deliver`, `send`, `consume`, `reply`) are replaced by `self._require_handle()` / `ep._require_handle()`.
- The three `return {"status": "error", ...}` guards (in `handle`, `ack`, `nack`) are replaced by `if ep._handle is None: return _not_started_error()`.
- `from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint` continues to work without modification in all existing callers.
- `SessionRegistry` lives in `_session.py`; `ClaudeCodeMCPEndpoint` lives in `_endpoint.py`; tool closures live in `_tools.py`.
- No module exceeds ~800 lines.
- `mypy packages/core/src` passes clean (no regressions, no new suppressions). (`--strict` is not yet enforced in `[tool.mypy]`; the CI gate is `just typecheck`, which runs `mypy` without `--strict`. If a Worker wishes to run `mypy --strict packages/core/src` as an extra check, they should first confirm the pre-existing baseline passes before attributing any failure to their changes.)
- `just check` passes (lint, typecheck, tests, coverage gates).

## Approach

No GoF pattern applies. This is DRY (eliminate 7× duplication) + SRP (each file one responsibility). The pattern-naming exercise confirms: straightforward extraction, not a structural pattern.

**Two distinct changes, two commits:** (1) guard collapse — add the two helpers, replace the seven inline guards, verify tests pass; (2) module split — convert the `.py` file to a package, move code with no logic changes, verify again. Mixing guard collapse with the move would make the diff unreviewable.

**Guards.** `_require_handle()` is a method on `ClaudeCodeMCPEndpoint` (needs `self.name` for the error message). It returns `BusHandle` so callers can assign `handle = self._require_handle()` and immediately use the handle without re-asserting. `_not_started_error()` is a module-level free function in `_tools.py` — it needs no state. The two `list_endpoints()` and `describe_endpoint()` tools that return `[]`/`None` when not started are intentional safe-default patterns for read-only directory lookups, not error guards; they stay as-is and are not part of the 7-guard count.

**Module split.** Converting `endpoints/claude_code_mcp.py` to a package `endpoints/claude_code_mcp/` preserves the existing import path `from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint` through the `__init__.py` re-export. This is the standard Python "module-to-package" refactor pattern. No caller file changes. The three submodules:

- `_session.py` (~70 lines): `SessionRegistry` middleware class only. No module-level constants (those live with the code that uses them).
- `_endpoint.py` (~790 lines): module-level constants (`_META_KEY_RE`, `_MISSING_ACK_DELAY_MAX_SECONDS`, `_OUTBOUND_REGISTRY_TTL_*`), `ClaudeCodeMCPEndpoint` class. The `_register_tools()` call in `__init__` becomes `register_tools(self)` imported from `._tools`.
- `_tools.py` (~340 lines): `_DISCORD_INBOUND_ONLY_KEYS` constant (only used in `reply()`), `_not_started_error()` helper, `register_tools(ep)` free function with all tool closures. Uses `from __future__ import annotations` + `TYPE_CHECKING` guard to import `ClaudeCodeMCPEndpoint` for the type annotation, breaking the circular dependency at runtime.

**mypy --strict.** `BusHandle` is already under `TYPE_CHECKING` in the current file; `_require_handle() -> BusHandle` is fine with `from __future__ import annotations`. The `TYPE_CHECKING`-guarded import of `ClaudeCodeMCPEndpoint` in `_tools.py` is the standard pattern mypy understands. All new files are under `packages/core/src`, already in `files`. No relaxation of any mypy flag.

**Tests.** No test changes required. All test files import from `agent_core.endpoints.claude_code_mcp`; the package `__init__.py` makes that import continue working. Existing tests (`test_claude_code_mcp.py`, `test_session_registry.py`, `test_claude_code_mcp_auto_ack.py`, etc.) exercise the guards, session tracking, and tool behavior through the public `ClaudeCodeMCPEndpoint` surface, which is unchanged.

## Sub-requests (topologically sorted)

1. **Add `_require_handle()` to `ClaudeCodeMCPEndpoint`** in `packages/core/src/agent_core/endpoints/claude_code_mcp.py`.

   Insert after `_unregister_session()` (currently line 312):

   ```python
   def _require_handle(self) -> BusHandle:
       """Assert the endpoint has been started; raise RuntimeError otherwise.

       Returns the handle so callers can write:
           handle = self._require_handle()
       """
       if self._handle is None:
           raise RuntimeError(f"endpoint '{self.name}' is not started")
       return self._handle
   ```

   `BusHandle` is already imported under `TYPE_CHECKING`; `from __future__ import annotations` is already present.

2. **Replace the four raising guards** with `self._require_handle()` (or `handle = self._require_handle()` where the return value is used).

   - `deliver()` (line 657): `if self._handle is None: raise RuntimeError(...)` → `handle = self._require_handle()` then replace all `self._handle.ack(...)` in that branch with `handle.ack(...)`.
   - `send()` tool closure (line 855–856): same pattern → `handle = self._require_handle()`, then `handle.publish(env)`.
   - `consume()` tool closure (line 982–983): → `handle = self._require_handle()`, then `handle.ack(...)`.
   - `reply()` tool closure (line 1047–1048): → `handle = self._require_handle()`, then `handle.publish(env)` and `handle.ack(in_reply_to)`.

3. **Add `_not_started_error()` free function** at module level, between the constants block (ending ~line 63) and `class SessionRegistry` (~line 66) in the same file (temporary; moves to `_tools.py` in SR8):

   ```python
   def _not_started_error() -> dict[str, str]:
       """Standard not-started response for dict-returning tool functions."""
       return {"status": "error", "message": "endpoint not started"}
   ```

4. **Replace the three dict-form guards** with `if self._handle is None: return _not_started_error()`.

   - `handle()` tool closure (line 927): replace guard.
   - `ack()` tool closure (line 938): replace guard.
   - `nack()` tool closure (line 950): replace guard.

5. **Verify guard collapse** — run `just test-fast` or `uv run pytest --no-cov -n0 packages/core/tests/ -x`; confirm all existing tests pass. Commit:

   ```
   git add packages/core/src/agent_core/endpoints/claude_code_mcp.py
   git commit -m "refactor(claude-mcp): collapse 7x not-started guard into _require_handle + _not_started_error"
   ```

6. **Convert `claude_code_mcp.py` to a package** — create the directory and skeleton `__init__.py`:

   Create `packages/core/src/agent_core/endpoints/claude_code_mcp/__init__.py`:
   ```python
   """ClaudeCodeMCPEndpoint — bus endpoint that hosts a FastMCP server.

   See _endpoint.py for the full class docstring and protocol notes.
   """

   from ._endpoint import ClaudeCodeMCPEndpoint as ClaudeCodeMCPEndpoint

   __all__ = ["ClaudeCodeMCPEndpoint"]
   ```

   The original `claude_code_mcp.py` still exists at this point; Python will use the package over the `.py` file if the directory comes first in the search path. Delete `claude_code_mcp.py` **after** `_endpoint.py` is in place (SR9).

   > **⚠ Import window warning:** The `__init__.py` created in this step imports `from ._endpoint import ClaudeCodeMCPEndpoint`, but `_endpoint.py` does not exist until SR9. Because Python prefers a package (`claude_code_mcp/`) over a same-named `.py` file in the same directory, **do not run tests or import `agent_core.endpoints.claude_code_mcp` between this step and SR9** — any such import will raise `ModuleNotFoundError` even though the original `claude_code_mcp.py` is still present. Run tests only after SR10.

7. **Create `_session.py`** — move `SessionRegistry` into the package:

   Create `packages/core/src/agent_core/endpoints/claude_code_mcp/_session.py` containing:
   - The module docstring (see below)
   - Imports: `from __future__ import annotations`, `logging`, `Any`, `anyio`, `Middleware`, `MiddlewareContext`; `TYPE_CHECKING` guard importing `ClaudeCodeMCPEndpoint` from `._endpoint`
   - The complete `SessionRegistry` class body (lines 66–119 of the original file), verbatim

   ```python
   """Session-registry middleware for ClaudeCodeMCPEndpoint.

   Captures the active FastMCP ServerSession on the first MCP message,
   enabling server-push of notifications to the connected agent.
   """

   from __future__ import annotations

   import logging
   from typing import TYPE_CHECKING, Any

   import anyio
   from fastmcp.server.middleware import Middleware, MiddlewareContext

   if TYPE_CHECKING:
       from ._endpoint import ClaudeCodeMCPEndpoint

   log = logging.getLogger(__name__)


   class SessionRegistry(Middleware):
       # ... verbatim copy of existing body ...
   ```

8. **Create `_tools.py`** — move tool closures into the package:

   Create `packages/core/src/agent_core/endpoints/claude_code_mcp/_tools.py` containing:
   - `from __future__ import annotations`
   - All tool-related imports (uuid, datetime, UTC, Any, Envelope, AcknowledgmentPayload, etc.)
   - `TYPE_CHECKING` guard: `from ._endpoint import ClaudeCodeMCPEndpoint`
   - `_DISCORD_INBOUND_ONLY_KEYS` constant (moved from top of original file)
   - `_not_started_error()` free function (moved from SR3/SR4 addition)
   - `register_tools(ep: ClaudeCodeMCPEndpoint) -> None` function containing all tool closures verbatim, with `self._` references changed to `ep._` and `self.` to `ep.`

   Skeleton shape:
   ```python
   """MCP tool implementations for ClaudeCodeMCPEndpoint.

   ``register_tools(ep)`` is called once in ``ClaudeCodeMCPEndpoint.__init__``
   to register all bus tools on the FastMCP server instance.
   """

   from __future__ import annotations

   import uuid
   from datetime import UTC, datetime
   from typing import TYPE_CHECKING, Any

   from agent_core.bus.envelope import AcknowledgmentPayload, Envelope

   if TYPE_CHECKING:
       from ._endpoint import ClaudeCodeMCPEndpoint

   _DISCORD_INBOUND_ONLY_KEYS: frozenset[str] = frozenset(
       {"author_display_name", "author_id", "guild_id", "is_bot", "is_dm"}
   )


   def _not_started_error() -> dict[str, str]:
       """Standard not-started response for dict-returning MCP tool functions."""
       return {"status": "error", "message": "endpoint not started"}


   def register_tools(ep: ClaudeCodeMCPEndpoint) -> None:
       """Register all bus MCP tools on ep._mcp."""

       @ep._mcp.tool()
       async def send(
           to: str,
           kind: str,
           payload: dict[str, Any],
           correlation_id: str | None = None,
           in_reply_to: str | None = None,
           metadata: dict[str, Any] | None = None,
           urgency: str = "green",
           expires_at: str | None = None,
       ) -> dict:
           """..."""
           handle = ep._require_handle()
           # ... rest verbatim, self.→ep. / self._handle.→handle. ...

       # ... remaining tools verbatim with self.→ep. substitution ...
   ```

9. **Create `_endpoint.py`** — move the trimmed class into the package:

   Create `packages/core/src/agent_core/endpoints/claude_code_mcp/_endpoint.py` containing:
   - Full module docstring from original file
   - All imports from the original file except `AcknowledgmentPayload` and `Envelope` sub-fields used only in tools (keep imports that `ClaudeCodeMCPEndpoint` methods need directly)
   - Module-level constants: `_META_KEY_RE`, `_MISSING_ACK_DELAY_MAX_SECONDS`, `_OUTBOUND_REGISTRY_TTL_MIN_SECONDS`, `_OUTBOUND_REGISTRY_TTL_MAX_SECONDS`
   - `_require_handle()` method on the class
   - `ClaudeCodeMCPEndpoint` class body with `_register_tools()` replaced by a call to `register_tools(self)` imported from `._tools`; `SessionRegistry` imported from `._session`

   The `__init__` method line:
   ```python
   # was: self._register_tools()
   from .._claude_code_mcp._tools import register_tools  # wrong – use relative import correctly
   ```

   Correct relative import at top of file:
   ```python
   from ._session import SessionRegistry
   from ._tools import register_tools
   ```

   In `__init__`:
   ```python
   self._mcp.add_middleware(SessionRegistry(self))
   register_tools(self)
   if tool_mounters:
       for mounter in tool_mounters:
           mounter(self._mcp)
   ```

10. **Delete original `claude_code_mcp.py`** and run the full suite:

    ```bash
    rm packages/core/src/agent_core/endpoints/claude_code_mcp.py
    uv run pytest --no-cov -n0 packages/core/tests/ -x
    mypy packages/core/src
    ```

    All tests must pass; mypy must be clean. Commit as a move-only commit:
    ```
    git add packages/core/src/agent_core/endpoints/claude_code_mcp/
    git rm packages/core/src/agent_core/endpoints/claude_code_mcp.py
    git commit -m "refactor(claude-mcp): carve into _session / _endpoint / _tools submodules"
    ```

## File-level changes

| File | Action | What changes |
|---|---|---|
| `packages/core/src/agent_core/endpoints/claude_code_mcp.py` | **Delete** | Replaced by `claude_code_mcp/` package |
| `packages/core/src/agent_core/endpoints/claude_code_mcp/__init__.py` | **Create** | Re-exports `ClaudeCodeMCPEndpoint`; preserves all existing import paths |
| `packages/core/src/agent_core/endpoints/claude_code_mcp/_session.py` | **Create** | `SessionRegistry` middleware class (~70 lines) |
| `packages/core/src/agent_core/endpoints/claude_code_mcp/_endpoint.py` | **Create** | Module constants + `ClaudeCodeMCPEndpoint` class + `_require_handle()` method; calls `register_tools(self)` in `__init__` (~790 lines) |
| `packages/core/src/agent_core/endpoints/claude_code_mcp/_tools.py` | **Create** | `_DISCORD_INBOUND_ONLY_KEYS` + `_not_started_error()` + `register_tools(ep)` with all 11 tool closures (~340 lines) |

No test files change. No caller files change.

## Alternatives considered

1. **Keep single-file, collapse guards only (no split).** Simplest — adds `_require_handle()` and `_not_started_error()`, replaces 7 guards, done. The B7 ticket explicitly requires the module split; a guard-only change satisfies the guard criterion but leaves a 1 150-line god-module. Ruled out: incomplete scope.

2. **Sibling `.py` files instead of a package (e.g., `_mcp_session.py`, `_mcp_tools.py` alongside `claude_code_mcp.py`).** Avoids the package conversion; `claude_code_mcp.py` becomes the entry point importing from siblings. Cleaner for small extractions, but confusing here because `claude_code_mcp.py` would still be 800+ lines and the sibling files would have no natural namespace home. The package approach bundles the submodules under the natural `claude_code_mcp/` namespace and is the standard Python move for module-to-package refactors. Ruled out: worse coherence and discoverability.

3. **Mixin class instead of a `register_tools(ep)` free function for tool extraction.** A `_ToolsMixin` class mixed into `ClaudeCodeMCPEndpoint` avoids the `self.→ep.` substitution. Mixins with mypy `--strict` require careful `Protocol` or `TypeVar` annotation to avoid `Cannot access attribute "X" for class "_ToolsMixin"` errors. The free-function approach with a `TYPE_CHECKING`-guarded import is simpler and already idiomatic in this codebase. Ruled out: adds annotation boilerplate for no behavioral benefit.

## Open questions

None. All file paths, line numbers, and mypy constraints are verified against the actual codebase. The `[tool.mypy] files` entry already covers `packages/core/src`, so no config change is needed.

## Out of scope

- The `list_endpoints()` and `describe_endpoint()` tool closures that return `[]` and `None` respectively when `_handle is None` — these are intentional safe-default patterns for read-only directory queries, not error guards. Leave them as-is.
- Any behavioral changes to tool semantics, envelope routing, or session tracking — this is a pure structural refactor.
- Adding new tests — existing test coverage already pins guard behavior, session tracking, and tool semantics through the `ClaudeCodeMCPEndpoint` public surface.
- Adding `packages/core/src` to `[tool.mypy] files` — it is already there.
- Discord endpoint split (B6) — a separate, parallel ticket.
