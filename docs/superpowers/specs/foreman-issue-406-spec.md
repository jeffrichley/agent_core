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
- The eight `except asyncio.CancelledError: pass` guards in `start()` and `stop()` are handled per the direction confirmed by the issue author before sub-request 10 is implemented (see Open questions): either (a) annotated with an inline comment marking intentional post-cancel cleanup, or (b) each `pass` replaced with a `logger.debug()` or `logger.warning()` call per the explicit B6 issue text "(log them)".
- `just check` passes throughout (lint + typecheck + tests + coverage).

## Approach

No GoF pattern fits. This is Python-idiomatic SRP decomposition using cooperative multiple inheritance (the "mixin" pattern): each cohesion axis becomes a private mixin class; `DiscordEndpoint` inherits from all five. The concrete class provides all instance attributes in `__init__`; each mixin declares the subset it accesses as class-level annotations so `mypy --strict` can verify the contract without circular imports or a separate Protocol.

The three-phase discipline from Decision D2 in the Track B spec:

1. **Characterize first**: write golden-master tests against the existing monolith. Every test must be green before the first move commit. This gives the split a safety net.
2. **Move-only commits**: create the mixin files, move method bodies verbatim, update `DiscordEndpoint` to inherit. No logic change in these commits — even a one-line if-condition rewrite in the same commit is a violation.
3. **Type + swallow commits**: add `mypy --strict` annotations and document the CancelledError guards in separate commits from the moves.

**Why mixins over function extraction**: the natural alternative (extract `start_endpoint(ep, bus)` module-level functions, make class methods one-line delegators) would require importing `DiscordEndpoint` into each helper module, creating circular imports. Mixins avoid the circle: helper modules import nothing from `endpoint.py`; `endpoint.py` imports the mixins.

**Shared exceptions live in `_exceptions.py`**: `_ToolError` and `_PersistError` are raised and caught by the outbound/tool mixin methods, but the anti-circular rule forbids the mixin modules from importing `endpoint.py`. Defining the exceptions in `endpoint.py` would therefore be unusable from the mixins (a mixin importing `endpoint.py` reintroduces the exact circular import the mixin split avoids). The exceptions are moved into a thin, leaf `_exceptions.py` that imports nothing from any other module in the package. `endpoint.py` imports them from `_exceptions.py` and re-exports them unchanged, so any external code doing `from ...endpoint import _ToolError` keeps working; the mixins import them directly from `_exceptions.py`.

**Why mixin-level attribute declarations**: `mypy --strict` cannot verify `self.name` in `_LifecycleMixin.start()` without knowing what `self` provides. The mixin declares `name: str` at class scope; `DiscordEndpoint.__init__` assigns `self.name = name`. mypy sees both and resolves the type without a Protocol or `TYPE_CHECKING` import of the concrete class.

**Module-level helper placement**: helpers move with their primary consumer:
- `_TOOL_ALIASES`, `_canonical_tool`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`, `_DISCORD_EMBED_TOTAL_CHAR_CAP` → `_outbound.py` (used only by outbound dispatch methods)
- `_parse_iso_datetime`, `_safe_filename`, `_FILENAME_ALLOWED` → `_tools.py` (used only by tool implementations)
- `_redact_url_qs` → `_handlers.py` (used exclusively inside `_make_on_message_handler()` at lines 1166 and 1206 of `endpoint.py`; that method is assigned to `_HandlersMixin`)
- `_default_attachments_dir`, `_active_endpoints` → `endpoint.py` (used in `__init__`)

**The 8 swallows**: all eight are `except asyncio.CancelledError: pass` clauses that immediately follow an explicit `task.cancel()` + `await task` pair in `start()` rollback and `stop()`. Logging a CancelledError here is noise — the task was cancelled on purpose.

**Conflict with issue parenthetical — pending issue-author confirmation**: The issue body says "(4) fold in discord's 8 `except:pass` swallows (log them)." The Planner proposes comment-only treatment by analogy to D7's handling of intentional guards (`psutil.NoSuchProcess`), reasoning that each guard immediately follows an explicit `task.cancel()` + `await task` and the CancelledError is the expected outcome. However, D7 itself does not classify the discord swallows as intentional — it only says "Discord's 8 swallows are handled inside B6." That classification is the Planner's inference, not an explicit D7 ruling. Before sub-request 10 is implemented, the issue author must confirm the direction. If the issue author confirms comment-only treatment, update this paragraph to record the approval explicitly and proceed with sub-request 10 option (a). If the issue author confirms logging, proceed with sub-request 10 option (b). See Open questions.

## Sub-requests (topologically sorted)

1. **Write characterization tests** — add `packages/agent-core-discord/tests/test_endpoint_characterization.py` pinning:
   - `_TOOL_ALIASES` table: assert each alias maps to the correct canonical key
   - Parametrized tool routing: for each of the 15 dispatch keys (send, edit, react, fetch, discord_send, download_attachments, list_channels, get_channel_info, send_briefing, create_poll, create_scheduled_event, cancel_scheduled_event, list_scheduled_events, create_thread, send_typing) a ToolInvocation envelope reaches the correct internal handler and returns the expected `status` key in the JSON Acknowledgment note
   - Alias routing: send_discord_message → send, edit_message → edit, add_reaction → react, fetch_messages → fetch reach their handlers identically to the canonical keys
   - Not-started shape: `deliver()` on a non-started endpoint raises `EndpointUnavailable` with `f"discord '{ep.name}' not started"` as the message
   - Acknowledgment emission shape: a successful tool dispatch emits exactly one Acknowledgment with `kind="Acknowledgment"`, `correlation_id` matching the inbound envelope's `correlation_id`, `in_reply_to` matching the inbound envelope's `id`, `to` matching the inbound envelope's `from_`, `payload.of` matching the inbound envelope's `id`
   - Urgency mapping: a successful dispatch emits `urgency="green"`; a `_ToolError` dispatch emits `urgency="yellow"`
   - Lifecycle transitions: (a) `start()` with the fake client sets `self._handle` to the provided bus and registers the endpoint in `_active_endpoints[ep.name]`; (b) `stop()` after a successful start removes the endpoint from `_active_endpoints` and clears `self._handle` so a subsequent `deliver()` raises `EndpointUnavailable`. Both cases use the existing `_client_factory` seam; no real Discord connection needed.
   - Run `just test-fast` and confirm all new tests pass against the existing unmodified `endpoint.py`

2. **Create `_exceptions.py`** — create `packages/agent-core-discord/src/agent_core_discord/_exceptions.py` and move the `_ToolError` and `_PersistError` class definitions into it verbatim. This module imports nothing from any other `agent_core_discord` module, so `endpoint.py` and every mixin can import the exceptions from it without a circular import. Move-only commit.

3. **Create `_acks.py`** — move `_track_pending_ack`, `_remote_remove_ack`, `_clear_pending_ack` from `DiscordEndpoint` into a new `class _AcksMixin` in `packages/agent-core-discord/src/agent_core_discord/_acks.py`. Method bodies are byte-identical. Move-only commit.

4. **Create `_lifecycle.py`** — move `start`, `stop`, `_pending_acks_sweep_loop`, `_attachment_sweep_loop`, `_access_config_reload_loop`, `_sweep_pending_acks_once`, `_sweep_attachments_once`, `_sweep_recent_inbounds_once` into `class _LifecycleMixin` in `_lifecycle.py`. Move-only commit.

5. **Create `_handlers.py`** — move module-level helper `_redact_url_qs` and methods `_add_listener`, `_channel_allowed`, `_remember_inbound_mapping`, `_record_inbound`, `_resolve_channel_id`, `_typing_while_pending`, `_resolve_user_display_name`, `_make_on_message_handler`, `_make_on_reaction_add_handler`, `_make_on_raw_poll_vote_handler`, `_make_on_raw_message_lifecycle_handler` into `class _HandlersMixin` in `_handlers.py`. (`_redact_url_qs` moves here because it is called at lines 1166 and 1206 of `_make_on_message_handler()` — nowhere else in the file.) Move-only commit.

6. **Create `_outbound.py`** — move module-level helpers (`_TOOL_ALIASES`, `_canonical_tool`, `_DISCORD_EMBED_TOTAL_CHAR_CAP`, `_embed_char_count`, `_check_embeds_within_caps`, `_serialize_poll`) and methods `deliver`, `_reply`, `_deliver_text_message`, `_dispatch`, `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch` into `class _OutboundMixin` in `_outbound.py`. Import any of `_ToolError`/`_PersistError` this module raises or catches from `_exceptions.py` (`from ._exceptions import _ToolError`), never from `endpoint.py`. Move-only commit.

7. **Create `_tools.py`** — move module-level helpers (`_parse_iso_datetime`, `_safe_filename`, `_FILENAME_ALLOWED`) and methods `_download_url`, `_persist_attachment`, `_download_attachments`, `_list_channels`, `_get_channel_info`, `_resolve_guild`, `_send_briefing`, `_create_poll`, `_create_scheduled_event`, `_cancel_scheduled_event`, `_list_scheduled_events`, `_create_thread`, `_send_typing`, `_transcribe_audio_sync`, `_transcribe_audio` into `class _ToolsMixin` in `_tools.py`. Import any of `_ToolError`/`_PersistError` this module raises or catches from `_exceptions.py`, never from `endpoint.py`. Move-only commit.

8. **Update `endpoint.py`** — reduce to: all imports, `_active_endpoints`, `_default_attachments_dir`, the mixin imports, `class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin)` with only `__init__`, and a re-export block for backward compatibility:
   - `from ._exceptions import _ToolError, _PersistError` — external code importing `endpoint._ToolError`/`endpoint._PersistError` keeps working (exceptions are **not** defined here)
   - `from ._tools import _parse_iso_datetime` — `test_endpoint_outbound.py:22` imports this at top-level
   - `from ._outbound import _check_embeds_within_caps, _embed_char_count` — `test_endpoint_hardening.py:12` imports `_check_embeds_within_caps` at top-level; `test_endpoint_hardening.py:249` imports `_embed_char_count` locally

   Run `grep -r 'from agent_core_discord.endpoint import' packages/agent-core-discord/tests/` before committing to confirm no other private helpers are stranded; add any additional re-exports found. Move-only commit (can be bundled with sub-requests 3–7 as one commit or a separate follow-up commit). Verify `just test-fast` passes unchanged.

9. **Add `mypy --strict` annotations** — in each mixin file, declare class-level attribute annotations for all `self.X` accesses the mixin methods make (so mypy can verify without importing `DiscordEndpoint`). Add full parameter + return type annotations to every method. Fix any errors surfaced by `uv run mypy --strict packages/agent-core-discord/src`. In the root `pyproject.toml`, add `"packages/agent-core-discord/src"` to the `files` list under `[tool.mypy]`, and add:
   ```toml
   [[tool.mypy.overrides]]
   module = ["agent_core_discord.*"]
   strict = true
   warn_unused_ignores = true
   ```
   Run `just check` to confirm the gate stays green.

10. **Handle the 8 CancelledError guards** — **prerequisite: get issue-author confirmation before implementing this sub-request** (see Open questions). In `_lifecycle.py` (after sub-request 4), in each of the 8 `except asyncio.CancelledError: pass` blocks, implement the issue-author-confirmed direction:

    - **(a) Comment-only path (if issue author confirms D7 analogy)**: add the inline comment `# Intentional: explicitly cancelled above; swallowing CancelledError is correct cleanup.` Do **not** add any `logger` call. Before committing, update Approach § Conflict with issue parenthetical to record the approval explicitly.
    - **(b) Logging path (if issue author confirms B6 text "(log them)")**: replace `pass` with `logger.debug("CancelledError swallowed after explicit task.cancel()", exc_info=True)` (or `logger.warning` if the issue author specifies that level), using the module-level logger already present in `_lifecycle.py`.

    This is a separate commit from the move commits (comment-only or logging-only; no move, no type change).

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** — gut to ~220 lines: imports, `_active_endpoints`, `_default_attachments_dir`, mixin imports, `DiscordEndpoint` with `__init__` only; re-exports for backward compatibility: `_ToolError`/`_PersistError` from `_exceptions.py`, `_parse_iso_datetime` from `_tools.py`, `_check_embeds_within_caps`/`_embed_char_count` from `_outbound.py` (none of these are defined here) |
| `packages/agent-core-discord/src/agent_core_discord/_exceptions.py` | **Create** — defines `_ToolError` and `_PersistError` (moved verbatim from `endpoint.py`); imports nothing from any other `agent_core_discord` module, so both `endpoint.py` and the mixin modules import the exceptions from here without a circular import (~15 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_acks.py` | **Create** — `_AcksMixin` with `_track_pending_ack`, `_remote_remove_ack`, `_clear_pending_ack` (~150 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_lifecycle.py` | **Create** — `_LifecycleMixin` with `start`, `stop`, 3 background loops, 3 sweep helpers (~480 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_handlers.py` | **Create** — `_HandlersMixin` with `_add_listener`, inbound state helpers, 4 event handler factories, channel allow, user display name, typing (~480 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_outbound.py` | **Create** — `_OutboundMixin` with `deliver`, `_reply`, `_deliver_text_message`, `_dispatch`, `_resolve_channel`, `_send`, `_edit`, `_react`, `_fetch` + module-level dispatch helpers (~550 lines) |
| `packages/agent-core-discord/src/agent_core_discord/_tools.py` | **Create** — `_ToolsMixin` with download/persist/transcribe, all remaining tool implementations + module-level helpers (~430 lines) |
| `packages/agent-core-discord/tests/test_endpoint_characterization.py` | **Create** — golden-master characterization suite: routing table, alias resolution, not-started shape, ack emission shape, urgency mapping, lifecycle transitions (start registers in `_active_endpoints`; stop drains it and clears `_handle`) |
| `pyproject.toml` | **Modify** — add `"packages/agent-core-discord/src"` to `[tool.mypy] files`; add `[[tool.mypy.overrides]]` block with `strict = true` for `agent_core_discord.*` |

## Alternatives considered

1. **Function extraction instead of mixins** — extract `start_endpoint(ep: DiscordEndpoint, bus)` module-level functions, keep class methods as one-line delegators. Ruled out: requires importing `DiscordEndpoint` into every helper module, creating circular imports (`_lifecycle.py` → `endpoint.py` → `_lifecycle.py`). Mixins avoid the circle because the helpers import nothing from `endpoint.py`.

2. **Composition instead of mixins** — extract `_AckManager`, `_AttachmentStore`, etc. as standalone objects instantiated inside `__init__` (`self._acks = _AckManager(self)`). Cleaner conceptually but requires threading `self` or specific attributes into each sub-object constructor, changing the calling convention throughout (`self._clear_pending_ack()` → `self._acks.clear()`), and rewriting ~80 call sites — violating the "move-only" commit constraint.

3. **Split class across sub-packages** (`agent_core_discord.lifecycle`, `.handlers`, etc.)  — more discoverable but doesn't change the file-count problem and creates longer import paths for private internals. The existing project style keeps private implementation details in `_`-prefixed modules within the same package; this split follows that convention.

## Open questions

**1. CancelledError swallow treatment (blocks sub-request 10)**: The B6 issue text explicitly says "(4) fold in discord's 8 `except:pass` swallows (log them)". This spec proposes comment-only treatment by analogy to D7's handling of intentional guards (`psutil.NoSuchProcess`), but D7 does not explicitly classify the discord swallows — that is the Planner's inference. The Worker must ask the issue author before implementing sub-request 10: should the 8 `except asyncio.CancelledError: pass` guards be (a) annotated with comments only (per the D7 `psutil.NoSuchProcess` analogy) or (b) logged (per the explicit B6 issue text)? If the issue author confirms (a), update Approach § Conflict with issue parenthetical to record the approval before committing.

## Out of scope

- Behavioral changes to any tool implementation, inbound handler, or lifecycle method — all commits touching the existing logic are move-only or comment-only.
- Public surface changes: `DiscordEndpoint.__init__` signature, method names, the `_active_endpoints` module-level registry, `_ToolError`/`_PersistError` locations relative to importers.
- Adding new tools or event handlers (separate tickets).
- The `claude_code_mcp.py` god-module (ticket B7).
- The non-discord `except: pass` swallows (ticket B8).
- mypy for the other 10 packages (ticket B5; should run after B4 to avoid churning audit code).
