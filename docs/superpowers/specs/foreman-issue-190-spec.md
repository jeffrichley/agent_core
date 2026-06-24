# Spec: hot-reload `access_config_path` on mtime change (issue #190)

## Goal

Add a background mtime-poll task to `DiscordEndpoint` that detects changes to `access_config_path` on disk and atomically swaps `self._access` without restarting the endpoint. Operators can edit the JSON channel allowlist and see it take effect within one poll interval (default 5 s) while all other endpoints in the same daemon process continue uninterrupted.

Issue: https://github.com/jeffrichley/agent_core/issues/190

## Acceptance criteria

- `DiscordEndpoint.__init__` accepts `access_config_reload_interval: float = 5.0`. When `> 0` and `access_config_path` is not `None`, a background asyncio task is spawned in `start()` and cancelled in `stop()`. When `== 0` or `access_config_path is None`, no task is started (test-friendly disable path).
- The task polls `access_config_path.stat().st_mtime` every `access_config_reload_interval` seconds. When the mtime is identical to the last-loaded mtime, no action is taken.
- When mtime changes, the task pre-validates the file (reads it and calls `json.loads`) before calling `load_access_config`. A `json.JSONDecodeError` or `OSError` (partial write, permissions change) causes the task to log at WARN level with the error message, keep the previous `self._access`, and retry on the next poll cycle. It does NOT swap to `AccessConfig()` defaults.
- On a clean reload, `self._access` is replaced with the freshly-loaded `AccessConfig`, `self._access_config_mtime` is updated, and an INFO log is emitted: `"discord-<name>: access config reloaded (channels=N, dmPolicy=X)"`.
- Initial mtime is recorded in `start()` immediately after the initial `load_access_config` call so the first poll cycle does not false-fire on a file that has not changed.
- The reload task follows the repo's cancel-all-first-then-await-all rollback discipline in both `stop()` and the `start()` rollback block (`except BaseException`), matching the existing pattern for `_sweep_task` and `_attachment_sweep_task` (endpoint.py:939–970 and endpoint.py:596–654).
- Test: `test_access_reload_picks_up_added_channel` — write a valid access JSON with one channel, start endpoint with a short interval (0.05 s), add a second channel by rewriting the file, wait `interval + 0.1 s`, assert `self._access.channels` includes the new channel.
- Test: `test_access_reload_picks_up_removed_channel` — reverse: start with two channels, remove one, wait, assert removed channel is absent.
- Test: `test_access_reload_keeps_config_on_malformed_json` — write valid JSON, start endpoint, then overwrite with malformed JSON, wait one cycle, assert `self._access` unchanged (channels still the original set).
- Test: `test_access_reload_warns_on_malformed_json` — same setup, assert a WARN log record contains the string `"access config reload"` (caplog, matching the existing pattern in `test_access.py`).
- Test: `test_access_reload_disabled_when_interval_zero` — construct with `access_config_reload_interval=0`, start, assert `ep._access_reload_task is None`.
- Test: `test_access_reload_disabled_when_no_path` — no `access_config_path`, default interval, start, assert `ep._access_reload_task is None`.
- Test: `test_access_reload_task_cancelled_on_stop` — start then stop, assert `ep._access_reload_task is None`.
- `new_failures_count == 0` against the full `packages/agent-core-discord` suite.

## Approach

**Pattern naming.** No GoF pattern fits exactly. The relevant principle is "make the right thing easy": a background polling loop is the minimal mechanism that satisfies the operator's actual need (human-cadence edits, ~5 s latency fine) without introducing new library dependencies or bus-coupling. This is a straightforward polling-loop with mtime gating — naming a pattern where one does not cleanly apply would add false weight.

**Why mtime-poll over `watchfiles`/inotify.** The issue explicitly recommends option (1) with the rationale: the edit cadence is human-operator, not machine, so 5 s poll is sufficient. `watchfiles` would add a new dependency and more moving parts. The spec follows the issue's recommendation.

**Why NOT modify `load_access_config` for the malformed-JSON case.** `load_access_config` already handles `json.JSONDecodeError` by returning permissive `AccessConfig()` defaults and logging an exception. That is the right behavior for the initial load at `start()` time (fail-open so the endpoint doesn't block on a misconfigured access file). It is the WRONG behavior in the reload path — swapping to permissive defaults on a partial write would silently open a security door. The reload loop therefore pre-validates the file itself (`json.loads`) before calling `load_access_config`. `load_access_config` is not modified; all its existing behavior for the initial load path is preserved.

**Why the mtime comparison uses `float` equality, not `!=`.** `st_mtime` on Linux is a float with sub-second resolution. Two successive writes within the same second would still differ by mtime. Equality comparison (`==`) is sufficient and avoids spurious reloads on filesystem-metadata-only operations (e.g., `touch` without content change on some filesystems). If `touch` does advance mtime without changing content, the reload produces an identical `AccessConfig` — the swap is a no-op at the gate level, so it is safe.

**Why the initial mtime is captured AFTER `load_access_config` in `start()`, not before.** Recording the mtime after the load ensures the first poll cycle baseline matches the file version actually in memory. Recording before would create a race where a concurrent write between the mtime-capture and the load would let the first poll cycle reload the same version the task just loaded, producing a spurious reload log.

**Task placement in `start()`.** The task is started after the `on_ready` event fires (alongside `_sweep_task` and `_attachment_sweep_task` at endpoint.py:586–595), not before. Starting it earlier would create a task that may fire during the `asyncio.wait` race window and before `self._handle` is valid. Starting alongside the other sweep tasks is consistent with the existing lifecycle discipline.

**Rollback discipline.** The existing rollback in `start()` (endpoint.py:596–654) and the shutdown sequence in `stop()` (endpoint.py:929–991) both follow "cancel all first, then await all" to prevent a non-yielding `asyncio.sleep` monkeypatch from spinning forever (per the existing comment at endpoint.py:938–942). The reload task must be added to both the cancel and the await phases in both locations.

**GIL covers the atomic swap.** The issue notes this explicitly: `self._access = new_access` is a single reference reassignment; Python's GIL makes it atomic with respect to concurrent reads in other coroutines that read `self._access`. No lock is needed.

**Existing conventions used.** `json` is already imported at endpoint.py:17. `load_access_config` is already imported at endpoint.py:38. Log calls follow the `log.warning("discord(%s): ...", self.name, ...)` pattern already used throughout the file.

## Sub-requests (topologically sorted)

1. In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, add `access_config_reload_interval: float = 5.0` to `DiscordEndpoint.__init__`'s parameter list (after `recent_inbounds_ttl_seconds: float = 3600.0` at line 260, before `_client_factory`). In the `__init__` body, add three instance variables after the `_typing_tasks` initialization at line 321:

   ```python
   self.access_config_reload_interval = access_config_reload_interval
   self._access_reload_task: asyncio.Task | None = None
   self._access_config_mtime: float | None = None
   ```

2. In `start()`, after the `self._access = load_access_config(self.access_config_path)` call at line 481, record the initial mtime:

   ```python
   # Record initial mtime so the poll task can detect future changes.
   if self.access_config_path is not None and self.access_config_path.exists():
       try:
           self._access_config_mtime = self.access_config_path.stat().st_mtime
       except OSError:
           self._access_config_mtime = None
   ```

3. In `start()`, after the `_attachment_sweep_task` is created (around line 594), start the reload task:

   ```python
   if self.access_config_path is not None and self.access_config_reload_interval > 0:
       self._access_reload_task = asyncio.create_task(
           self._access_config_reload_loop(),
           name=f"discord-endpoint-{self.name}-access-reload",
       )
   ```

4. In `start()`'s `except BaseException` rollback block (endpoint.py:596–654), add the reload task to the "cancel all first" phase immediately after the `_attachment_sweep_task.cancel()` line, and add the await immediately after the `_attachment_sweep_task` await block. Follow this exact pattern, mirroring the existing sweep task cleanup:

   Cancel phase (add after the `_attachment_sweep_task.cancel()` call):
   ```python
   if self._access_reload_task is not None:
       self._access_reload_task.cancel()
   ```

   Await phase (add after the `_attachment_sweep_task` await block):
   ```python
   if self._access_reload_task is not None:
       try:
           await self._access_reload_task
       except asyncio.CancelledError:
           pass
       except Exception:
           log.exception(
               "discord endpoint '%s': access reload task raised during start rollback",
               self.name,
           )
       self._access_reload_task = None
   ```

5. In `stop()` (endpoint.py:929–991), add the reload task to both the "cancel all first" phase and the "await all" phase, following the same pattern as `_sweep_task` and `_attachment_sweep_task`. Cancel phase: add `if self._access_reload_task is not None: self._access_reload_task.cancel()` alongside the existing sweep cancellations. Await phase: add the await block alongside the existing sweep awaits:

   ```python
   if self._access_reload_task is not None:
       try:
           await self._access_reload_task
       except asyncio.CancelledError:
           pass
       except Exception:
           log.exception(
               "discord endpoint '%s': access reload task raised during stop",
               self.name,
           )
       self._access_reload_task = None
   ```

6. Add the `_access_config_reload_loop()` method to `DiscordEndpoint`. Place it immediately before `_attachment_sweep_loop` (around line 1470) so all three sweep-loop methods are grouped together:

   ```python
   async def _access_config_reload_loop(self) -> None:
       """Periodic mtime-poll reload of access_config_path. Runs until cancelled by stop().

       Pre-validates JSON before swapping self._access so a partial write does not
       open the gate to permissive defaults. Keeps the previous config on any read or
       parse error; retries on the next poll cycle.
       """
       try:
           while True:
               await asyncio.sleep(self.access_config_reload_interval)
               if self.access_config_path is None:
                   continue
               try:
                   current_mtime = self.access_config_path.stat().st_mtime
               except OSError:
                   continue
               if current_mtime == self._access_config_mtime:
                   continue
               # mtime changed — pre-validate before committing
               try:
                   raw_text = self.access_config_path.read_text(encoding="utf-8")
                   json.loads(raw_text)
               except (OSError, json.JSONDecodeError) as exc:
                   log.warning(
                       "discord(%s): access config reload skipped (read/parse error), "
                       "keeping previous config: %s",
                       self.name,
                       exc,
                   )
                   continue
               new_access = load_access_config(self.access_config_path)
               self._access = new_access
               self._access_config_mtime = current_mtime
               log.info(
                   "discord(%s): access config reloaded (channels=%d, dmPolicy=%s)",
                   self.name,
                   len(new_access.channels),
                   new_access.dm_policy,
               )
       except asyncio.CancelledError:
           raise
   ```

7. Create `packages/agent-core-discord/tests/test_access_reload.py` with the tests described in Acceptance criteria. Use `access_config_reload_interval=0.05` (50 ms) for all timing tests so they complete in under 1 s. Use `asyncio.sleep(interval + 0.1)` as the "wait one cycle" step. Use `FakeDiscordClient` via the `_client_factory` seam and `monkeypatch.setenv("X_TOK", "tok")`, mirroring the `_start_endpoint` helper pattern in `test_endpoint_inbound.py:28–42`. Full test file content:

   ```python
   """Tests for DiscordEndpoint access-config hot-reload (issue #190)."""
   from __future__ import annotations

   import asyncio
   import json
   import logging

   import pytest
   from agent_core_discord.endpoint import DiscordEndpoint
   from agent_core_discord.testing.fakes import FakeBusHandle, FakeDiscordClient


   async def _start(monkeypatch, tmp_path, *, interval: float = 0.05, path_json: dict | None = None):
       """Start a DiscordEndpoint with an optional access config file at tmp_path."""
       access_path = None
       if path_json is not None:
           p = tmp_path / "access.json"
           p.write_text(json.dumps(path_json), encoding="utf-8")
           access_path = p
       monkeypatch.setenv("X_TOK", "tok")
       ep = DiscordEndpoint(
           name="discord-test",
           target="agent-test",
           token_env="X_TOK",
           access_config_path=access_path,
           access_config_reload_interval=interval,
           _client_factory=lambda **kw: FakeDiscordClient(**kw),
       )
       await ep.start(FakeBusHandle())
       return ep, access_path


   @pytest.mark.asyncio
   async def test_access_reload_picks_up_added_channel(monkeypatch, tmp_path):
       initial = {"channels": {"100": {}}}
       ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
       try:
           p.write_text(json.dumps({"channels": {"100": {}, "200": {}}}), encoding="utf-8")
           await asyncio.sleep(0.05 + 0.1)
           assert "200" in ep._access.channels
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_access_reload_picks_up_removed_channel(monkeypatch, tmp_path):
       initial = {"channels": {"100": {}, "200": {}}}
       ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
       try:
           p.write_text(json.dumps({"channels": {"100": {}}}), encoding="utf-8")
           await asyncio.sleep(0.05 + 0.1)
           assert "200" not in ep._access.channels
           assert "100" in ep._access.channels
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_access_reload_keeps_config_on_malformed_json(monkeypatch, tmp_path):
       initial = {"channels": {"100": {}}}
       ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
       try:
           original_channels = dict(ep._access.channels)
           # Simulate a mid-edit partial write
           p.write_text("{bad json", encoding="utf-8")
           await asyncio.sleep(0.05 + 0.1)
           assert ep._access.channels == original_channels
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_access_reload_warns_on_malformed_json(monkeypatch, tmp_path, caplog):
       initial = {"channels": {"100": {}}}
       ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
       try:
           p.write_text("{bad json", encoding="utf-8")
           with caplog.at_level(logging.WARNING):
               await asyncio.sleep(0.05 + 0.1)
           assert any("access config reload" in rec.message for rec in caplog.records)
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_access_reload_disabled_when_interval_zero(monkeypatch, tmp_path):
       initial = {"channels": {"100": {}}}
       ep, _ = await _start(monkeypatch, tmp_path, interval=0, path_json=initial)
       try:
           assert ep._access_reload_task is None
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_access_reload_disabled_when_no_path(monkeypatch, tmp_path):
       ep, _ = await _start(monkeypatch, tmp_path, path_json=None)
       try:
           assert ep._access_reload_task is None
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_access_reload_task_cancelled_on_stop(monkeypatch, tmp_path):
       initial = {"channels": {"100": {}}}
       ep, _ = await _start(monkeypatch, tmp_path, path_json=initial)
       assert ep._access_reload_task is not None
       await ep.stop()
       assert ep._access_reload_task is None
   ```

8. Create `packages/agent-core-discord/changelog.d/190.added.md` with the towncrier `added` fragment:

   ```
   `DiscordEndpoint` now hot-reloads its `access_config_path` JSON without a daemon
   restart. A background mtime-poll task (default interval: 5 s, configurable via
   `access_config_reload_interval`, 0 to disable) detects file changes and atomically
   swaps the in-memory `AccessConfig`. Malformed JSON during a mid-edit write is logged
   at WARN level and ignored; the previous config is kept until the next poll finds a
   valid file. Operators can add channels to the allowlist and see them take effect
   within one poll interval while all other endpoints in the same daemon process run
   uninterrupted. (#190)
   ```

9. Run from the repo root to verify:
   ```bash
   uv run pytest packages/agent-core-discord/tests -v --no-cov
   just check
   ```
   Expected: zero failures, zero lint errors.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | (1) Add `access_config_reload_interval: float = 5.0` param to `__init__`. (2) Add `self.access_config_reload_interval`, `self._access_reload_task`, `self._access_config_mtime` instance variables. (3) Record initial mtime after `load_access_config` in `start()`. (4) Start reload task after ready event, alongside sweep tasks. (5) Add reload task to cancel-all-first + await-all rollback pattern in both `start()`'s `except BaseException` block and `stop()`. (6) Add `_access_config_reload_loop()` method. |
| `packages/agent-core-discord/tests/test_access_reload.py` | New file. Seven async tests covering: add channel, remove channel, malformed JSON keeps config, malformed JSON warns, interval=0 disables, no path disables, stop cancels task. |
| `packages/agent-core-discord/changelog.d/190.added.md` | New towncrier `added` fragment for the hot-reload feature. |

## Alternatives considered

- **`watchfiles`/inotify (option 2 from the issue).** Faster reaction (sub-second), event-driven rather than poll-based. Rejected per the issue's own recommendation: the edit cadence is human-operator, 5 s poll is sufficient, and adding a new library dependency for a polling interval acceptable to operators is unnecessary complexity.
- **SIGHUP signal handler.** A conventional Unix daemon restart signal. Would work on Linux/macOS but is not portable to Windows (signal.SIGHUP does not exist). The daemon runs on Windows for some users. Rejected.
- **Bus-side reload event.** Send a bus `Event` envelope to the endpoint telling it to reload. More extensible. Adds bus-coupling to a feature the issue explicitly marks as orthogonal to bus-level work. Rejected per the issue's Out of scope section; can be added later.
- **Modify `load_access_config` to raise on malformed JSON.** Would require adding a `strict: bool` parameter and a callers audit. Keeping the two call paths (initial load = fail-open, hot-reload = keep-old-on-error) separate is cleaner. Rejected — the pre-validation in the reload loop is minimal code and keeps `load_access_config` unchanged.
- **Do nothing.** The operator workaround (schedule daemon restarts around cron windows) works but compounds with every new channel add. Rejected — the issue documents a concrete recurring operational cost.

## Open questions

None.

## Out of scope

- Hot-reload of `token_env` / `env_file` (bot token). Token changes require client reconnect; file separately.
- Hot-reload of other construction-time params (`mailbox`, `outbound_channel_id`, `attachments_dir`).
- Bus-coupled reload signals.
- Generalizing the reload mechanism to other endpoint types (voice, webcam, MCP servers). The pattern can be extracted after this shape is proven.
- Modifying `load_access_config` behavior for the initial-load path.
