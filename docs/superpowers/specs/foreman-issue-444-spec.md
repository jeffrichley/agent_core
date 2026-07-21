# Spec: mypy --strict for agent-core-discord + log the 8 CancelledError swallows (issue #444)

## Goal

Enable `mypy --strict` enforcement for `packages/agent-core-discord/src` by adding the package to the root `[tool.mypy]` files list, wiring a `strict = true` per-module override, fixing all annotation gaps across the package's source files, and adding a debug-level log call to each of the 8 `except asyncio.CancelledError: pass` guards in `endpoint.py`'s lifecycle methods (`start()` rollback and `stop()`). `just check` must be green on the resulting branch. See issue #444.

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/agent-core-discord/src"`.
- `pyproject.toml` has `[[tool.mypy.overrides]]` with `module = ["discord", "discord.*"]` and `ignore_missing_imports = true` (discord.py publishes no stubs).
- `pyproject.toml` has `[[tool.mypy.overrides]]` with `module = ["agent_core_discord.*"]` and `strict = true`.
- `uv run mypy` exits 0 (no errors).
- All 8 `except asyncio.CancelledError: pass` guards in `endpoint.py` have been replaced with `except asyncio.CancelledError: log.debug(...)` at debug level; none remain as bare `pass`.
- The CancelledError changes are in a **separate commit** from the annotation/config changes ("no moves" commit — only adds `log.debug(...)` calls, no restructuring).
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern fits here. This is a typing discipline closure ("make the right thing easy": strict mypy makes type regressions impossible to introduce silently) plus an observability fix (debug logs make expected-but-opaque cancellations visible in verbose output without being noisy in production).

**Why `[[tool.mypy.overrides]]` rather than top-level `strict = true`.** The root `[tool.mypy]` section currently covers `packages/core/src` and `packages/agent-core-channel/src` with a lighter set of flags (`warn_unused_ignores`, `no_implicit_optional`, `check_untyped_defs`). Enabling `strict = true` at the top level would force the same strictness on those packages immediately, which is a separate decision. The overrides table is the correct scoping mechanism: add one entry for `agent_core_discord.*` and one to silence the missing-stubs noise from `discord.*`.

**Status of `_lifecycle.py`.** Issue #444 references a file `_lifecycle.py` that does not exist in the current tree. The issue was decomposed from #406 as "step 6 of 6"; the earlier steps were expected to extract lifecycle methods from `endpoint.py` into mixin files. Since those steps have not yet landed, this spec targets `endpoint.py` directly. The 8 `except asyncio.CancelledError: pass` guards referenced by the issue are all in `endpoint.py`: 4 in the `start()` rollback branch (lines ≈663, 674, 685, 697) and 4 in `stop()` (lines ≈1022, 1033, 1044, 1056). **If a future mixin extraction moves these guards to a new file, the CancelledError logging travels with the code automatically.** The Worker should check whether mixin files have been created in the branch they are working from and adjust file references accordingly.

**On class-level attribute annotations.** The issue instructs "in each mixin declare class-level attribute annotations for every `self.X` the mixin accesses." Since there are no mixins in the current tree, this technique is not needed. `DiscordEndpoint.__init__` assigns all instance attributes, and mypy infers their types from the assignments without explicit class-variable declarations. If mypy `--strict` flags any attribute access as `Cannot access attribute … for class "DiscordEndpoint"`, add an explicit class-level annotation using the inferred type.

**Principal annotation gaps in `endpoint.py`.** The file is large (2295 lines) and most methods are well-annotated. The known gaps are:

1. Handler factory methods lack return types: `_make_on_message_handler`, `_make_on_reaction_add_handler`, `_make_on_raw_poll_vote_handler`, `_make_on_raw_message_lifecycle_handler`. All return an `async def` coroutine function; the return type is `Callable[[Any], Coroutine[Any, Any, None]]` (import from `collections.abc`). `_make_on_reaction_add_handler` takes two args so its callable shape is `Callable[[Any, Any], Coroutine[Any, Any, None]]`.
2. `_resolve_channel(self, channel_id: str)` — missing `-> Any` (returns a discord channel object with no stubs).
3. `_deliver_text_message(self, envelope: Envelope) -> dict` — `dict` must become `dict[str, Any]`.
4. `_dispatch(self, tool: str, args: dict, env: Envelope)` — `args: dict` → `args: dict[str, Any]`; return is already `-> Any`.
5. Inside `_dispatch`: nested `_v(model: Any, raw: dict) -> Any` and `_inject_channel_id(raw: dict) -> dict` both need parameterized types: `raw: dict[str, Any]` and `-> dict[str, Any]`.
6. Tool handler return types — all currently `-> dict` or `-> list[dict]`; replace with `-> dict[str, Any]` and `-> list[dict[str, Any]]` respectively. Affected: `_send`, `_edit`, `_react`, `_fetch`, `_download_attachments`, `_list_channels`, `_get_channel_info`, `_send_briefing`, `_create_poll`, `_create_scheduled_event`, `_cancel_scheduled_event`, `_create_thread`, `_send_typing`. (`_list_scheduled_events` already returns `-> list[dict[str, Any]]`.)
7. `_done` callback inside `_send_typing`: `def _done(t: asyncio.Task) -> None` — needs `asyncio.Task[Any]`.
8. Run `uv run mypy` after the bulk of fixes; additional issues may surface in rarely-checked corners.

**Principal annotation gaps in `testing/fakes.py`.**

- `FakeAttachment.__init__` — missing `-> None`.
- `FakeMessage.__init__` — missing `-> None`; `author` parameter missing type `author: Any = None`.
- `FakeChannel.__init__` — missing `-> None`.
- `FakeChannel.typing(self)` — missing `-> Any`; inner `_T.__aenter__` and `__aexit__` need self-typed annotations and `-> None`.
- `FakeChannel.history(self, limit: int = 50, before: Any = None)` — missing `-> AsyncGenerator[FakeMessage, None]` (import `from collections.abc import AsyncGenerator`).
- `FakeGuild.__init__` — missing `-> None`.
- `FakeUser.__init__` — missing `-> None`.
- `FakeDiscordClient.__init__` — missing `-> None`.
- `FakeDiscordClient._handlers: dict[str, Callable]` — needs `dict[str, Callable[..., Any]]`.
- `FakeDiscordClient.event(fn: Callable)` and `add_listener(fn: Callable, ...)` — `Callable` needs `Callable[..., Any]`.
- `FakeDiscordClient.guilds` property — missing `-> list[FakeGuild]`.
- `FakeDiscordClient.fire(self, event_name: str, *args)` — `*args` needs `*args: Any`.
- `FakeBusHandle.publish/ack/nack(self, *a, **kw)` — `*a: Any`, `**kw: Any`, `-> Any`.
- `FakeBusHandle.endpoints(self)` — missing `-> list[Any]`.
- `FakeBusHandle.spawn(self, coro, *, name=None)` — `coro: Any`, `name: str | None = None`, `-> asyncio.Task[Any]`.

**The 8 CancelledError swallows to be logged.** Every guard follows the pattern `await <task>` then `except asyncio.CancelledError: pass`. Replace `pass` with a `log.debug(...)` line naming the task and the lifecycle phase:

| Location | Task | Lifecycle phase | Suggested message |
|---|---|---|---|
| `start()` rollback, line ≈663 | `_sweep_task` | start rollback | `"discord(%s): sweep task cancelled during start rollback", self.name` |
| `start()` rollback, line ≈674 | `_attachment_sweep_task` | start rollback | `"discord(%s): attachment sweep task cancelled during start rollback", self.name` |
| `start()` rollback, line ≈685 | `_access_reload_task` | start rollback | `"discord(%s): access reload task cancelled during start rollback", self.name` |
| `start()` rollback, line ≈697 | `_client_task` | start rollback | `"discord(%s): gateway task cancelled during start rollback", self.name` |
| `stop()`, line ≈1022 | `_sweep_task` | stop | `"discord(%s): sweep task cancelled during stop", self.name` |
| `stop()`, line ≈1033 | `_attachment_sweep_task` | stop | `"discord(%s): attachment sweep task cancelled during stop", self.name` |
| `stop()`, line ≈1044 | `_access_reload_task` | stop | `"discord(%s): access reload task cancelled during stop", self.name` |
| `stop()`, line ≈1056 | `_client_task` | stop | `"discord(%s): gateway task cancelled during stop", self.name` |

The five `except asyncio.CancelledError: raise` guards (in `_typing_while_pending`, the three sweep loops, and `_send_typing._pulse`) are **not swallows** and must remain as `raise` — do not touch them. The `send_retry.py` guard at line ≈78 also uses `raise` and must not be touched.

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` mypy configuration.** In the `[tool.mypy]` table, add `"packages/agent-core-discord/src"` as a third entry in `files`. Then add two new `[[tool.mypy.overrides]]` sections after the existing `[tool.mypy]` block:

   ```toml
   [[tool.mypy.overrides]]
   module = ["discord", "discord.*"]
   ignore_missing_imports = true

   [[tool.mypy.overrides]]
   module = ["agent_core_discord.*"]
   strict = true
   ```

2. **Add type annotations to `endpoint.py`.** Fix every gap listed in the Approach section. Key changes:
   - Add `Coroutine` to the existing `from collections.abc import Callable` import line (becomes `from collections.abc import Callable, Coroutine`).
   - `_make_on_message_handler(self) -> Callable[[Any], Coroutine[Any, Any, None]]`
   - `_make_on_reaction_add_handler(self) -> Callable[[Any, Any], Coroutine[Any, Any, None]]`
   - `_make_on_raw_poll_vote_handler(self, event_type: str) -> Callable[[Any], Coroutine[Any, Any, None]]`
   - `_make_on_raw_message_lifecycle_handler(self, event_type: str) -> Callable[[Any], Coroutine[Any, Any, None]]`
   - `_resolve_channel(self, channel_id: str) -> Any`
   - `_deliver_text_message(self, envelope: Envelope) -> dict[str, Any]`
   - `_dispatch(self, tool: str, args: dict[str, Any], env: Envelope) -> Any`
   - Inside `_dispatch`: `_v(model: Any, raw: dict[str, Any]) -> Any` and `_inject_channel_id(raw: dict[str, Any]) -> dict[str, Any]`
   - `_send(self, args: _SendArgs) -> dict[str, Any]`
   - `_edit(self, args: _EditArgs) -> dict[str, Any]`
   - `_react(self, args: _ReactArgs) -> dict[str, Any]`
   - `_fetch(self, args: _FetchArgs) -> list[dict[str, Any]]`
   - `_download_attachments(self, args: _DownloadAttachmentsArgs) -> dict[str, Any]`
   - `_list_channels(self, args: _ListChannelsArgs) -> list[dict[str, Any]]`
   - `_get_channel_info(self, args: _GetChannelInfoArgs) -> dict[str, Any]`
   - `_send_briefing(self, args: _SendBriefingArgs) -> dict[str, Any]`
   - `_create_poll(self, args: _CreatePollArgs) -> dict[str, Any]`
   - `_create_scheduled_event(self, args: _CreateScheduledEventArgs) -> dict[str, Any]`
   - `_cancel_scheduled_event(self, args: _CancelScheduledEventArgs) -> dict[str, Any]`
   - `_create_thread(self, args: _CreateThreadArgs) -> dict[str, Any]`
   - `_send_typing(self, args: _SendTypingArgs) -> dict[str, Any]`
   - Nested inside `_send_typing`: `def _done(t: asyncio.Task[Any]) -> None`.
   - Run `uv run mypy` after each batch of fixes to catch any additional issues mypy surfaces.

3. **Add type annotations to `testing/fakes.py`.** Fix every gap listed in the Approach section:
   - `FakeAttachment.__init__(...) -> None`
   - `FakeMessage.__init__(self, *, id: str, channel_id: str, content: str = "", author: Any = None, ...) -> None`
   - `FakeChannel.__init__(...) -> None`
   - `FakeChannel.typing(self) -> Any` — simplest fix is `-> Any`; alternatively fully annotate `_T.__aenter__` as `async def __aenter__(self) -> None` and `__aexit__` as `async def __aexit__(self, *exc: Any) -> None`
   - `FakeChannel.history(self, limit: int = 50, before: Any = None) -> AsyncGenerator[FakeMessage, None]` (add `from collections.abc import AsyncGenerator` to imports)
   - `FakeGuild.__init__(...) -> None`
   - `FakeUser.__init__(...) -> None`
   - `FakeDiscordClient.__init__(self, *, intents: Any = None) -> None`
   - `FakeDiscordClient._handlers: dict[str, Callable[..., Any]]`
   - `FakeDiscordClient.event(self, fn: Callable[..., Any]) -> Callable[..., Any]`
   - `FakeDiscordClient.add_listener(self, fn: Callable[..., Any], name: str | None = None) -> None`
   - `FakeDiscordClient.guilds` property `-> list[FakeGuild]`
   - `FakeDiscordClient.fire(self, event_name: str, *args: Any) -> None` (already `async def`, just add `*args: Any`)
   - `FakeBusHandle.publish(self, *a: Any, **kw: Any) -> Any`
   - `FakeBusHandle.ack(self, *a: Any, **kw: Any) -> Any`
   - `FakeBusHandle.nack(self, *a: Any, **kw: Any) -> Any`
   - `FakeBusHandle.endpoints(self) -> list[Any]`
   - `FakeBusHandle.spawn(self, coro: Any, *, name: str | None = None) -> asyncio.Task[Any]`

4. **Run mypy to verify no remaining errors.** After sub-requests 1–3, run:
   ```bash
   uv run --no-sync mypy
   ```
   Expected output: `Success: no issues found`. If mypy surfaces additional issues beyond those listed above (e.g., in `briefing.py`, `access.py`, or `chunking.py`), fix them in this same commit. Verify that `access.py`'s `# type: ignore[arg-type]` on `dm_policy` is still needed under strict: remove it temporarily, run mypy, and re-add if mypy still rejects the narrowing (it most likely will, because `_VALID_DM_POLICIES` is `set[str]` not `AbstractSet[Literal[...]]`). If mypy no longer needs it, remove the ignore. Commit all annotation + config changes together:
   ```bash
   git add pyproject.toml \
     packages/agent-core-discord/src/agent_core_discord/endpoint.py \
     packages/agent-core-discord/src/agent_core_discord/testing/fakes.py
   git commit -m "feat: enable mypy --strict for agent-core-discord src"
   ```

5. **Add debug-level log calls to the 8 CancelledError swallows.** In `endpoint.py` only; no moves. Replace each `except asyncio.CancelledError:\n    pass` in `start()` and `stop()` with `except asyncio.CancelledError:\n    log.debug(...)` per the table in the Approach section. The five `raise` guards in `_typing_while_pending`, the three sweep loops, and `_send_typing._pulse` are **not touched**. Commit separately:
   ```bash
   git add packages/agent-core-discord/src/agent_core_discord/endpoint.py
   git commit -m "fix: log CancelledError swallows in endpoint start/stop at debug level"
   ```

6. **Verify the full gate.** Run:
   ```bash
   just check
   ```
   Expected: green (lint, typecheck, contracts, test, patch-cov all pass).

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/agent-core-discord/src"` to `[tool.mypy].files`; add two `[[tool.mypy.overrides]]` blocks (discord stubs + agent_core_discord strict) |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Add `Coroutine` to `collections.abc` import; add return types to handler factory methods and all tool handler methods; fix bare `dict`/`list[dict]` type parameters; annotate `asyncio.Task[Any]` in `_done`; add `log.debug(...)` to 8 CancelledError guards (separate commit) |
| `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py` | Add `-> None` to all `__init__` methods; fix `author: Any` parameter on `FakeMessage`; parameterize all bare `Callable`; annotate `guilds` property; annotate `fire` `*args`; annotate `FakeChannel.typing/history`; fully annotate `FakeBusHandle` stubs |
| Other source files in `packages/agent-core-discord/src` (if mypy surfaces gaps) | Minor annotation fixes as discovered by running mypy |

No new files are created. No methods are moved between files.

## Alternatives considered

1. **Comment-only documentation of the CancelledError swallows.** The issue's SpecReview #406 explicitly rejected this: a comment is invisible to tooling and can drift out of sync. A `log.debug(...)` call is testable (tests can assert the log record via `caplog`), requires no comment to explain the intent, and survives future code moves intact. Ruled out.

2. **Promote swallows to `log.warning`.** The CancelledError in each of these guards is the EXPECTED outcome — the task was explicitly `.cancel()`d immediately before the await. Warning-level logging implies something unexpected happened; debug-level is correct for "this is the happy exit path of a cancelled task". Ruled out.

3. **Enable `strict = true` at the root `[tool.mypy]` level globally.** Would immediately break the currently-passing typecheck step for `packages/core/src` and `packages/agent-core-channel/src`, which have not been prepared for strict mode. The per-module override is the right scoping mechanism. Ruled out.

4. **Extract lifecycle methods to a new `_lifecycle.py` mixin before adding mypy strict.** The issue calls this "step 6 of 6" implying the mixin extraction was supposed to happen in steps 1–5. Since those steps have not yet landed, adding the mixin extraction to this spec would widen the scope significantly and block a clean incremental landing. Ruled out for this issue; the mixin extraction remains a follow-on.

## Open questions

1. **Has the F-B6.1–5 mixin extraction landed on the branch being implemented?** If so, `endpoint.py`'s lifecycle section may have been moved to `_lifecycle.py` and the file references in sub-requests 2 and 5 must be adjusted. The Worker should check `git log --oneline` on the implementation branch for evidence of a mixin restructure.

2. **Does `access.py`'s `# type: ignore[arg-type]` on `dm_policy` remain needed under strict?** Most likely yes — mypy cannot narrow the `str` return from `raw.get("dmPolicy", "open")` to `Literal["open", "deny", "allowlist"]` even with the explicit guard, because `_VALID_DM_POLICIES` is typed as `set[str]` not `AbstractSet[Literal[...]]`. Sub-request 4 tells the Worker to test this explicitly.

## Out of scope

- Extracting `endpoint.py` into mixin files (`_lifecycle.py`, `_inbound.py`, `_outbound.py`, etc.) — this is the F-B6.1–5 work, not step 6.
- Enabling mypy `--strict` for `packages/core/src`, `packages/agent-core-channel/src`, or any other workspace package — a separate multi-ticket effort.
- Adding tests for the new `log.debug(...)` calls — the swallows are exercised by the existing lifecycle tests (`test_endpoint_lifecycle.py`, `test_discord_spawn_lifecycle.py`); a debug-level log is observable via `caplog` but the existing coverage is sufficient for this PR.
- Wiring `packages/agent-core-discord/tests` into mypy — test files are not in the `src/` tree and are not part of the production package; adding them to mypy's files list is a separate decision.
