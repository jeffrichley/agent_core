# Spec: extract `_OutboundMixin` and `_ToolsMixin` from `endpoint.py` (issue #442)

## Goal

Create `packages/agent-core-discord/src/agent_core_discord/_outbound.py` containing `class _OutboundMixin` (with the core delivery/dispatch surface and outbound helpers) and `packages/agent-core-discord/src/agent_core_discord/_tools.py` containing `class _ToolsMixin` (with the 15 individual tool handlers and their module-level helpers). Both classes are extracted byte-identical from `endpoint.py`. Update `DiscordEndpoint`'s base classes to inherit from both new mixins. The existing characterization suite (`test_endpoint_characterization.py`) and `just test-fast` must pass unchanged. Addresses issue #442 (Step 4 of 6 in the F-B6 refactor, decomposed from issue #406).

---

## Acceptance criteria

- `packages/agent-core-discord/src/agent_core_discord/_outbound.py` exists and defines:
  - Module-level: `_TOOL_ALIASES`, `_canonical_tool`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`
  - `class _OutboundMixin` with exactly these nine methods (byte-identical bodies): `deliver`, `_reply`, `_deliver_text_message`, `_dispatch`, `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch`
- `packages/agent-core-discord/src/agent_core_discord/_tools.py` exists and defines:
  - Module-level: `_FILENAME_ALLOWED`, `_parse_iso_datetime`, `_safe_filename`
  - `class _ToolsMixin` with exactly these fifteen methods (byte-identical bodies): `_download_url`, `_persist_attachment`, `_download_attachments`, `_list_channels`, `_get_channel_info`, `_resolve_guild`, `_send_briefing`, `_create_poll`, `_create_scheduled_event`, `_cancel_scheduled_event`, `_list_scheduled_events`, `_create_thread`, `_send_typing`, `_transcribe_audio_sync`, `_transcribe_audio`
- `_outbound.py` imports `_ToolError` from `agent_core_discord._exceptions` (never from `endpoint.py`).
- `_tools.py` imports `_ToolError` and `_PersistError` from `agent_core_discord._exceptions` (never from `endpoint.py`).
- Neither `_outbound.py` nor `_tools.py` imports anything from `endpoint.py` (no circular imports).
- `endpoint.py`'s `DiscordEndpoint` declaration changes to `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):`.
- `endpoint.py` adds these imports: `from agent_core_discord._outbound import _OutboundMixin, _TOOL_ALIASES, _canonical_tool, _check_embeds_within_caps, _embed_char_count` and `from agent_core_discord._tools import _ToolsMixin, _parse_iso_datetime`.
- The nine moved methods (`deliver`, `_reply`, `_deliver_text_message`, `_dispatch`, `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch`) are removed from `endpoint.py`'s body.
- The fifteen moved tool-handler methods are removed from `endpoint.py`'s body.
- All moved module-level helpers (`_TOOL_ALIASES`, `_canonical_tool`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`, `_FILENAME_ALLOWED`, `_parse_iso_datetime`, `_safe_filename`) are removed from `endpoint.py`.
- The following now-unused imports are removed from `endpoint.py`: `contextlib`, `json`, `re`, `uuid`, `datetime` / `UTC` / `timedelta` (from `datetime`), `Literal` (from `typing`), `unquote` / `urlparse` (from `urllib.parse`), `ValidationError` (from `pydantic`), the entire `agent_core.bus.envelope` import block for `AcknowledgmentPayload`, `Envelope`, `TextMessagePayload`, `EndpointUnavailable` (from `agent_core.bus.protocol`), the entire `agent_core_discord.args` import block, `build_briefing_embeds` (from `agent_core_discord.briefing`), `smart_chunk_discord` (from `agent_core_discord.chunking`), the entire `send_retry` import line, both `shape_validator` import lines.
- No test file is modified.
- No changes to `_lifecycle.py`, `_acks.py`, `_handlers.py`, or `_exceptions.py`.
- `just test-fast` exits 0 with no new failures; the existing characterization suite in `test_endpoint_characterization.py` remains green.

---

## Approach

No GoF pattern fits. This is SRP-driven structural decomposition via cooperative multiple inheritance — the same pattern applied in issues #440 and #441 for `_AcksMixin`, `_LifecycleMixin`, and `_HandlersMixin`. The constraint "imports nothing from `endpoint.py`" (to avoid circular imports) governs every choice.

**Split rationale.** The issue assigns `deliver`/`_dispatch`/`_send`/`_edit`/`_react`/`_fetch`/`_reply` to `_OutboundMixin` because they implement the envelope delivery contract and the four primary Discord verbs. The fifteen deeper tool handlers (`_download_attachments`, `_list_channels`, `_resolve_guild`, `_create_poll`, etc.) go to `_ToolsMixin` because they implement individual MCP tool calls dispatched by `_dispatch`. The split is clean: `_dispatch` (in `_outbound`) calls `self._download_attachments` etc. (from `_tools`) via Python's normal MRO at runtime — no static import from one mixin to the other is needed.

**`_redact_url_qs` is NOT moved here.** The issue was written before step 3 (#441) was implemented. Step 3 moved `_redact_url_qs` to `_handlers.py` alongside `_make_on_message_handler`, its sole caller. That placement is correct; moving it again to `_tools.py` would require `_handlers.py` to import from `_tools.py`, adding unnecessary coupling. Leave it in `_handlers.py`.

**`_default_attachments_dir` stays in `endpoint.py`.** This module helper is referenced only in `DiscordEndpoint.__init__`, which remains in `endpoint.py`. Moving it would gain nothing.

**Byte-identical body invariant.** The bodies of all 24 moved methods are character-for-character identical to those in the current `endpoint.py`. They use `self.*` attributes (all owned by `DiscordEndpoint.__init__`) and module-level names that will be defined at the top of their respective new files.

**Re-export discipline.** Tests currently import `_TOOL_ALIASES`, `_canonical_tool`, `_check_embeds_within_caps`, `_embed_char_count` from `agent_core_discord.endpoint` (confirmed by reading `test_endpoint_characterization.py` line 15–19, `test_endpoint_hardening.py` lines 12 and 249). Tests also import `_parse_iso_datetime` from `agent_core_discord.endpoint` (confirmed in `test_endpoint_outbound.py` line 22). After the move, `endpoint.py` re-exports these five names from their new modules so no test file requires modification.

**`log` per module.** `_outbound.py` and `_tools.py` each define their own `log = logging.getLogger(__name__)`. Log messages from moved methods will appear under `agent_core_discord._outbound` and `agent_core_discord._tools`. This is acceptable for a move-only step.

**MRO.** The declaration `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):` adds `_OutboundMixin` and `_ToolsMixin` as the fourth and fifth bases. None of the five mixins share method names, so MRO precedence order is irrelevant in practice. `_dispatch` (in `_OutboundMixin`) calls `self._download_attachments` (resolved at runtime from `_ToolsMixin`), and `_send_briefing` (in `_ToolsMixin`) calls `self._send` (resolved from `_OutboundMixin`) — both via normal Python dispatch, no static import needed between the two new modules.

---

## Sub-requests (topologically sorted)

1. **Create `_outbound.py`** — new file at `packages/agent-core-discord/src/agent_core_discord/_outbound.py`.

   Module header and imports:
   ```python
   """Outbound delivery mixin for DiscordEndpoint.

   Move-only extraction from endpoint.py (issue #442, Step 4 of F-B6).
   Imports nothing from endpoint.py to avoid circular imports.
   """

   from __future__ import annotations

   import asyncio
   import json
   import logging
   import uuid
   from datetime import UTC, datetime
   from typing import Any, Literal

   from pydantic import ValidationError

   from agent_core.bus.envelope import AcknowledgmentPayload, Envelope, TextMessagePayload
   from agent_core.bus.protocol import EndpointUnavailable
   from agent_core_discord._exceptions import _ToolError
   from agent_core_discord.args import (
       _CancelScheduledEventArgs,
       _CreatePollArgs,
       _CreateScheduledEventArgs,
       _CreateThreadArgs,
       _DiscordSendArgs,
       _DownloadAttachmentsArgs,
       _EditArgs,
       _FetchArgs,
       _GetChannelInfoArgs,
       _ListChannelsArgs,
       _ListScheduledEventsArgs,
       _ReactArgs,
       _SendArgs,
       _SendBriefingArgs,
       _SendTypingArgs,
   )
   from agent_core_discord.chunking import smart_chunk_discord
   from agent_core_discord.send_retry import channel_send_with_retries, is_retryable_discord_send_error
   from agent_core_discord.shape_validator import Recognized, Unrecognized
   from agent_core_discord.shape_validator import validate as validate_shape

   log = logging.getLogger(__name__)
   ```

   Then cut verbatim from `endpoint.py`:
   - `_TOOL_ALIASES` (dict literal, lines 74–80 of the current file)
   - `_canonical_tool()` function (lines 83–84)
   - `_DISCORD_EMBED_TOTAL_CHAR_CAP` constant (line 112)
   - `_embed_char_count()` function (lines 115–131)
   - `_check_embeds_within_caps()` function (lines 134–140)
   - `_serialize_poll()` function (lines 143–197)
   - `class _OutboundMixin:` containing these nine methods in order: `deliver` (lines 331–439), `_deliver_text_message` (lines 441–517), `_dispatch` (lines 519–579), `_reply` (lines 581–602), `_resolve_channel` (lines 606–615), `_send` (lines 617–749), `_edit` (lines 751–785), `_react` (lines 787–800), `_fetch` (lines 804–855)

2. **Create `_tools.py`** — new file at `packages/agent-core-discord/src/agent_core_discord/_tools.py`.

   Module header and imports:
   ```python
   """Tool-handler mixin for DiscordEndpoint.

   Move-only extraction from endpoint.py (issue #442, Step 4 of F-B6).
   Imports nothing from endpoint.py to avoid circular imports.
   """

   from __future__ import annotations

   import asyncio
   import logging
   import re
   import uuid
   from datetime import UTC, datetime, timedelta
   from pathlib import Path
   from typing import Any
   from urllib.parse import unquote, urlparse

   from agent_core_discord._exceptions import _PersistError, _ToolError
   from agent_core_discord.args import (
       _CancelScheduledEventArgs,
       _CreatePollArgs,
       _CreateScheduledEventArgs,
       _CreateThreadArgs,
       _DownloadAttachmentsArgs,
       _GetChannelInfoArgs,
       _ListChannelsArgs,
       _ListScheduledEventsArgs,
       _SendArgs,
       _SendBriefingArgs,
       _SendTypingArgs,
   )
   from agent_core_discord.briefing import build_briefing_embeds
   from agent_core_discord.send_retry import channel_send_with_retries

   log = logging.getLogger(__name__)
   ```

   Then cut verbatim from `endpoint.py`:
   - `_parse_iso_datetime()` function (lines 87–99)
   - `_FILENAME_ALLOWED` regex (line 107)
   - `_safe_filename()` function (lines 200–216)
   - `class _ToolsMixin:` containing these fifteen methods in order: `_download_url` (lines 857–871), `_persist_attachment` (lines 873–900), `_download_attachments` (lines 902–929), `_list_channels` (lines 931–952), `_get_channel_info` (lines 954–969), `_resolve_guild` (lines 971–991), `_send_briefing` (lines 993–1008), `_create_poll` (lines 1010–1028), `_create_scheduled_event` (lines 1030–1071), `_cancel_scheduled_event` (lines 1073–1100), `_list_scheduled_events` (lines 1102–1121), `_create_thread` (lines 1123–1152), `_send_typing` (lines 1154–1177), `_transcribe_audio_sync` (lines 1179–1208), `_transcribe_audio` (lines 1210–1218)

3. **Update `endpoint.py`**:

   a. Add these two import lines after the existing `_lifecycle` import:
      ```python
      from agent_core_discord._outbound import (
          _OutboundMixin,
          _TOOL_ALIASES,
          _canonical_tool,
          _check_embeds_within_caps,
          _embed_char_count,
      )
      from agent_core_discord._tools import _ToolsMixin, _parse_iso_datetime
      ```

   b. Change the class declaration from:
      ```python
      class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin):
      ```
      to:
      ```python
      class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):
      ```

   c. Remove from `endpoint.py`:
      - All nine methods now in `_OutboundMixin` (`deliver`, `_deliver_text_message`, `_dispatch`, `_reply`, `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch`)
      - All fifteen methods now in `_ToolsMixin`
      - All moved module-level helpers/constants: `_TOOL_ALIASES`, `_canonical_tool`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`, `_parse_iso_datetime`, `_FILENAME_ALLOWED`, `_safe_filename`
      - The `# Pepper-facing tool names...` comment block that preceded `_TOOL_ALIASES`
      - The `# --- Endpoint Protocol ---` comment and the `# --- Outbound tool handlers...` comment

   d. Remove these now-unused imports (keeping `asyncio`, `logging`, `from collections import OrderedDict`, `from collections.abc import Callable`, `from pathlib import Path`, `from typing import TYPE_CHECKING, Any`, `from agent_core_discord.access import AccessConfig`, and all existing `_*` mixin imports):
      - `import contextlib` (was unused since step 2)
      - `import json`
      - `import re`
      - `import uuid`
      - `from datetime import UTC, datetime, timedelta`
      - `Literal` from the `from typing import ...` line (keep `TYPE_CHECKING, Any`)
      - `from urllib.parse import unquote, urlparse`
      - `from pydantic import ValidationError`
      - `from agent_core.bus.envelope import (AcknowledgmentPayload, Envelope, TextMessagePayload,)` — entire block
      - `from agent_core.bus.protocol import EndpointUnavailable`
      - The entire `from agent_core_discord.args import (...)` block (all 16 arg models)
      - `from agent_core_discord.briefing import build_briefing_embeds`
      - `from agent_core_discord.chunking import smart_chunk_discord`
      - `from agent_core_discord.send_retry import channel_send_with_retries, is_retryable_discord_send_error`
      - `from agent_core_discord.shape_validator import (Recognized, Unrecognized,)`
      - `from agent_core_discord.shape_validator import (validate as validate_shape,)`

4. **Verify** — run `just test-fast` and confirm exit 0 with no new failures. All tests in `test_endpoint_characterization.py`, `test_endpoint_hardening.py`, `test_endpoint_outbound.py`, `test_endpoint_lifecycle.py`, `test_endpoint_inbound.py`, `test_resolve_channel_id.py`, and all other existing tests must remain green.

---

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/_outbound.py` | **Create** — 6 module-level helpers/constants + `_OutboundMixin` with 9 methods |
| `packages/agent-core-discord/src/agent_core_discord/_tools.py` | **Create** — 3 module-level helpers/constants + `_ToolsMixin` with 15 methods |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** — add `_OutboundMixin`/`_ToolsMixin` imports + 5 re-exports, update class bases, remove 24 moved methods, remove 9 moved module helpers, remove ~16 now-unused import lines |

No test files are modified. No changes to `_lifecycle.py`, `_acks.py`, `_handlers.py`, or `_exceptions.py`.

---

## Alternatives considered

1. **Merge all 24 methods into a single `_OutboundMixin`** — one mixin instead of two, simpler base class declaration. Ruled out: the issue explicitly names two separate modules with separate responsibilities; combining them defeats the SRP goal and the issue's step decomposition. A 1,200-line mixin is not better than two 600-line ones.

2. **Put `_dispatch` in `_tools.py` instead of `_outbound.py`** — `_dispatch` routes to tool handlers, so conceptually it sits at the boundary. Ruled out: the issue explicitly assigns `_dispatch` to `_outbound.py`. More importantly, `_dispatch` is the envelope delivery sub-path; it pairs naturally with `deliver`, `_reply`, and `_deliver_text_message` which also live in `_outbound.py`.

3. **Move `_redact_url_qs` from `_handlers.py` to `_tools.py`** to match the issue's original description. Ruled out: `_redact_url_qs` was correctly moved to `_handlers.py` in step 3 (#441) alongside its only caller `_make_on_message_handler`. Moving it a second time to `_tools.py` would require `_handlers.py` to import from `_tools.py`, adding unnecessary coupling. Leave it where it belongs.

---

## Open questions

None. `endpoint.py` (post step-3 state) was read in full (all 1,218 lines). All test files that import from `endpoint.py` were enumerated and their specific imports verified (grep on the test directory). The five names that tests import from `endpoint.py` and that will move (`_TOOL_ALIASES`, `_canonical_tool`, `_check_embeds_within_caps`, `_embed_char_count`, `_parse_iso_datetime`) were confirmed by reading `test_endpoint_characterization.py` lines 15–19, `test_endpoint_hardening.py` lines 12 and 249, and `test_endpoint_outbound.py` line 22. Circular-import safety was verified: neither `_outbound.py` nor `_tools.py` imports from `endpoint.py`, `_lifecycle.py`, `_acks.py`, or `_handlers.py`.

---

## Out of scope

- `DiscordEndpoint.__init__` and `_default_attachments_dir`: stay in `endpoint.py` (not part of any mixin).
- `_TYPING_TTL_SECONDS` class attribute: stays in `endpoint.py` (class-level constant on `DiscordEndpoint`, referenced via `self._TYPING_TTL_SECONDS` from `_handlers.py`).
- `_redact_url_qs`: already in `_handlers.py` (step 3); do not move.
- Updating the module-level docstring in `endpoint.py` to reflect the new split.
- Updating `__init__.py` to expose `_outbound.py` or `_tools.py`: they are intentionally private (leading underscore).
- Adding new characterization tests: the existing suite already exercises all 24 moved methods via `DiscordEndpoint`'s public surface.
- The remaining F-B6 steps 5 and 6 (per issue #406).
