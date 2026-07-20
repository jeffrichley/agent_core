# Spec: extract `_OutboundMixin` and `_ToolsMixin` from `endpoint.py` (issue #442)

## Goal

Split the two largest responsibility clusters out of `DiscordEndpoint` in
`packages/agent-core-discord/src/agent_core_discord/endpoint.py` into two new
sibling modules — `_outbound.py` and `_tools.py` — following the mixin pattern
established by this series (decomposed from #406). A third module, `_exceptions.py`,
is created as an infrastructure sub-step to give both mixins a stable import
target for `_ToolError` / `_PersistError` (currently stranded at the bottom of
`endpoint.py`). All method bodies are moved verbatim ("byte-identical bodies").
`DiscordEndpoint` gains the two mixin bases; `endpoint.py` re-exports every
moved symbol so existing test-level imports (`from agent_core_discord.endpoint
import _ToolError`, `_parse_iso_datetime`, `_check_embeds_within_caps`,
`_embed_char_count`) continue to resolve unchanged.

## Acceptance criteria

- `packages/agent-core-discord/src/agent_core_discord/_exceptions.py` exists and
  defines `_ToolError` and `_PersistError` (moved verbatim from `endpoint.py` lines
  2287–2295).
- `packages/agent-core-discord/src/agent_core_discord/_outbound.py` exists and
  defines `class _OutboundMixin` containing:
  - Module-level: `_TOOL_ALIASES`, `_canonical_tool`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`,
    `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`.
  - Methods: `deliver`, `_reply`, `_deliver_text_message`, `_dispatch`,
    `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch`.
  - Imports `_ToolError` from `._exceptions` (not from `endpoint`).
- `packages/agent-core-discord/src/agent_core_discord/_tools.py` exists and
  defines `class _ToolsMixin` containing:
  - Module-level: `_parse_iso_datetime`, `_FILENAME_ALLOWED`, `_safe_filename`,
    `_redact_url_qs`.
  - Methods: `_download_url`, `_persist_attachment`, `_download_attachments`,
    `_list_channels`, `_get_channel_info`, `_resolve_guild`, `_send_briefing`,
    `_create_poll`, `_create_scheduled_event`, `_cancel_scheduled_event`,
    `_list_scheduled_events`, `_create_thread`, `_send_typing`,
    `_transcribe_audio_sync`, `_transcribe_audio`.
  - Imports `_ToolError` and `_PersistError` from `._exceptions`.
- `DiscordEndpoint` inherits from `(_OutboundMixin, _ToolsMixin)` in addition to
  retaining its `__init__`, lifecycle methods, inbound event handlers, and pending-ack
  bookkeeping (see "Out of scope").
- `endpoint.py` re-exports `_ToolError`, `_PersistError`, `_parse_iso_datetime`,
  `_check_embeds_within_caps`, and `_embed_char_count` so that existing test-level
  imports resolve unchanged (verified below).
- `just test-fast` exits 0 after all changes.

## Approach

No GoF pattern fits this change. The closest engineering principle is SRP (Single
Responsibility Principle): the outbound mixin owns "send/edit/react/fetch/deliver/dispatch,"
the tools mixin owns "download/persist/transcribe/schedule/poll/channel-info." Neither
mixin introduces new behaviour; they are mechanical moves.

**Why mixins instead of composition.** `DiscordEndpoint`'s inbound handlers
(`_make_on_message_handler`, etc.) call tools methods (`_persist_attachment`,
`_transcribe_audio`) and outbound methods (`_reply`, `_send`) via `self`. A
composition approach would require explicit delegation (`self._tools._persist_attachment`)
and either a two-argument constructor or a back-reference. Mixin inheritance preserves
all existing `self.method()` call sites unchanged — no functional diff needed in the
remaining class body.

**Why `_exceptions.py` is created in this step.** Both mixin files reference
`_ToolError` and `_PersistError`. Neither `_outbound.py` nor `_tools.py` can sensibly
own the exception classes (they'd form an import cycle with each other), and leaving
them in `endpoint.py` would create a circular import because `endpoint.py` will import
from `_outbound` and `_tools`, which would import from `endpoint`. The only clean
target is a dedicated `_exceptions.py` with no intra-package imports.

**Why `endpoint.py` must re-export moved symbols.** Inspection of the test suite
reveals direct imports from `endpoint.py`:
- `from agent_core_discord.endpoint import _ToolError` — in `test_endpoint_outbound.py`
  (three inline-import sites), `test_endpoint_hardening.py`, `test_resolve_channel_id.py`.
- `from agent_core_discord.endpoint import _parse_iso_datetime` — in
  `test_endpoint_outbound.py` line 22.
- `from agent_core_discord.endpoint import _check_embeds_within_caps` — in
  `test_endpoint_hardening.py` line 12.
- `from agent_core_discord.endpoint import _embed_char_count` — in
  `test_endpoint_hardening.py` line 249.

Modifying test files is out of scope for this move-only step. The fix is to add
re-export lines to `endpoint.py` so these imports keep resolving.

**Import split summary.** Each new module brings its own runtime imports for everything
it uses. Both use `from __future__ import annotations` (matching `endpoint.py`). The
`TYPE_CHECKING` guard for `BusHandle` is only needed in `_outbound.py` (where `deliver`
uses it in its annotation). `_tools.py` has no `TYPE_CHECKING` guard.

## Sub-requests (topologically sorted)

1. **Create `_exceptions.py`** — move `_ToolError` and `_PersistError` verbatim from
   `endpoint.py` lines 2287–2295 into a new file:

   Path: `packages/agent-core-discord/src/agent_core_discord/_exceptions.py`

   ```python
   """Exceptions shared across DiscordEndpoint mixin modules."""


   class _ToolError(Exception):
       """User-error during tool dispatch — produces an Acknowledgment with note."""


   class _PersistError(Exception):
       """Attachment could not be persisted (unsafe path, etc.). Neutral —
       shared by the MCP tool and the inbound path; the tool layer
       translates it to _ToolError."""
   ```

2. **Create `_outbound.py`** — move the outbound helpers and mixin class verbatim.

   Path: `packages/agent-core-discord/src/agent_core_discord/_outbound.py`

   File preamble + imports:

   ```python
   """_OutboundMixin — outbound tool dispatch surface for DiscordEndpoint."""

   from __future__ import annotations

   import asyncio
   import json
   import logging
   import uuid
   from datetime import UTC, datetime
   from typing import TYPE_CHECKING, Any, Literal

   from pydantic import ValidationError

   from agent_core.bus.envelope import (
       AcknowledgmentPayload,
       Envelope,
       TextMessagePayload,
   )
   from agent_core.bus.protocol import EndpointUnavailable
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
   from agent_core_discord.shape_validator import (
       Recognized,
       Unrecognized,
   )
   from agent_core_discord.shape_validator import validate as validate_shape
   from agent_core_discord._exceptions import _ToolError

   if TYPE_CHECKING:
       from agent_core.bus.handle import BusHandle

   log = logging.getLogger(__name__)
   ```

   Then the module-level symbols moved verbatim from `endpoint.py` (lines 82–210):
   - `_TOOL_ALIASES` dict (lines 82–88)
   - `_canonical_tool` function (lines 91–92)
   - `_DISCORD_EMBED_TOTAL_CHAR_CAP` constant (line 125)
   - `_embed_char_count` function (lines 128–144)
   - `_check_embeds_within_caps` function (lines 147–153)
   - `_serialize_poll` function (lines 156–210)

   Then `class _OutboundMixin:` with these methods moved verbatim from
   `DiscordEndpoint` in `endpoint.py`:
   - `deliver` (lines 724–832)
   - `_deliver_text_message` (lines 834–910)
   - `_dispatch` (lines 912–972)
   - `_reply` (lines 974–995)
   - `_resolve_channel` (lines 1460–1468)
   - `_send` (lines 1684–1816)
   - `_edit` (lines 1818–1852)
   - `_react` (lines 1854–1867)
   - `_fetch` (lines 1871–1922)

3. **Create `_tools.py`** — move the tools helpers and mixin class verbatim.

   Path: `packages/agent-core-discord/src/agent_core_discord/_tools.py`

   File preamble + imports:

   ```python
   """_ToolsMixin — attachment, guild-query, and media tool surface for DiscordEndpoint."""

   from __future__ import annotations

   import asyncio
   import logging
   import re
   import uuid
   from datetime import UTC, datetime, timedelta
   from pathlib import Path
   from typing import Any
   from urllib.parse import unquote, urlparse

   from agent_core_discord.args import (
       _CancelScheduledEventArgs,
       _CreatePollArgs,
       _CreateScheduledEventArgs,
       _CreateThreadArgs,
       _DownloadAttachmentsArgs,
       _GetChannelInfoArgs,
       _ListChannelsArgs,
       _ListScheduledEventsArgs,
       _SendBriefingArgs,
       _SendArgs,
       _SendTypingArgs,
   )
   from agent_core_discord.briefing import build_briefing_embeds
   from agent_core_discord.send_retry import channel_send_with_retries
   from agent_core_discord._exceptions import _ToolError, _PersistError

   log = logging.getLogger(__name__)
   ```

   Then the module-level symbols moved verbatim from `endpoint.py`:
   - `_parse_iso_datetime` function (lines 95–107)
   - `_FILENAME_ALLOWED` regex (line 120)
   - `_safe_filename` function (lines 213–229)
   - `_redact_url_qs` function (lines 232–236)

   Then `class _ToolsMixin:` with these methods moved verbatim from
   `DiscordEndpoint` in `endpoint.py`:
   - `_download_url` (lines 1924–1938)
   - `_persist_attachment` (lines 1940–1967)
   - `_download_attachments` (lines 1969–1996)
   - `_list_channels` (lines 1998–2019)
   - `_get_channel_info` (lines 2021–2036)
   - `_resolve_guild` (lines 2038–2058)
   - `_send_briefing` (lines 2060–2075)
   - `_create_poll` (lines 2077–2095)
   - `_create_scheduled_event` (lines 2097–2138)
   - `_cancel_scheduled_event` (lines 2140–2167)
   - `_list_scheduled_events` (lines 2169–2188)
   - `_create_thread` (lines 2190–2219)
   - `_send_typing` (lines 2221–2244)
   - `_transcribe_audio_sync` (lines 2246–2275)
   - `_transcribe_audio` (lines 2277–2284)

4. **Update `endpoint.py`** — prune moved code, add mixin bases, add re-exports.

   a. **Replace the top-level import block** — remove imports that are now only
      needed by `_outbound.py` or `_tools.py`. Retain all imports still used by
      remaining methods (`_make_on_message_handler`, `start`, `stop`, inbound
      handlers, pending-ack methods, etc.). Add re-imports from the new modules:

      ```python
      # Re-exports: tests import these names from endpoint directly.
      from agent_core_discord._exceptions import _PersistError, _ToolError
      from agent_core_discord._outbound import _OutboundMixin, _check_embeds_within_caps, _embed_char_count
      from agent_core_discord._tools import _ToolsMixin, _parse_iso_datetime, _redact_url_qs
      ```

      `_redact_url_qs` is also used at runtime inside `_make_on_message_handler`
      (endpoint.py lines 1166 and 1207) — the re-import satisfies both the
      runtime usage and the test-import requirement.

   b. **Remove moved module-level symbols** from `endpoint.py`:
      - `_TOOL_ALIASES`, `_canonical_tool` (lines 82–92)
      - `_parse_iso_datetime` (lines 95–107)
      - `_FILENAME_ALLOWED` (line 120)
      - `_DISCORD_EMBED_TOTAL_CHAR_CAP`, `_embed_char_count`,
        `_check_embeds_within_caps`, `_serialize_poll` (lines 125–210)
      - `_safe_filename`, `_redact_url_qs` (lines 213–236)
      - `_ToolError`, `_PersistError` (lines 2287–2295 — now the last two classes)

   c. **Change `DiscordEndpoint` class declaration**:

      ```python
      class DiscordEndpoint(_OutboundMixin, _ToolsMixin):
      ```

   d. **Remove the moved methods** from `DiscordEndpoint`'s body — delete the
      bodies of `deliver`, `_deliver_text_message`, `_dispatch`, `_reply`,
      `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch`, `_download_url`,
      `_persist_attachment`, `_download_attachments`, `_list_channels`,
      `_get_channel_info`, `_resolve_guild`, `_send_briefing`, `_create_poll`,
      `_create_scheduled_event`, `_cancel_scheduled_event`,
      `_list_scheduled_events`, `_create_thread`, `_send_typing`,
      `_transcribe_audio_sync`, `_transcribe_audio`.

   e. **Trim now-unused imports** from `endpoint.py`'s import block. After removal
      of the above methods, the following imports are no longer needed directly
      in `endpoint.py` and should be removed (they live in `_outbound.py` or
      `_tools.py` now):
      - `AcknowledgmentPayload` (used only in `_reply`)
      - `EndpointUnavailable` from `agent_core.bus.protocol` (used only in
        `deliver` and `_send`)
      - `smart_chunk_discord` (used only in `_send`)
      - `channel_send_with_retries`, `is_retryable_discord_send_error` (used
        only in `deliver`, `_send`, `_create_poll`)
      - `Recognized`, `Unrecognized`, `validate_shape` (used only in `deliver`)
      - `ValidationError` (used only in `_dispatch`)
      - All the arg models (`_SendArgs`, `_EditArgs`, `_ReactArgs`, etc.) —
        used only in `_dispatch` / moved methods
      - `build_briefing_embeds` (used only in `_send_briefing`)
      - `unquote`, `urlparse` (used only in `_safe_filename`)

      Imports that REMAIN in `endpoint.py` (still used by remaining code):
      - `asyncio`, `contextlib`, `json`, `logging`, `re`, `time`, `uuid`
      - `collections.OrderedDict`
      - `datetime.UTC`, `datetime`, `timedelta`
      - `pathlib.Path`
      - `typing.TYPE_CHECKING`, `Any`, `Literal`, `Callable`
      - `pydantic.ValidationError` — check: only used in `_dispatch` (moved),
        so remove.
      - `agent_core.bus.envelope.Envelope`, `EventPayload`, `TextMessagePayload`
        (used by inbound handlers) — KEEP these three
      - `agent_core_credentials.secrets.SecretNotFoundError`, `get as get_secret`
        (used in `start`) — KEEP
      - `agent_core_discord.access.*` (used by inbound handlers and start) — KEEP
      - `agent_core_discord.briefing.build_briefing_embeds` — check: only used
        in `_send_briefing` (moved to `_tools`), so REMOVE
      - `agent_core_discord.sigil.parse_sigil` (used in `_make_on_message_handler`) — KEEP
      - `agent_core_discord.text_sanitize.scrub_surrogates` (used in
        `_make_on_message_handler`) — KEEP
      - `agent_core_discord.shape_validator.*` — only used in `deliver` (moved),
        so REMOVE
      - `agent_core_discord.chunking.smart_chunk_discord` — only in `_send` (moved),
        so REMOVE

5. **Verify** — run the test suite:

   ```bash
   just test-fast
   ```

   Expected: all tests pass. The move-only constraint means no test should need
   modification.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/_exceptions.py` | **Create** — `_ToolError` + `_PersistError`, moved verbatim from `endpoint.py` lines 2287–2295 |
| `packages/agent-core-discord/src/agent_core_discord/_outbound.py` | **Create** — `_OutboundMixin` class + 6 module-level helpers, moved verbatim |
| `packages/agent-core-discord/src/agent_core_discord/_tools.py` | **Create** — `_ToolsMixin` class + 4 module-level helpers, moved verbatim |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** — remove 24 moved methods + 10 moved module-level symbols; add 3 mixin bases; add re-export imports; prune dead imports |

No test files change. No other source files change.

## Alternatives considered

1. **Move `_ToolError`/`_PersistError` into `_outbound.py` and import them from
   there in `_tools.py`** — avoids creating a fourth file. Ruled out: creates a
   coupling where `_tools.py` depends on `_outbound.py` for exception types that
   are logically shared infrastructure. If the mixin split is later refactored,
   the exception dependency would force a particular ordering. A neutral
   `_exceptions.py` with no intra-package deps is cleaner and matches how other
   multi-mixin codebases handle shared exceptions.

2. **Keep `_ToolError`/`_PersistError` in `endpoint.py` and have the new
   mixin files import them from `endpoint`** — simpler, no new file. Ruled out:
   `endpoint.py` imports from `_outbound.py` and `_tools.py` (for the mixin
   classes). If both mixin files also import from `endpoint.py`, Python's import
   system creates a circular dependency: `endpoint → _outbound → endpoint`. This
   would produce an `ImportError` at module load time.

3. **Move all 24 methods into a single `_mixin.py` file** — one file instead of
   two. Ruled out: the issue is explicit about the two-mixin split (`_outbound.py`
   for message-delivery verbs, `_tools.py` for attachment/guild/media tools). The
   two responsibility clusters are already clearly separated by the issue author;
   merging them defeats the SRP rationale.

## Open questions

None. The `endpoint.py` source was read in full; all moved symbols, their line
numbers, and their inter-dependencies are confirmed. The test import paths were
verified via grep. The circular-import constraint that drives the `_exceptions.py`
sub-step is deterministic.

## Out of scope

- Modifying any test file. The re-export pattern in `endpoint.py` preserves all
  existing test-level import paths unchanged.
- Moving inbound handlers (`_make_on_message_handler`, `_make_on_reaction_add_handler`,
  `_make_on_raw_poll_vote_handler`, `_make_on_raw_message_lifecycle_handler`) — those
  are a separate extraction step in the #406 series.
- Moving lifecycle methods (`start`, `stop`, `_pending_acks_sweep_loop`,
  `_attachment_sweep_loop`, `_access_config_reload_loop`) — separate step.
- Moving bookkeeping methods (`_track_pending_ack`, `_remote_remove_ack`,
  `_sweep_pending_acks_once`, `_clear_pending_ack`, `_sweep_attachments_once`,
  `_record_inbound`, `_remember_inbound_mapping`, `_sweep_recent_inbounds_once`,
  `_resolve_channel_id`, `_typing_while_pending`, `_channel_allowed`,
  `_resolve_user_display_name`) — separate step.
- Adding new tests. The existing characterization suite (all passing tests under
  `packages/agent-core-discord/tests/`) is the regression gate.
- Any behavioural or API change. This is a pure code-organisation move.
