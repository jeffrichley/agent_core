# Spec: reduce `endpoint.py` to composition + backward-compat re-exports (issue #443)

## Goal

Reduce `packages/agent-core-discord/src/agent_core_discord/endpoint.py` from its current 2295 lines to ~220 lines by wiring together the five mixin classes created in Steps 1–4 of the F-B6 series, keeping only the `__init__` method and the module-level registry items, and adding a backward-compat re-export block so every `from agent_core_discord.endpoint import X` statement in existing tests and external code continues to resolve without breakage. See issue #443.

## Acceptance criteria

- `wc -l packages/agent-core-discord/src/agent_core_discord/endpoint.py` reports ≤230 lines.
- `class DiscordEndpoint` declaration reads exactly `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):` and the class body contains only `_TYPING_TTL_SECONDS: float = 90.0` and `__init__` (verbatim from the current file).
- `_active_endpoints: dict[str, DiscordEndpoint] = {}` remains a module-level name directly defined in `endpoint.py`.
- `_default_attachments_dir` function remains directly defined in `endpoint.py`.
- Each of the following names resolves successfully when imported from `agent_core_discord.endpoint`:
  - `_ToolError` (re-export from `_exceptions.py`)
  - `_PersistError` (re-export from `_exceptions.py`)
  - `_parse_iso_datetime` (re-export from its mixin module)
  - `_check_embeds_within_caps` (re-export from its mixin module)
  - `_embed_char_count` (re-export from its mixin module)
  - `_TOOL_ALIASES` (re-export from its mixin module)
  - `_canonical_tool` (re-export from its mixin module)
  - `_serialize_poll` (re-export from its mixin module)
  - `_safe_filename` (re-export from its mixin module)
  - `_redact_url_qs` (re-export from its mixin module)
- `just test-fast` exits 0.
- `uv run pytest packages/agent-core-discord/ --no-cov -n auto` exits 0.
- No test file is modified; the re-export block does all the backward-compat work.

## Approach

**Pattern**: no GoF pattern fits. Python multiple-inheritance mixins are the standard idiom for splitting a god-class into focused facets without changing public API — the "make the right thing easy" principle (Google engineering canon). All external import paths are preserved; no consumer needs to change.

**Prerequisite check (do first)**: Steps 1–4 of the F-B6 series must have merged before this step can be implemented. The Worker must confirm the following files exist in `packages/agent-core-discord/src/agent_core_discord/` before making any changes:
- `_exceptions.py` — defining `_ToolError` and `_PersistError`
- The mixin module(s) defining `_AcksMixin`, `_LifecycleMixin`, `_HandlersMixin`, `_OutboundMixin`, `_ToolsMixin`

If those files are absent, stop and wait for the prior steps to merge.

**What stays in `endpoint.py`**:
1. Trimmed module docstring (4–6 lines: module purpose only; method-level detail has moved to the mixin files).
2. Imports required by `__init__` and the two module-level items below — trim the current 78-line import block down to only what is consumed locally.
3. `_active_endpoints: dict[str, DiscordEndpoint] = {}` — the module-level live-endpoint registry that discord.py event handlers look up by name. This is a module global, not a mixin concern; it stays here.
4. `_default_attachments_dir` — a module-level helper used by `__init__`; stays alongside the registry.
5. `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):` containing only `_TYPING_TTL_SECONDS = 90.0` and `__init__` (verbatim, no changes to the body).
6. Backward-compat re-export block at the bottom (see below).

**What is not in `endpoint.py` after this step** (moved in Steps 1–4):
- All method implementations — they now live in the mixin classes.
- Module-level helpers: `_parse_iso_datetime`, `_TOOL_ALIASES`, `_canonical_tool`, `_check_embeds_within_caps`, `_embed_char_count`, `_serialize_poll`, `_safe_filename`, `_redact_url_qs`, `_FILENAME_ALLOWED`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`.
- `_ToolError` and `_PersistError` — now in `_exceptions.py`.

**Backward-compat re-export block**: Place this block at the bottom of `endpoint.py`, after the class definition, with a clearly-worded comment so future contributors do not remove it by mistake:

```python
# ---------------------------------------------------------------------------
# Backward-compat re-exports.
# External code and existing tests import these names from this module.
# They now live in the mixin modules created in the F-B6 split; the imports
# below keep every `from agent_core_discord.endpoint import X` working without
# requiring any consumer change.  Do NOT remove these imports.
# ---------------------------------------------------------------------------
from agent_core_discord._exceptions import _PersistError, _ToolError  # noqa: F401
from agent_core_discord.<acks_module> import ...       # noqa: F401  (fill in from Step 1–4 result)
from agent_core_discord.<lifecycle_module> import ...  # noqa: F401
from agent_core_discord.<handlers_module> import ...   # noqa: F401
from agent_core_discord.<outbound_module> import ...   # noqa: F401
from agent_core_discord.<tools_module> import ...      # noqa: F401
```

The Worker must determine the actual module names and which symbols live in each by grepping the mixin files (see Sub-request 3 below) and filling in the concrete import paths. The `# noqa: F401` suppresses the "imported but unused" lint warning that would otherwise fire on re-exports. The complete symbol set to re-export (verified by grepping the current codebase for `from agent_core_discord.endpoint import`) is:

| Symbol | Imported by (current codebase) | Expected home after Steps 1–4 |
|---|---|---|
| `_parse_iso_datetime` | `tests/test_endpoint_outbound.py:22` | mixin module for `_ToolsMixin` |
| `_ToolError` | `tests/test_endpoint_outbound.py`, `tests/test_resolve_channel_id.py`, `tests/test_endpoint_hardening.py` | `_exceptions.py` |
| `_PersistError` | (issue #443 — external usage) | `_exceptions.py` |
| `_check_embeds_within_caps` | `tests/test_endpoint_hardening.py:12` | mixin module for `_OutboundMixin` |
| `_embed_char_count` | `tests/test_endpoint_hardening.py:249` | mixin module for `_OutboundMixin` |
| `_TOOL_ALIASES` | (issue #443 — external usage) | mixin module for `_LifecycleMixin` or `_OutboundMixin` |
| `_canonical_tool` | (issue #443 — external usage) | same module as `_TOOL_ALIASES` |
| `_serialize_poll` | (issue #443 — external usage) | mixin module for `_OutboundMixin` |
| `_safe_filename` | (issue #443 — external usage) | mixin module for `_HandlersMixin` |
| `_redact_url_qs` | (issue #443 — external usage) | mixin module for `_HandlersMixin` |

**`__init__` is copied verbatim**: The `__init__` body (currently lines 252–347 in `endpoint.py`) must not be altered — it initialises all instance attributes that mixin methods read. The only change to the class definition is adding the five mixin base classes to the class declaration line.

## Sub-requests (topologically sorted)

1. **Verify prerequisites** — confirm the mixin modules and `_exceptions.py` from Steps 1–4 exist:
   ```bash
   ls packages/agent-core-discord/src/agent_core_discord/_*.py
   ```
   Stop if any expected file is absent.

2. **Re-run the full backward-compat import grep** — confirm the complete symbol list has not grown since this spec was written:
   ```bash
   grep -r "from agent_core_discord.endpoint import" packages/ --include="*.py"
   ```
   If any symbol appears that is not in the table above (and is not `DiscordEndpoint` or `_active_endpoints`), add it to the re-export list before writing the new file.

3. **Map each re-exported symbol to its new module** — for each symbol in the table, find its definition in the mixin files:
   ```bash
   grep -rn "^def _parse_iso_datetime\|^_TOOL_ALIASES\|^def _canonical_tool\|^def _check_embeds_within_caps\|^def _embed_char_count\|^def _serialize_poll\|^def _safe_filename\|^def _redact_url_qs" \
     packages/agent-core-discord/src/agent_core_discord/_*.py
   ```
   Record the module path for each hit. This determines the concrete from-import lines in the re-export block.

4. **Rewrite `endpoint.py`** with the following structure (fill in concrete mixin module names from step 3):
   ```
   """DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.
   (4–6 line summary only; detailed method docs live in the mixin modules.)
   """
   from __future__ import annotations

   # — only the imports consumed by _active_endpoints, _default_attachments_dir,
   #   or __init__ directly —
   import logging
   from pathlib import Path
   from collections import OrderedDict
   from collections.abc import Callable
   from datetime import UTC, datetime
   from typing import TYPE_CHECKING, Any
   import asyncio
   import time

   from agent_core_discord.access import AccessConfig
   from agent_core_discord._exceptions import _ToolError, _PersistError  # noqa: F401
   from agent_core_discord.<module_a> import _AcksMixin
   from agent_core_discord.<module_b> import _LifecycleMixin
   from agent_core_discord.<module_c> import _HandlersMixin
   from agent_core_discord.<module_d> import _OutboundMixin
   from agent_core_discord.<module_e> import _ToolsMixin

   if TYPE_CHECKING:
       from agent_core.bus.handle import BusHandle

   log = logging.getLogger(__name__)

   _active_endpoints: dict[str, DiscordEndpoint] = {}


   def _default_attachments_dir(endpoint_name: str) -> Path:
       """Predictable default attachments root, no target-name parsing."""
       return (Path("~/.agent-core/attachments").expanduser() / endpoint_name).resolve()


   class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):
       """Bus endpoint that bridges one Discord bot to one named agent (1:1)."""

       _TYPING_TTL_SECONDS: float = 90.0

       def __init__(
           self,
           *,
           # ... (verbatim from current file) ...
       ):
           # ... (verbatim from current file) ...


   # ---------------------------------------------------------------------------
   # Backward-compat re-exports.  External code and existing tests import
   # these names from this module.  They now live in the mixin modules from
   # the F-B6 split.  Do NOT remove these imports.
   # ---------------------------------------------------------------------------
   from agent_core_discord._exceptions import _PersistError, _ToolError  # noqa: F401
   # (plus per-symbol imports from Steps 3–4 above)
   ```
   Note: `_ToolError` and `_PersistError` appear twice in this template — once at the top with the mixin imports (needed for `__init__` to reference them if any) and once in the re-export block. Consolidate to a single import statement if they are not used inside `endpoint.py` itself after the reduction.

5. **Confirm line count** — run `wc -l packages/agent-core-discord/src/agent_core_discord/endpoint.py` and confirm ≤230.

6. **Run the fast suite** — `just test-fast` — confirm exit 0.

7. **Run the discord characterization suite** — `uv run pytest packages/agent-core-discord/ --no-cov -n auto` — confirm exit 0.

8. **Commit**:
   ```bash
   git add packages/agent-core-discord/src/agent_core_discord/endpoint.py
   git commit -m "refactor(discord): reduce endpoint.py to composition + backward-compat re-exports"
   ```

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Rewrite** — 2295 → ≤230 lines; class declaration gains five mixin bases; all method bodies removed (moved to mixin modules in Steps 1–4); backward-compat re-export block added at bottom |

No other files change. Tests are not modified; `__init__.py` is not modified.

## Alternatives considered

1. **Update all test import sites to point directly at mixin modules** — avoids re-exports entirely but requires touching 15+ test files, pollutes unrelated diffs, and breaks the "no external-import change" guarantee called out as Critical in SpecReview #406. Ruled out.

2. **Use `__all__` without explicit re-import lines** — `__all__` controls `from endpoint import *` star-imports but does NOT make `from endpoint import _ToolError` work when `_ToolError` is not imported into the `endpoint` module namespace. Ruled out (doesn't solve the problem).

3. **Merge all six F-B6 steps into a single atomic PR** — reduces integration risk but produces an enormous diff that is harder to review and bisect. The issue explicitly decomposes into 6 steps; Step 5 is the composition wiring. Ruled out.

## Open questions

The exact module filenames for the five mixin classes are not known until the Worker inspects the output of Steps 1–4 (those files do not yet exist in the codebase at spec-writing time). The Worker must discover the filenames at implementation time by running the `ls` in Sub-request 1. The mixin class names (`_AcksMixin`, `_LifecycleMixin`, `_HandlersMixin`, `_OutboundMixin`, `_ToolsMixin`) and the exceptions module name (`_exceptions.py`) are fixed by the issue.

## Out of scope

- Creating the mixin modules (`_acks.py`, `_lifecycle.py`, `_handlers.py`, `_outbound.py`, `_tools.py`, `_exceptions.py`) — that is Steps 1–4 of F-B6.
- Modifying any test file — re-exports handle backward compat entirely.
- Modifying `packages/agent-core-discord/src/agent_core_discord/__init__.py` — it imports `DiscordEndpoint` from `endpoint` and is already correct.
- Refactoring `__init__`'s body — the `__init__` is copied verbatim; its content is a separate concern.
- Step 6 of the F-B6 series (whatever cleanup follows this step).
