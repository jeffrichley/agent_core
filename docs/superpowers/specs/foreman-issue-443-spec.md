# Spec: reduce `endpoint.py` to composition + backward-compat re-exports (issue #443)

## Goal

Reduce `packages/agent-core-discord/src/agent_core_discord/endpoint.py` from its current 2,295 lines to ~220 lines by wiring together the five mixin classes created in Steps 1–4 of the F-B6 series (decomposed from issue #406), retaining only the module-level registry, `_default_attachments_dir`, and `__init__`, and adding a backward-compat re-export block so every existing `from agent_core_discord.endpoint import X` import continues to resolve without any consumer change. See issue #443.

## Acceptance criteria

- `wc -l packages/agent-core-discord/src/agent_core_discord/endpoint.py` reports ≤ 230 lines.
- `class DiscordEndpoint` declaration reads exactly `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):` and the class body contains only `_TYPING_TTL_SECONDS: float = 90.0` and `__init__` (verbatim from the current file, lines 252–347).
- `_active_endpoints: dict[str, DiscordEndpoint] = {}` remains a module-level name directly defined in `endpoint.py`.
- `_default_attachments_dir` function remains directly defined in `endpoint.py`.
- Each of the following names resolves when imported from `agent_core_discord.endpoint` (verified by a small `python -c "from agent_core_discord.endpoint import X"` check):
  - `_ToolError` — re-export from `_exceptions.py`
  - `_PersistError` — re-export from `_exceptions.py`
  - `_parse_iso_datetime` — re-export from the mixin module where it lives post-split
  - `_check_embeds_within_caps` — re-export from the mixin module where it lives post-split
  - `_embed_char_count` — re-export from the mixin module where it lives post-split
  - `_TOOL_ALIASES` — re-export from the mixin module where it lives post-split
  - `_canonical_tool` — re-export from the mixin module where it lives post-split
  - `_serialize_poll` — re-export from the mixin module where it lives post-split
  - `_safe_filename` — re-export from the mixin module where it lives post-split
  - `_redact_url_qs` — re-export from the mixin module where it lives post-split
- `uv run pytest packages/agent-core-discord/ --no-cov -n auto` exits 0.
- `just test-fast` exits 0.
- No test file is modified; the re-export block does all backward-compat work.

## Approach

**Pattern**: No GoF pattern fits. Python multiple-inheritance mixins are the standard idiom for decomposing a god-class into focused facets while preserving the public API — Google's "make the right thing easy" principle applied to file size and review ergonomics. All external import paths are preserved by re-exports; no consumer changes.

**Prerequisite gate (check first)**: Steps 1–4 of F-B6 must have merged before this step can be implemented. The Worker must confirm the following files exist in `packages/agent-core-discord/src/agent_core_discord/` before making any changes:

- `_exceptions.py` — defines `_ToolError` and `_PersistError`
- The five mixin module(s) defining `_AcksMixin`, `_LifecycleMixin`, `_HandlersMixin`, `_OutboundMixin`, `_ToolsMixin`

If those files are absent, stop. This step has a hard dependency on the prior steps.

**What stays in `endpoint.py`**:

1. A trimmed 4–6 line module docstring (module purpose only; method-level documentation has moved to the mixin files).
2. Only the imports consumed locally by `__init__` and the two module-level definitions — trim the current 78-line import block accordingly.
3. `_active_endpoints: dict[str, DiscordEndpoint] = {}` — the module-level live-endpoint registry that discord.py event handlers look up by name. This is a process-global, not a mixin concern; it must stay here.
4. `_default_attachments_dir(endpoint_name: str) -> Path` — a module-level helper used by `__init__`; stays alongside the registry.
5. `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):` with only `_TYPING_TTL_SECONDS: float = 90.0` and `__init__` (verbatim body, no changes).
6. Backward-compat re-export block (see below).

**What is removed from `endpoint.py`** (moved in Steps 1–4):

- All method implementations — they now live in the five mixin classes.
- Module-level helpers: `_parse_iso_datetime`, `_TOOL_ALIASES`, `_canonical_tool`, `_check_embeds_within_caps`, `_embed_char_count`, `_serialize_poll`, `_safe_filename`, `_redact_url_qs`, `_FILENAME_ALLOWED`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`.
- `_ToolError` and `_PersistError` — now in `_exceptions.py`.

**Backward-compat re-export block**: Place this at the bottom of `endpoint.py` after the class definition, with a clearly-worded banner comment so future contributors do not accidentally remove it:

```python
# ---------------------------------------------------------------------------
# Backward-compat re-exports.
# External code and existing tests import these names directly from this
# module.  They now live in the mixin modules from the F-B6 split; the
# imports below keep every `from agent_core_discord.endpoint import X`
# working without requiring any consumer change.  Do NOT remove.
# ---------------------------------------------------------------------------
from agent_core_discord._exceptions import _PersistError, _ToolError  # noqa: F401
# (per-symbol imports from mixin modules — fill in after running Sub-request 3)
```

The Worker must discover the concrete module paths for each re-exported symbol by grepping the mixin files (Sub-request 3 below) and filling in the actual `from agent_core_discord.<module> import <symbol>  # noqa: F401` lines. The `# noqa: F401` suppresses the "imported but unused" lint warning that fires on re-exports.

**Symbol-to-module mapping** (verified by grepping current test/production code for `from agent_core_discord.endpoint import`):

| Symbol | Confirmed importer | Expected home after Steps 1–4 |
|---|---|---|
| `_ToolError` | `tests/test_endpoint_outbound.py`, `tests/test_resolve_channel_id.py`, `tests/test_endpoint_hardening.py` | `_exceptions.py` |
| `_PersistError` | issue #443 external usage | `_exceptions.py` |
| `_parse_iso_datetime` | `tests/test_endpoint_outbound.py:22` | mixin module for `_ToolsMixin` (scheduled-event tools use it) |
| `_check_embeds_within_caps` | `tests/test_endpoint_hardening.py:12` | mixin module for `_OutboundMixin` |
| `_embed_char_count` | `tests/test_endpoint_hardening.py:249` | mixin module for `_OutboundMixin` |
| `_TOOL_ALIASES` | issue #443 external usage | mixin module for `_HandlersMixin` or `_OutboundMixin` |
| `_canonical_tool` | issue #443 external usage | same module as `_TOOL_ALIASES` |
| `_serialize_poll` | issue #443 external usage | mixin module for `_OutboundMixin` (used in `_fetch`) |
| `_safe_filename` | issue #443 external usage | mixin module for `_HandlersMixin` (used in attachment inbound path) |
| `_redact_url_qs` | issue #443 external usage | mixin module for `_HandlersMixin` (used in inbound logging) |

**`__init__` is copied verbatim**: Lines 251–347 of the current `endpoint.py`. The only change to the class declaration is adding the five mixin bases. The body must not be altered — it initialises all instance attributes that mixin methods read.

## Sub-requests (topologically sorted)

1. **Verify prerequisites** — confirm mixin modules and `_exceptions.py` exist:
   ```bash
   ls packages/agent-core-discord/src/agent_core_discord/_*.py
   ```
   Expected: `_exceptions.py` plus at least five mixin module files (one per mixin class). **Stop if any expected file is absent.**

2. **Re-run the backward-compat import grep** — confirm the symbol list has not grown since this spec was written:
   ```bash
   grep -rn "from agent_core_discord\.endpoint import" packages/ --include="*.py"
   ```
   Add any newly discovered symbol (not `DiscordEndpoint` or `_active_endpoints`) to the re-export list before proceeding.

3. **Map each re-exported symbol to its new module** — for each symbol in the table above, find its definition in the mixin files:
   ```bash
   grep -rn "^def _parse_iso_datetime\|^_TOOL_ALIASES\|^def _canonical_tool\|^def _check_embeds_within_caps\|^def _embed_char_count\|^def _serialize_poll\|^def _safe_filename\|^def _redact_url_qs" \
     packages/agent-core-discord/src/agent_core_discord/_*.py
   ```
   Record the module path for each hit. This determines the concrete `from-import` lines in the re-export block.

4. **Rewrite `endpoint.py`** with the following structure (fill in the concrete mixin and module names discovered in steps 1–3):
   ```python
   """DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.

   The bulk of the implementation lives in the five mixin classes imported below.
   This module owns only the module-level live-endpoint registry and __init__.
   """
   from __future__ import annotations

   import asyncio
   import logging
   import time
   from collections import OrderedDict
   from collections.abc import Callable
   from datetime import UTC, datetime
   from pathlib import Path
   from typing import TYPE_CHECKING, Any

   from agent_core_discord.access import AccessConfig
   from agent_core_discord._exceptions import _PersistError, _ToolError  # noqa: F401
   from agent_core_discord.<module_a> import _AcksMixin
   from agent_core_discord.<module_b> import _LifecycleMixin
   from agent_core_discord.<module_c> import _HandlersMixin
   from agent_core_discord.<module_d> import _OutboundMixin
   from agent_core_discord.<module_e> import _ToolsMixin

   if TYPE_CHECKING:
       from agent_core.bus.handle import BusHandle

   log = logging.getLogger(__name__)

   _active_endpoints: dict[str, "DiscordEndpoint"] = {}


   def _default_attachments_dir(endpoint_name: str) -> Path:
       """Predictable default attachments root, no target-name parsing."""
       return (Path("~/.agent-core/attachments").expanduser() / endpoint_name).resolve()


   class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):
       """Bus endpoint that bridges one Discord bot to one named agent (1:1)."""

       _TYPING_TTL_SECONDS: float = 90.0

       def __init__(
           self,
           *,
           # ... (verbatim from current endpoint.py lines 251–347) ...
       ):
           # ... (verbatim body) ...


   # ---------------------------------------------------------------------------
   # Backward-compat re-exports.
   # External code and existing tests import these names from this module.
   # They now live in the mixin modules from the F-B6 split.  Do NOT remove.
   # ---------------------------------------------------------------------------
   from agent_core_discord._exceptions import _PersistError, _ToolError  # noqa: F401
   # (fill in per-symbol imports from steps 3–4)
   ```
   Note: if `_ToolError`/`_PersistError` are already imported at the top for use in `__init__` (unlikely — `__init__` does not raise them directly), consolidate to a single import statement.

5. **Confirm line count**:
   ```bash
   wc -l packages/agent-core-discord/src/agent_core_discord/endpoint.py
   ```
   Expected: ≤ 230 lines.

6. **Verify all re-exports resolve**:
   ```bash
   uv run python -c "
   from agent_core_discord.endpoint import (
       _ToolError, _PersistError, _parse_iso_datetime,
       _check_embeds_within_caps, _embed_char_count,
       _TOOL_ALIASES, _canonical_tool, _serialize_poll,
       _safe_filename, _redact_url_qs, DiscordEndpoint, _active_endpoints,
   )
   print('all imports resolved')
   "
   ```
   Expected: prints `all imports resolved` with no `ImportError`.

7. **Run the discord package suite**:
   ```bash
   uv run pytest packages/agent-core-discord/ --no-cov -n auto
   ```
   Expected: exit 0.

8. **Run the full fast suite**:
   ```bash
   just test-fast
   ```
   Expected: exit 0.

9. **Commit**:
   ```bash
   git add packages/agent-core-discord/src/agent_core_discord/endpoint.py
   git commit -m "refactor(discord): reduce endpoint.py to composition + backward-compat re-exports"
   ```

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Rewrite** — 2,295 → ≤ 230 lines; class declaration gains five mixin bases; all method bodies removed (moved to mixin modules in Steps 1–4); backward-compat re-export block added at the bottom |

No other files change. Tests are not modified. `__init__.py` is not modified (it already imports only `DiscordEndpoint`).

## Alternatives considered

1. **Update all test import sites to point directly at the mixin modules** — avoids re-exports but requires touching 10+ test files, pollutes this diff with test churn, and breaks the "no consumer change" guarantee that SpecReview #406 flagged as Critical. Ruled out.

2. **Use `__all__` without explicit re-import lines** — `__all__` governs star-imports only; it does NOT make `from endpoint import _ToolError` work if `_ToolError` is not imported into the `endpoint` module namespace. Ruled out (does not solve the problem).

3. **Merge all six F-B6 steps into a single PR** — reduces integration risk but produces an enormous diff that is harder to review, bisect, and roll back. The issue explicitly decomposes into 6 steps. Ruled out.

## Open questions

The exact module filenames and which symbols live in which mixin module are not known at spec-writing time — the mixin files from Steps 1–4 do not yet exist in the codebase. The Worker must discover this at implementation time via Sub-requests 1 and 3. The mixin class names (`_AcksMixin`, `_LifecycleMixin`, `_HandlersMixin`, `_OutboundMixin`, `_ToolsMixin`) and the exceptions module name (`_exceptions.py`) are fixed by the issue.

This is a known unknown, not a blocking ambiguity: the Worker can resolve it with a single `ls` and a `grep` once the prior steps have merged.

## Out of scope

- Creating the mixin modules (`_acks.py`, `_lifecycle.py`, `_handlers.py`, `_outbound.py`, `_tools.py`, `_exceptions.py`) — that is Steps 1–4 of F-B6.
- Modifying any test file — the re-export block handles backward compat entirely.
- Modifying `packages/agent-core-discord/src/agent_core_discord/__init__.py` — it already imports only `DiscordEndpoint`, which stays in `endpoint.py`.
- Refactoring `__init__`'s body — it is copied verbatim; its content is a separate concern.
- Step 6 of the F-B6 series (whatever cleanup follows this step).
