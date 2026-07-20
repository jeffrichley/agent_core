# Spec: split `discord/endpoint.py` into cohesive modules (issue #406)

## Goal

Split `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (2295 lines, untyped) into five mixin modules behind a byte-identical `DiscordEndpoint` public surface. First lay down a characterization test suite that pins current observable behavior; then carve in move-only commits; then add `mypy --strict` annotations per module; finally document the eight intentional `except asyncio.CancelledError: pass` guards. Implements ticket B6 from the Theme F Track B spec (`docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md`).

## Acceptance criteria

- No carved module exceeds ~550 lines (the 2295-line original is the baseline; each resulting module represents one cohesion axis).
- `packages/agent-core-discord/src/agent_core_discord/__init__.py` is unchanged: `from agent_core_discord.endpoint import DiscordEndpoint` + `__all__ = ["DiscordEndpoint"]`.
- `DiscordEndpoint.__init__` signature is byte-identical: same positional/keyword parameters, same defaults.
- `DiscordEndpoint.start`, `.stop`, `.deliver`, `.name`, `.target`, `.token_env`, `.outbound_channel_id` are all still present and callable on the concrete class.
- All downstream import paths that already work (e.g. `from agent_core_discord.endpoint import DiscordEndpoint, _active_endpoints`) remain valid.
- A new `packages/agent-core-discord/tests/test_endpoint_characterization.py` file passes before and after the split, serving as the permanent regression suite.
- `uv run mypy --strict packages/agent-core-discord/src` exits 0 after the annotation commit.
- `packages/agent-core-discord/src` is added to `[tool.mypy] files` in the root `pyproject.toml`, and a `[[tool.mypy.overrides]]` block applies `strict = true` to `agent_core_discord.*`.
- The eight `except asyncio.CancelledError: pass` guards in `start()` and `stop()` each have an inline comment explaining they are intentional post-cancel cleanup; no new logging is added for them.
- `just check` passes throughout (lint + typecheck + tests + coverage).

## Approach

No GoF pattern fits. This is Python-idiomatic SRP decomposition using cooperative multiple inheritance (the "mixin" pattern): each cohesion axis becomes a private mixin class; `DiscordEndpoint` inherits from all five. The concrete class provides all instance attributes in `__init__`; each mixin declares the subset it accesses as class-level annotations so `mypy --strict` can verify the contract without circular imports or a separate Protocol.

The three-phase discipline from Decision D2 in the Track B spec:

1. **Characterize first**: write golden-master tests against the existing monolith. Every test must be green before the first move commit. This gives the split a safety net.
2. **Move-only commits**: create the mixin files, move method bodies verbatim, update `DiscordEndpoint` to inherit. No logic change in these commits — even a one-line if-condition rewrite in the same commit is a violation.
3. **Type + swallow commits**: add `mypy --strict` annotations and document the CancelledError guards in separate commits from the moves.

**Why mixins over function extraction**: the natural alternative (extract `start_endpoint(ep, bus)` module-level functions, make class methods one-line delegators) would require importing `DiscordEndpoint` into each helper module, creating circular imports. Mixins avoid the circle: helper modules import nothing from `endpoint.py`; `endpoint.py` imports the mixins.

**Why mixin-level attribute declarations**: `mypy --strict` cannot verify `self.name` in `_LifecycleMixin.start()` without knowing what `self` provides. The mixin declares `name: str` at class scope; `DiscordEndpoint.__init__` assigns `self.name = name`. mypy sees both and resolves the type without a Protocol or `TYPE_CHECKING` import of the concrete class.

**Module-level helper placement**: helpers move with their primary consumer:
- `_TOOL_ALIASES`, `_canonical_tool`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`, `_DISCORD_EMBED_TOTAL_CHAR_CAP` → `_outbound.py` (used only by outbound dispatch methods)
- `_parse_iso_datetime`, `_safe_filename`, `_redact_url_qs`, `_FILENAME_ALLOWED` → `_tools.py` (used only by tool implementations)
- `_default_attachments_dir`, `_active_endpoints` → `endpoint.py` (used in `__init__`)

**The 8 swallows**: all eight are `except asyncio.CancelledError: pass` clauses that immediately follow an explicit `task.cancel()` + `await task` pair in `start()` rollback and `stop()`. Logging a CancelledError here is noise — the task was cancelled on purpose. Per D7: annotate as intentional, do not add log calls.

## Sub-requests (topologically sorted)

1. **Write characterization tests** — add `packages/agent-core-discord/tests/test_endpoint_characterization.py` pinning:
   - `_TOOL_ALIASES` table: assert each alias maps to the correct canonical key
   - Parametrized tool routing: for each of the 15 dispatch keys (send, edit, react, fetch, discord_send, download_attachments, list_channels, get_channel_info, send_briefing, create_poll, create_scheduled_event, cancel_scheduled_event, list_scheduled_events, create_thread, send_typing) a ToolInvocation envelope reaches the correct internal handler and returns the expected `status` key in the JSON Acknowledgment note
   - Alias routing: send_discord_message → send, edit_message → edit, add_reaction → react, fetch_messages → fetch reach their handlers identically to the canonical keys
   - Not-started shape: `deliver()` on a non-started endpoint raises `EndpointUnavailable` with `f"discord '{ep.name}' not started"` as the message
   - Acknowledgment emission shape: a successful tool dispatch emits exactly one Acknowledgment with `kind="Acknowledgment"`, `correlation_id` matching the inbound envelope's `correlation_id`, `in_reply_to` matching the inbound envelope's `id`, `to` matching the inbound envelope's `from_`, `payload.of` matching the inbound envelope's `id`
   - Urgency mapping: a successful dispatch emits `urgency="green"`; a `_ToolError` dispatch emits `urgency="yellow"`
   - Run `just test-fast` and confirm all new tests pass against the existing unmodified `endpoint.py`

2. **Create `_acks.py`** — move `_track_pending_ack`, `_remote_remove_ack`, `_clear_pending_ack` from `DiscordEndpoint` into a new `class _AcksMixin` in `packages/agent-core-discord/src/agent_core_discord/_acks.py`. Method bodies are byte-identical. Move-only commit.

3. **Create `_lifecycle.py`** — move `start`, `stop`, `_pending_acks_sweep_loop`, `_attachment_sweep_loop`, `_access_config_reload_loop`, `_sweep_pending_acks_once`, `_sweep_attachments_once`, `_sweep_recent_inbounds_once` into `class _LifecycleMixin` in `_lifecycle.py`. Move-only commit.

4. **Create `_handlers.py`** — move `_add_listener`, `_channel_allowed`, `_remember_inbound_mapping`, `_record_inbound`, `_resolve_channel_id`, `_typing_while_pending`, `_resolve_user_display_name`, `_make_on_message_handler`, `_make_on_reaction_add_handler`, `_make_on_raw_poll_vote_handler`, `_make_on_raw_message_lifecycle_handler` into `class _HandlersMixin` in `_handlers.py`. Move-only commit.

5. **Create `_outbound.py`** — move module-level helpers (`_TOOL_ALIASES`, `_canonical_tool`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`) and methods `deliver`, `_reply`, `_deliver_text_message`, `_dispatch`, `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch` into `class _OutboundMixin` in `_outbound.py`. Move-only commit.

6. **Create `_tools.py`** — move module-level helpers (`_parse_iso_datetime`, `_safe_filename`, `_redact_url_qs`, `_FILENAME_ALLOWED`) and methods `_download_url`, `_persist_attachment`, `_download_attachments`, `_list_channels`, `_get_channel_info`, `_resolve_guild`, `_send_briefing`, `_create_poll`, `_create_scheduled_event`, `_cancel_scheduled_event`, `_list_scheduled_events`, `_create_thread`, `_send_typing`, `_transcribe_audio_sync`, `_transcribe_audio` into `class _ToolsMixin` in `_tools.py`. Move-only commit.

7. **Update `endpoint.py`** — reduce to: all imports, `_active_endpoints`, `_default_attachments_dir`, the mixin imports, `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin)` with only `__init__`, and `_ToolError`/`_PersistError`. Move-only commit (can be bundled with sub-requests 2–6 as one commit or a separate follow-up commit). Verify `just test-fast` passes unchanged.

8. **Add `mypy --strict` annotations** — in each mixin file, declare class-level attribute annotations for all `self.X` accesses the mixin methods make (so mypy can verify without importing `DiscordEndpoint`). Add full parameter + return type annotations to every method. Fix any errors surfaced by `uv run mypy --strict packages/agent-core-discord/src`. In the root `pyproject.toml`, add `"packages/agent-core-discord/src"` to the `files` list under `[tool.mypy]`, and add:
   ```toml
   [[tool.mypy.overrides]]
   module = ["agent_core_discord.*"]
   strict = true
   warn_unused_ignores = true
   ```
   Run `just check` to confirm the gate stays green.

9. **Document the 8 CancelledError guards** — in `_lifecycle.py` (after sub-request 3), in each of the 8 `except asyncio.CancelledError: pass` blocks, add the inline comment: `# Intentional: explicitly cancelled above; swallowing CancelledError is correct cleanup.` This is a logic-touch commit (no move, no type change) and must be separate from the move commits.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** — gut to ~220 lines: imports, `_active_endpoints`, `_default_attachments_dir`, mixin imports, `DiscordEndpoint` with `__init__` only, `_ToolError`, `_PersistError` |
| `packages/agent-core-discord/src/agent_core_discord/_acks.py` | **Create** — `_AcksMixin` with `_track_pending_ack`, `_remote_remove_ack`, `_clear_pending_ack` (~150 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_lifecycle.py` | **Create** — `_LifecycleMixin` with `start`, `stop`, 3 background loops, 3 sweep helpers (~480 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_handlers.py` | **Create** — `_HandlersMixin` with `_add_listener`, inbound state helpers, 4 event handler factories, channel allow, user display name, typing (~480 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_outbound.py` | **Create** — `_OutboundMixin` with `deliver`, `_reply`, `_deliver_text_message`, `_dispatch`, `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch` + module-level dispatch helpers (~550 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_tools.py` | **Create** — `_ToolsMixin` with download/persist/transcribe, all remaining tool implementations + module-level helpers (~430 lines) |
| `packages/agent-core-discord/tests/test_endpoint_characterization.py` | **Create** — golden-master characterization suite: routing table, alias resolution, not-started shape, ack emission shape, urgency mapping |
| `pyproject.toml` | **Modify** — add `"packages/agent-core-discord/src"` to `[tool.mypy] files`; add `[[tool.mypy.overrides]]` block with `strict = true` for `agent_core_discord.*` |

## Alternatives considered

1. **Function extraction instead of mixins** — extract `start_endpoint(ep: DiscordEndpoint, bus)` module-level functions, keep class methods as one-line delegators. Ruled out: requires importing `DiscordEndpoint` into every helper module, creating circular imports (`_lifecycle.py` → `endpoint.py` → `_lifecycle.py`). Mixins avoid the circle because the helpers import nothing from `endpoint.py`.

2. **Composition instead of mixins** — extract `_AckManager`, `_AttachmentStore`, etc. as standalone objects instantiated inside `__init__` (`self._acks = _AckManager(self)`). Cleaner conceptually but requires threading `self` or specific attributes into each sub-object constructor, changing the calling convention throughout (`self._clear_pending_ack()` → `self._acks.clear()`), and rewriting ~80 call sites — violating the "move-only" commit constraint.

3. **Split class across sub-packages** (`agent_core_discord.lifecycle`, `.handlers`, etc.)  — more discoverable but doesn't change the file-count problem and creates longer import paths for private internals. The existing project style keeps private implementation details in `_`-prefixed modules within the same package; this split follows that convention.

## Open questions

None. The approach, module layout, mypy strategy, and commit discipline are all resolved by the Track B design decisions (D2, D4, D7) and the existing repo conventions.

## Out of scope

- Behavioral changes to any tool implementation, inbound handler, or lifecycle method — all commits touching the existing logic are move-only or comment-only.
- Public surface changes: `DiscordEndpoint.__init__` signature, method names, the `_active_endpoints` module-level registry, `_ToolError`/`_PersistError` locations relative to importers.
- Adding new tools or event handlers (separate tickets).
- The `claude_code_mcp.py` god-module (ticket B7).
- The non-discord `except: pass` swallows (ticket B8).
- mypy for the other 10 packages (ticket B5; should run after B4 to avoid churning audit code).
