# Spec: extract `_HandlersMixin` (move-only) — F-B6.3 (issue #441)

## Goal

Move eleven event-handler methods out of `DiscordEndpoint` in `endpoint.py` into a new `_HandlersMixin` class in a new sibling file `_handlers.py`, then update `DiscordEndpoint` to inherit from it. No logic changes; method bodies are byte-identical. This is step 3 of 6 in the F-B6 decomposition series originating from issue #406. See issue [#441](https://github.com/jeffrichley/agent_core/issues/441).

## Acceptance criteria

- `packages/agent-core-discord/src/agent_core_discord/_handlers.py` is created, containing:
  - `class _HandlersMixin` with no `__init__` of its own
  - All eleven methods moved verbatim from `DiscordEndpoint`: `_add_listener`, `_channel_allowed`, `_remember_inbound_mapping`, `_record_inbound`, `_resolve_channel_id`, `_typing_while_pending`, `_resolve_user_display_name`, `_make_on_message_handler`, `_make_on_reaction_add_handler`, `_make_on_raw_poll_vote_handler`, `_make_on_raw_message_lifecycle_handler`
  - `class _ToolError(Exception)` moved from `endpoint.py` line 2287
  - Module-level `log = logging.getLogger(__name__)` — log records from the eleven methods appear under `agent_core_discord._handlers` rather than `agent_core_discord.endpoint` after the move; this logger-namespace delta is an accepted consequence of code-organization extraction
- `packages/agent-core-discord/src/agent_core_discord/endpoint.py` is updated:
  - Class definition changes from `class DiscordEndpoint:` to `class DiscordEndpoint(_HandlersMixin):`
  - The eleven methods are deleted from the class body
  - `from ._handlers import _HandlersMixin, _ToolError` is added to the import block (re-importing `_ToolError` is required because approximately 43 call-sites in the methods that remain in `endpoint.py` — outbound tools, lifecycle, sweep loops — still raise it)
- `packages/agent-core-discord/tests/test_handlers_mixin.py` is created — a characterization suite that exercises all eleven methods through a minimal concrete subclass (see Approach) without instantiating the full `DiscordEndpoint`
- `just test-fast` exits zero
- `just check` (lint + typecheck + full suite with coverage) exits zero

## Approach

No GoF pattern applies directly — this is a structural **SRP decomposition**: separating event-handler orchestration from the `DiscordEndpoint` lifecycle machinery. The Python mixin pattern (a no-`__init__` helper class added to the MRO) is the standard idiom for extracting coherent method groups without altering call sites.

**`_handlers.py` contents.** The new module needs: standard library (`asyncio`, `time`, `uuid`, `datetime`), typing helpers (`Any`, `Callable`), and the same bus/access/envelope imports that the eleven methods currently use in `endpoint.py`. The Worker must verify the exact import symbols by reading the top of `endpoint.py` (lines 12–62 contain all imports). All eleven method bodies are copied verbatim — not paraphrased, not re-structured.

**`_ToolError` migration.** `_ToolError` is currently defined at `endpoint.py` line 2287 and is used in approximately 44 locations throughout the file. One of those locations is `_resolve_channel_id` (which moves). The other ~43 are in outbound-tool methods, lifecycle helpers, and validation paths that stay in `endpoint.py`. The resolution is:
1. Move the `_ToolError` class definition to `_handlers.py`.
2. Add `from ._handlers import _HandlersMixin, _ToolError` near the top of `endpoint.py` so all remaining references continue to resolve. No circular import: `_handlers.py` does not import from `endpoint.py`.

**`_HandlersMixin` state contract.** The mixin has no `__init__`. Every attribute it touches is set by `DiscordEndpoint.__init__`. The full list: `self._client`, `self._handle`, `self._access`, `self._inbound_envelope_discord`, `self.pending_acks_max`, `self._recent_inbounds`, `self._recent_inbounds_max`, `self._recent_inbounds_timestamps`, `self._awaiting_reply_ids`, `self._awaiting_reply_ids_timestamps`, `self._user_display_name_cache`, `self.name`, `self.target`, `self._TYPING_TTL_SECONDS`. Adding a `Protocol` to type-annotate these dependencies is out of scope for a move-only step.

**Characterization suite** (`test_handlers_mixin.py`) instantiates a minimal concrete subclass that pre-populates only the attributes the mixin touches, exercising the eleven methods without requiring Discord credentials or a running bus:

```python
from collections import OrderedDict
from agent_core_discord._handlers import _HandlersMixin
from agent_core_discord.access import AccessConfig

class _MinimalHandler(_HandlersMixin):
    _TYPING_TTL_SECONDS = 90.0

    def __init__(self, client=None, handle=None, access=None):
        self._client = client
        self._handle = handle
        self._access = access or AccessConfig()
        self._inbound_envelope_discord = OrderedDict()
        self.pending_acks_max = 5000
        self._recent_inbounds = OrderedDict()
        self._recent_inbounds_max = 5000
        self._recent_inbounds_timestamps = {}
        self._awaiting_reply_ids = set()
        self._awaiting_reply_ids_timestamps = {}
        self._user_display_name_cache = {}
        self.name = "test"
        self.target = "test-agent"
```

Tests should cover at minimum: `_channel_allowed` (empty vs. non-empty `channels`), `_remember_inbound_mapping` (LRU cap), `_record_inbound` (insertion order and cap), `_resolve_channel_id` (explicit, auto-echo, and error paths), and a presence check that all eleven methods exist on `_HandlersMixin` directly (i.e., `'_add_listener' in _HandlersMixin.__dict__`). The `_make_on_message_handler` / `_make_on_reaction_add_handler` factories are already exercised heavily in `test_endpoint_inbound.py` via `DiscordEndpoint`; thin smoke tests are sufficient in the mixin suite.

## Sub-requests (topologically sorted)

1. Create `packages/agent-core-discord/src/agent_core_discord/_handlers.py`:
   - Add module-level `log = logging.getLogger(__name__)`
   - Copy `_ToolError` class definition verbatim from `endpoint.py` line 2287
   - Define `class _HandlersMixin:` with no `__init__`
   - Cut (copy then remove from source) all eleven method bodies verbatim into `_HandlersMixin`
   - Add all necessary imports (verify from `endpoint.py` lines 12–62)

2. Update `packages/agent-core-discord/src/agent_core_discord/endpoint.py`:
   - Add `from ._handlers import _HandlersMixin, _ToolError` to the import block
   - Change class line to `class DiscordEndpoint(_HandlersMixin):`
   - Delete the eleven method bodies (already moved in step 1)
   - Delete the original `_ToolError` class definition at line 2287

3. Add characterization suite `packages/agent-core-discord/tests/test_handlers_mixin.py` using `_MinimalHandler` as described in Approach; cover `_channel_allowed`, `_remember_inbound_mapping`, `_record_inbound`, `_resolve_channel_id`, and a presence check for all eleven methods

4. Create towncrier fragment `packages/agent-core-discord/changelog.d/441.changed` with text: `Internal: extracted eleven event-handler methods from \`DiscordEndpoint\` into \`_HandlersMixin\` (\`_handlers.py\`); no behaviour changes.`

5. Run `just test-fast` and fix any import or attribute errors before declaring done; then run `just check` to confirm the coverage and lint gates pass

## File-level changes

| File | Change | Description |
|---|---|---|
| `packages/agent-core-discord/src/agent_core_discord/_handlers.py` | **Create** | New module: `_HandlersMixin` with eleven methods, `_ToolError`, module logger |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** | Add `_HandlersMixin` to bases; delete eleven methods; delete `_ToolError` definition; add import of both from `._handlers` |
| `packages/agent-core-discord/tests/test_handlers_mixin.py` | **Create** | Characterization suite via `_MinimalHandler` concrete subclass |
| `packages/agent-core-discord/changelog.d/441.changed` | **Create** | Towncrier news fragment |

## Alternatives considered

- **Keep all methods in `endpoint.py` (do nothing)**: The file is already 2,294 lines and growing with each F-B6 decomposition step. Keeping all handlers inline increases merge-conflict surface and makes it harder to reason about discrete concerns. Ruled out because it does not address the decomposition goal from #406.
- **Extract to free functions in `_handlers.py` instead of a mixin**: Free functions would require threading `self` or all required attributes as explicit parameters, which means rewriting every call site in `_make_on_message_handler` and friends. This violates the "byte-identical bodies" constraint. Ruled out.
- **Move `_ToolError` to a standalone `_errors.py` module**: Clean long-term, but introduces a third file for a type used exclusively inside the Discord endpoint package. The issue scopes this to a move-only step; a dedicated errors module is appropriate if a later decomposition step reveals a second consumer. Ruled out as over-engineering for the current scope.

## Open questions

None. The issue's constraints ("move-only", "byte-identical bodies", named files, named class, `just test-fast` green) are unambiguous.

## Out of scope

- Changing any method body logic — even a one-liner simplification.
- Adding a `Protocol` or Abstract Base Class to annotate the mixin's state dependencies on `DiscordEndpoint`.
- Moving any method not in the eleven-method list (e.g., `_sweep_recent_inbounds_once`, `_track_pending_ack`, `_clear_pending_ack`, `_resolve_channel`).
- Renaming the logger namespace back to `agent_core_discord.endpoint` in `_handlers.py` — the new namespace is acceptable and consistent with the file's identity after extraction.
- Any other step of the F-B6.x series (#406 sub-issues steps 1–2 and 4–6).
