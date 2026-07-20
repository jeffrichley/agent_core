# Spec: extract `_exceptions`, `_acks`, `_lifecycle` mixins from `endpoint.py` (issue #440)

## Goal

Split three groups of methods out of `DiscordEndpoint` in
`packages/agent-core-discord/src/agent_core_discord/endpoint.py` into three new
sibling modules using cooperative multiple inheritance. All bodies move
byte-identical; no logic changes. The Step-1 characterization suite
(`test_endpoint_characterization.py`) and `just test-fast` must pass
unchanged after the move. Addresses issue #440 (Step 2 of 6 in the F-B6
refactor, decomposed from issue #406).

---

## Acceptance criteria

- `_exceptions.py` exists in `packages/agent-core-discord/src/agent_core_discord/`
  and defines `_ToolError` and `_PersistError` with byte-identical bodies.
  It imports nothing from other `agent_core_discord` modules.
- `_acks.py` exists in the same directory and defines `class _AcksMixin` with
  three methods: `_track_pending_ack`, `_remote_remove_ack`,
  `_clear_pending_ack`. Method bodies are byte-identical to the originals in
  `endpoint.py`.
- `_lifecycle.py` exists and defines `_active_endpoints: dict[str, Any] = {}` at
  module level, plus `class _LifecycleMixin` with eight methods: `start`, `stop`,
  `_pending_acks_sweep_loop`, `_attachment_sweep_loop`,
  `_access_config_reload_loop`, `_sweep_pending_acks_once`,
  `_sweep_attachments_once`, `_sweep_recent_inbounds_once`. Method bodies are
  byte-identical.
- `endpoint.py`'s `DiscordEndpoint` declaration changes to
  `class DiscordEndpoint(_AcksMixin, _LifecycleMixin):`.
- `endpoint.py` re-exports `_ToolError`, `_PersistError`, and `_active_endpoints`
  so that existing test imports (`from agent_core_discord.endpoint import
  _ToolError`, `from agent_core_discord.endpoint import _active_endpoints`) continue
  to work without modification.
- No test file is modified.
- `just test-fast` exits 0 with no new failures.

---

## Approach

No GoF pattern fits. This is SRP-driven structural decomposition — giving each
module one clearly named responsibility — using Python 3's cooperative multiple
inheritance (cooperative MI). The constraint from the issue is that helper modules
import **nothing from `endpoint.py`** to avoid circular imports; `endpoint.py`
imports the mixins, not the other way around.

**Circular-import strategy.** The only thing the moved methods need from each
other's modules is:
1. `_ToolError` / `_PersistError` — resolved by `_exceptions.py`, a leaf with zero
   package imports. `_acks.py` and `_lifecycle.py` can import from it freely since
   `_exceptions.py` doesn't import them.
2. `_active_endpoints` registry — must live in `_lifecycle.py` (not `endpoint.py`)
   because `start()` and `stop()` reference it as a module global; Python resolves
   free-variable globals against the module where the function is **defined**, not
   where the class is used. `endpoint.py` re-imports the dict object from
   `_lifecycle.py` so the single live registry is shared.

**Re-export discipline.** Tests import `_ToolError`, `_PersistError`, and
`_active_endpoints` directly from `agent_core_discord.endpoint`. After the move,
`endpoint.py` adds `from agent_core_discord._exceptions import _ToolError,
_PersistError` and `from agent_core_discord._lifecycle import _active_endpoints,
_LifecycleMixin` (along with `_AcksMixin` from `_acks.py`). These re-exports
preserve all external import paths without touching test files.

**`_AcksMixin` and `_LifecycleMixin` carry no `__init__`** — all instance-state
setup stays in `DiscordEndpoint.__init__`. The mixins are thin: they contain only
the methods listed in the issue, each method using `self.*` attributes that are
guaranteed to exist on the concrete class.

**`log` per module.** Each new module defines its own
`log = logging.getLogger(__name__)`. This is idiomatic Python and means log
messages from `_LifecycleMixin.start()` will show logger name
`agent_core_discord._lifecycle` rather than `agent_core_discord.endpoint`.  This is
acceptable for a move-only step (a later step could unify if desired).

**Method body invariant.** The bodies of all moved methods are character-for-
character identical to those in the current `endpoint.py`. No renaming, no logic
changes, no new parameters. Reviewers can verify with `git diff` after replacing
the removed code with a `pass` marker then diffing against the new files.

**MRO.** `class DiscordEndpoint(_AcksMixin, _LifecycleMixin):` — `_AcksMixin`
first, `_LifecycleMixin` second. The two mixins share no method names, so
precedence order is irrelevant in practice, but placing the smaller/simpler mixin
first is conventional.

---

## Sub-requests (topologically sorted)

1. **Create `_exceptions.py`** — new file with `_ToolError` and `_PersistError`
   cut verbatim from the end of `endpoint.py` (lines 2287–2292 in the current file).
   Module header, `from __future__ import annotations`, then the two class bodies.
   No other imports.

2. **Create `_acks.py`** — new file. Import `contextlib`, `logging`, `time`.
   Define `log = logging.getLogger(__name__)`. Define `class _AcksMixin:` with
   three methods cut verbatim from `endpoint.py`:
   - `_track_pending_ack` (current lines 1471–1486)
   - `_remote_remove_ack` (current lines 1488–1506)
   - `_clear_pending_ack` (current lines 1667–1682)

3. **Create `_lifecycle.py`** — new file. Imports at top:
   ```python
   from __future__ import annotations

   import asyncio
   import json
   import logging
   import time
   from typing import TYPE_CHECKING, Any

   from agent_core_credentials.secrets import SecretNotFoundError
   from agent_core_credentials.secrets import get as get_secret
   from agent_core_discord.access import AccessConfig, _build_access_config, load_access_config

   if TYPE_CHECKING:
       from agent_core.bus.handle import BusHandle

   log = logging.getLogger(__name__)
   _active_endpoints: dict[str, Any] = {}
   ```
   Then `class _LifecycleMixin:` with eight methods cut verbatim from `endpoint.py`:
   - `_sweep_recent_inbounds_once` (current lines 386–402)
   - `_sweep_pending_acks_once` (current lines 1508–1531)
   - `_sweep_attachments_once` (current lines 1545–1606)
   - `_pending_acks_sweep_loop` (current lines 1533–1543)
   - `_attachment_sweep_loop` (current lines 1652–1665)
   - `_access_config_reload_loop` (current lines 1608–1650)
   - `start` (current lines 484–722)
   - `stop` (current lines 997–1072)

4. **Update `endpoint.py`**:
   a. Add three new imports (after the existing `agent_core_discord.*` imports):
      ```python
      from agent_core_discord._acks import _AcksMixin
      from agent_core_discord._exceptions import _PersistError, _ToolError
      from agent_core_discord._lifecycle import _LifecycleMixin, _active_endpoints
      ```
   b. Remove the module-level definition:
      ```python
      _active_endpoints: dict[str, DiscordEndpoint] = {}
      ```
      (now imported from `_lifecycle`)
   c. Change the class declaration:
      ```python
      class DiscordEndpoint:
      ```
      →
      ```python
      class DiscordEndpoint(_AcksMixin, _LifecycleMixin):
      ```
   d. Remove from `endpoint.py`'s body the nine method definitions now in the
      mixins (`_sweep_recent_inbounds_once`, `_track_pending_ack`,
      `_remote_remove_ack`, `_sweep_pending_acks_once`, `_pending_acks_sweep_loop`,
      `_sweep_attachments_once`, `_access_config_reload_loop`,
      `_attachment_sweep_loop`, `_clear_pending_ack`, `start`, `stop`).
   e. Remove the two class definitions at the end of the file (`_ToolError`,
      `_PersistError`).
   f. Clean up imports that are now exclusively used by the moved methods and no
      longer needed in `endpoint.py`: remove `load_access_config` and
      `_build_access_config` from the `agent_core_discord.access` import (keep
      `AccessConfig`, `InboundContext`, `gate_message` which are still used in
      `__init__` and inbound handlers). Remove `SecretNotFoundError` and
      `get as get_secret` from `agent_core_credentials.secrets` imports (only used
      in `start()`). Keep all other existing imports.

5. **Verify** — run `just test-fast` and confirm exit 0 with no new failures.
   All tests in `test_endpoint_characterization.py`, `test_endpoint_lifecycle.py`,
   `test_endpoint_hardening.py`, `test_endpoint_outbound.py`, and
   `test_resolve_channel_id.py` must remain green. These tests import `_ToolError`,
   `_active_endpoints`, etc. from `agent_core_discord.endpoint` — those re-exports
   must exist and must resolve to the same objects as before.

---

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/_exceptions.py` | **Create** — `_ToolError` and `_PersistError`, no package imports |
| `packages/agent-core-discord/src/agent_core_discord/_acks.py` | **Create** — `_AcksMixin` with 3 methods |
| `packages/agent-core-discord/src/agent_core_discord/_lifecycle.py` | **Create** — `_active_endpoints` dict + `_LifecycleMixin` with 8 methods |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** — add mixin imports/re-exports, change class declaration, remove moved code |

No test files are modified.

---

## Alternatives considered

1. **Keep `_active_endpoints` in `endpoint.py` and pass it as a class attribute** —
   `_LifecycleMixin` would read it via `self.__class__._active_endpoints` or via an
   abstract property. Ruled out: adds indirection that the issue explicitly avoids
   ("byte-identical bodies"); the method bodies reference the bare name
   `_active_endpoints` as a module global, which Python resolves against the
   defining module's `__globals__`. Moving the dict to `_lifecycle.py` is the
   minimal change that preserves byte-identical bodies while avoiding circular
   imports.

2. **Extract a `_registry.py` leaf** — put `_active_endpoints` in its own file,
   imported by both `_lifecycle.py` and `endpoint.py`. Ruled out: adds an extra
   file for a one-line definition. Issue #440 is explicitly scoped to three files
   (`_exceptions.py`, `_acks.py`, `_lifecycle.py`). Introducing a fourth module
   is scope creep not requested in the issue; the two-line re-export in
   `endpoint.py` achieves the same result.

3. **Move only `_exceptions.py`; leave acks and lifecycle in `endpoint.py` for
   a future step** — incrementally safer, but the issue is explicitly "Step 2 of 6"
   and names all three extractions as the deliverable. Splitting further would
   create an intermediate state that is inconsistent with the F-B6 plan.

---

## Open questions

None. `endpoint.py` was read in full (all 2295 lines). All test files that
import from `endpoint.py` were enumerated via grep. The circular-import
constraint and `_active_endpoints` global-resolution semantics were verified.
The method assignments in the issue were cross-checked against the line
numbers in the actual file.

---

## Out of scope

- Inbound event handler factories (`_make_on_message_handler`,
  `_make_on_reaction_add_handler`, `_make_on_raw_poll_vote_handler`,
  `_make_on_raw_message_lifecycle_handler`): listed as later steps in the F-B6
  plan; do not move in this step.
- Outbound tool handlers (`_send`, `_edit`, `_react`, `_fetch`,
  `_download_attachments`, `_list_channels`, `_get_channel_info`,
  `_send_briefing`, `_create_poll`, `_create_scheduled_event`,
  `_cancel_scheduled_event`, `_list_scheduled_events`, `_create_thread`,
  `_send_typing`): later F-B6 steps.
- `_resolve_channel_id`, `_resolve_channel`, `_resolve_guild`,
  `_record_inbound`, `_remember_inbound_mapping`, `_typing_while_pending`,
  `_resolve_user_display_name`, `_transcribe_audio`, `_transcribe_audio_sync`,
  `_channel_allowed`, `_add_listener`, `deliver`, `_deliver_text_message`,
  `_dispatch`, `_reply`: stay in `endpoint.py` (not in this issue's scope).
- Updating the module docstring in `endpoint.py` to reflect the new location of
  `_active_endpoints` (low-value cosmetic change; can be done in a follow-up).
- Updating `__init__.py` to expose the new modules: they are intentionally
  private (leading underscore) and not part of the public API.
- Logging namespace change (methods moved to `_lifecycle.py` will log under
  `agent_core_discord._lifecycle` rather than `agent_core_discord.endpoint`):
  acceptable for a move-only step; no test asserts on logger names.
