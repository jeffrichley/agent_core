# Spec: extract `_HandlersMixin` from `endpoint.py` (issue #441)

## Goal

Create `packages/agent-core-discord/src/agent_core_discord/_handlers.py` containing `class _HandlersMixin` with eleven inbound handler methods extracted byte-identical from `endpoint.py`. Update `DiscordEndpoint`'s base classes to inherit from `_HandlersMixin`. The existing characterization suite (`test_endpoint_characterization.py`) and `just test-fast` must pass unchanged. Addresses issue #441 (Step 3 of 6 in the F-B6 refactor, decomposed from issue #406).

---

## Acceptance criteria

- `packages/agent-core-discord/src/agent_core_discord/_handlers.py` exists and defines `class _HandlersMixin` with exactly these eleven methods (byte-identical bodies to those in the current `endpoint.py`): `_add_listener`, `_channel_allowed`, `_remember_inbound_mapping`, `_record_inbound`, `_resolve_channel_id`, `_typing_while_pending`, `_resolve_user_display_name`, `_make_on_message_handler`, `_make_on_reaction_add_handler`, `_make_on_raw_poll_vote_handler`, `_make_on_raw_message_lifecycle_handler`.
- `_handlers.py` also defines `_redact_url_qs` as a module-level helper function (required by `_make_on_message_handler`, currently in `endpoint.py`, exclusively used by the moved methods).
- `_handlers.py` imports **nothing from `endpoint.py`** (no circular imports).
- `endpoint.py`'s `DiscordEndpoint` declaration changes to `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin):`.
- `endpoint.py` adds `from agent_core_discord._handlers import _HandlersMixin` to its imports.
- The eleven moved methods are removed from `endpoint.py`. `_redact_url_qs` is removed from `endpoint.py`.
- Five now-unused imports are removed from `endpoint.py`: `import time`, `EventPayload` (from `agent_core.bus.envelope`), `InboundContext` and `gate_message` (from `agent_core_discord.access`), `parse_sigil` (from `agent_core_discord.sigil`), `scrub_surrogates` (from `agent_core_discord.text_sanitize`).
- No test file is modified.
- No changes to `_lifecycle.py`, `_acks.py`, or `_exceptions.py`.
- `just test-fast` exits 0 with no new failures; the existing characterization suite in `test_endpoint_characterization.py` remains green.

---

## Approach

No GoF pattern fits. This is SRP-driven structural decomposition via cooperative multiple inheritance — the same pattern applied in issue #440 for `_AcksMixin` and `_LifecycleMixin`. Each new module holds one named responsibility; `endpoint.py` imports the mixins and wires them via the MRO. The constraint "imports nothing from `endpoint.py`" (to avoid circular imports) is the governing rule.

**Why `_redact_url_qs` moves too.** The function at `endpoint.py:226` is a module-level helper used exclusively inside the `_make_on_message_handler` closure (lines 825 and 865). If it stayed in `endpoint.py`, `_handlers.py` would need to import it from there, creating a circular dependency (`endpoint.py` → `_handlers.py` → `endpoint.py`). Moving it to `_handlers.py` — where all its callers now live — is the minimal change that preserves byte-identical method bodies without a circular import. No tests import `_redact_url_qs` directly, so no re-export is needed.

**Byte-identical body invariant.** The bodies of all eleven moved methods are character-for-character identical to those in the current `endpoint.py`. The moved code references `self.*` attributes (all owned by `DiscordEndpoint.__init__`) and module-level names (`log`, `_ToolError`, `_redact_url_qs`, etc.) that will be defined at the top of `_handlers.py`. No renaming, no logic changes, no new parameters.

**MRO.** The updated declaration `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin):` places `_HandlersMixin` third. The three mixins share no method names, so MRO precedence order is irrelevant in practice. `_LifecycleMixin.start()` (in `_lifecycle.py`) calls `self._add_listener(...)` and `self._make_on_message_handler()` etc.; Python resolves these via `self` (the concrete `DiscordEndpoint` instance), which inherits from all three mixins. No changes to `_lifecycle.py` are needed.

**`log` per module.** `_handlers.py` defines its own `log = logging.getLogger(__name__)`. Log messages from moved methods will now show logger name `agent_core_discord._handlers`. This is acceptable for a move-only step.

**Imports cleanup in `endpoint.py`.** After the move, five imports become unreferenced in `endpoint.py` and must be removed to keep the file clean: `import time`, `EventPayload`, `InboundContext`, `gate_message`, `parse_sigil`, `scrub_surrogates`. (Note: `re`, `Callable`, `Path`, `UTC`, `datetime`, `uuid`, `Envelope`, `TextMessagePayload`, `AccessConfig` all remain — each has at least one use outside the moved methods.)

---

## Sub-requests (topologically sorted)

1. **Create `_handlers.py`** — new file at `packages/agent-core-discord/src/agent_core_discord/_handlers.py`.

   Module header and imports:

   ```python
   """Inbound-handler mixin for DiscordEndpoint.

   Move-only extraction from endpoint.py (issue #441, Step 3 of F-B6).
   Imports nothing from endpoint.py to avoid circular imports.
   """

   from __future__ import annotations

   import asyncio
   import logging
   import re
   import time
   import uuid
   from collections.abc import Callable
   from datetime import UTC, datetime
   from pathlib import Path
   from typing import Any

   from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
   from agent_core_discord._exceptions import _ToolError
   from agent_core_discord.access import InboundContext, gate_message
   from agent_core_discord.sigil import parse_sigil
   from agent_core_discord.text_sanitize import scrub_surrogates

   log = logging.getLogger(__name__)
   ```

   Then add `_redact_url_qs` (cut verbatim from `endpoint.py:226-231`):

   ```python
   def _redact_url_qs(text: str) -> str:
       """Strip query strings from any URL in a message so signed Discord CDN
       tokens (?ex=&is=&hm=) never reach logs or persisted envelope metadata.
       """
       return re.sub(r"(https?://[^\s?]+)\?\S*", r"\1?<redacted>", text)
   ```

   Then `class _HandlersMixin:` containing these eleven methods cut verbatim from `endpoint.py` in their current order:
   - `_add_listener` (endpoint.py lines 343–356)
   - `_remember_inbound_mapping` (lines 358–364)
   - `_record_inbound` (lines 366–379)
   - `_resolve_channel_id` (lines 381–419)
   - `_typing_while_pending` (lines 421–457)
   - `_make_on_message_handler` (lines 735–933)
   - `_channel_allowed` (lines 935–949)
   - `_make_on_reaction_add_handler` (lines 951–989)
   - `_resolve_user_display_name` (lines 991–1026)
   - `_make_on_raw_poll_vote_handler` (lines 1027–1080)
   - `_make_on_raw_message_lifecycle_handler` (lines 1082–1115)

2. **Update `endpoint.py`**:

   a. Add one new import after the existing `agent_core_discord._*` import block (after the `_lifecycle` import line):
      ```python
      from agent_core_discord._handlers import _HandlersMixin
      ```

   b. Change the class declaration from:
      ```python
      class DiscordEndpoint(_AcksMixin, _LifecycleMixin):
      ```
      to:
      ```python
      class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin):
      ```

   c. Remove the eleven method definitions now in `_HandlersMixin` from `endpoint.py`'s body.

   d. Remove the `_redact_url_qs` function definition from `endpoint.py`.

   e. Remove these now-unused imports from `endpoint.py`:
      - `import time` (line 20)
      - `EventPayload` from the `agent_core.bus.envelope` import (line 34)
      - `InboundContext, gate_message` from the `agent_core_discord.access` import (lines 38-41)
      - `parse_sigil` from `agent_core_discord.sigil` (line 69)
      - `scrub_surrogates` from `agent_core_discord.text_sanitize` (line 70)

3. **Verify** — run `just test-fast` and confirm exit 0 with no new failures. All tests in `test_endpoint_characterization.py`, `test_endpoint_lifecycle.py`, `test_endpoint_hardening.py`, `test_endpoint_outbound.py`, `test_resolve_channel_id.py`, `test_endpoint_inbound.py`, and all other existing tests must remain green.

---

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/_handlers.py` | **Create** — `_redact_url_qs` module helper + `_HandlersMixin` with 11 methods |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** — add `_HandlersMixin` import, update class bases, remove 11 moved methods, remove `_redact_url_qs`, remove 5 now-unused imports |

No test files are modified. No changes to `_lifecycle.py`, `_acks.py`, or `_exceptions.py`.

---

## Alternatives considered

1. **Keep `_make_on_message_handler` and friends in `endpoint.py` for a later step, move only the smaller helpers now** — incremental, reduces risk per commit. Ruled out: the issue explicitly names all eleven methods as this step's deliverable; splitting further would produce an intermediate state inconsistent with the F-B6 plan (step 3 of 6).

2. **Move `_redact_url_qs` into `_exceptions.py` or a dedicated `_utils.py` leaf** — avoids adding a non-method to `_handlers.py`. Ruled out: `_redact_url_qs` is purely a handler concern (CDN URL scrubbing for log safety); placing it in `_exceptions.py` conflates utility with error types. A one-function `_utils.py` is YAGNI at this point — move it to the module where all its callers now live.

3. **Re-export `_redact_url_qs` from `endpoint.py` after moving it** — preserves backward compatibility for any future caller that might import it from `endpoint`. Ruled out: no test or production code imports `_redact_url_qs` from `endpoint.py` (verified by grep); adding a dead re-export is noise.

---

## Open questions

None. `endpoint.py` was read in full (all 1732 lines, post-step-2 state). All test files that import from `endpoint.py` were enumerated via grep. The eleven method bodies, their module-level dependencies, and the complete import footprint of each moved method were verified line by line. The circular-import constraint was confirmed: `_handlers.py` → `_exceptions.py` (leaf), `access.py`, `sigil.py`, `text_sanitize.py` — none of those import from `endpoint.py` or `_handlers.py`.

---

## Out of scope

- Outbound tool handlers (`_send`, `_edit`, `_react`, `_fetch`, `_download_attachments`, `_list_channels`, `_get_channel_info`, `_send_briefing`, `_create_poll`, `_create_scheduled_event`, `_cancel_scheduled_event`, `_list_scheduled_events`, `_create_thread`, `_send_typing`): later F-B6 steps.
- `_resolve_channel`, `_resolve_guild`, `_persist_attachment`, `_download_url`, `_transcribe_audio`, `_transcribe_audio_sync`, `deliver`, `_deliver_text_message`, `_dispatch`, `_reply`, `_safe_filename`, `_FILENAME_ALLOWED`, `_TOOL_ALIASES`, `_canonical_tool`, `_parse_iso_datetime`, `_default_attachments_dir`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`: stay in `endpoint.py`.
- Updating the module-level docstring in `endpoint.py` to reflect the new split.
- Updating `__init__.py` to expose `_handlers.py`: it is intentionally private (leading underscore).
- Adding new characterization tests for the moved methods: the existing characterization suite already exercises all eleven handlers via `DiscordEndpoint`'s public surface; no additional test is needed for this move-only step.
