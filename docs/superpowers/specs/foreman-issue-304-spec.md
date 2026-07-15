# Spec: portable liveness watchdog (heartbeat + self-terminate) (issue #304)

## Goal

Add a portable OS-thread liveness watchdog to the bus daemon so that a hung-but-alive process (live PID, completely wedged event loop — the 2026-07-14 invisible-outage class) is detected and force-terminated via `os._exit(non-zero)`, enabling the OS service manager to restart a fresh process. Core event-loop iterations bump a monotonic heartbeat; a dedicated OS thread (never an asyncio task) fires when the beat stops for `watchdog_timeout_seconds` (default 90 s). The same heartbeat writes a file for Theme E and pings systemd's `sd_notify` on Linux.

Issue: https://github.com/jeffrichley/agent_core/issues/304
Sub-ticket of #265 (Theme B — portable install & lifecycle) · epic #262. B-1 of 3.

## Acceptance criteria

- `packages/core/src/agent_core/bus/watchdog.py` exists with:
  - `_sd_notify_watchdog()` — writes `WATCHDOG=1` to `NOTIFY_SOCKET` (no-op when unset); errors swallowed
  - `Watchdog(timeout_seconds, *, clock=None, exit_fn=None, heartbeat_path=None)` class with `heartbeat()`, `start()`, `stop()`, and `check_once()` methods; `exit_fn` defaults to `os._exit`; `clock` defaults to `SystemClock()`
- `BusConfig` in `packages/core/src/agent_core/bus/core.py` has `watchdog_timeout_seconds: int = 90`
- `packages/core/src/agent_core/bus/runner.py` parses `BUS_WATCHDOG_TIMEOUT_SECONDS` env → YAML `bus.watchdog_timeout_seconds` → default 90
- `_run_bus` in `packages/core/src/agent_core/bus/cli.py`:
  - Constructs `Watchdog(bus.config.watchdog_timeout_seconds, heartbeat_path=bus.config.storage_path.parent / "watchdog_heartbeat")` and calls `start()` before `bus.start()`; `stop()` in the same `finally` block as `http_host.stop()`
  - Calls `watchdog.heartbeat()` at the top of each `_ttl_loop` and `_redelivery_loop` iteration
- `bus status` (the `_status` coroutine in `cli.py`) reads `storage_path.parent / "watchdog_heartbeat"` and prints seconds-since-last-heartbeat (or a "no file" note if absent)
- Unit tests in `packages/core/tests/bus/test_watchdog.py`:
  - FakeClock: calling `check_once()` after advancing the clock past the threshold fires exactly one CRITICAL `"WatchdogFired"` log + one `exit_fn` call; a second `check_once()` is a no-op (idempotent)
  - FakeClock: `heartbeat()` → advance clock → `check_once()` — no fire if beat was recent enough
  - FakeClock: healthy loop (heartbeat before each `check_once()`) never fires
  - `start()` with `timeout_seconds <= 0` creates no thread; `stop()` on disabled watchdog is safe
  - `heartbeat()` writes the heartbeat file with an ISO timestamp when `heartbeat_path` is set
  - `@pytest.mark.slow` thread test: `start()` the thread, never call `heartbeat()`, wait for real clock to elapse ≥ timeout; `exit_fn` called exactly once (proves off-loop design)
- Pure Python; no new third-party dependencies; cross-platform (Windows/Linux/macOS); unit-testable in Linux CI

## Approach

No GoF pattern fits cleanly. Engineering principle: **SRP** — the `Watchdog` is a standalone component with one job (detect a stalled loop and terminate), holding no knowledge of the bus, endpoints, or supervision layer.

**Why an OS thread, not an asyncio task**: A task blocked on the asyncio event loop cannot pre-empt a coroutine that never yields. A call to blocking I/O inside `run_ttl_sweep_once()` (e.g., SQLite under a lock) would stall every asyncio task including a watchdog task, preventing it from firing. A `threading.Thread` runs on a separate OS thread and is unaffected by asyncio loop state. This is the core correctness property the issue requires.

**`Watchdog` class** (`bus/watchdog.py`):
- `__init__`: stores `timeout_seconds`, `clock` (defaults `SystemClock()`), `exit_fn` (defaults `os._exit`), `heartbeat_path`; initializes `_last_progress = clock.now()`, `_fired = False`, `_stop_event = threading.Event()`, `_thread = None`.
- `heartbeat()`: assigns `self._last_progress = self._clock.now()`, writes `heartbeat_path` with ISO timestamp (silently ignores `OSError`), calls `_sd_notify_watchdog()`.
- `check_once()`: if `_fired`, return immediately. Compute `elapsed = (clock.now() - _last_progress).total_seconds()`. If `elapsed >= timeout_seconds`: set `_fired = True`, log CRITICAL `"WatchdogFired: no progress for X.Xs (threshold=Ys); terminating process"`, call `exit_fn(1)`.
- `start()`: if `timeout_seconds <= 0`, return (disabled). Reset `_last_progress`, clear `_stop_event`, start a `threading.Thread(target=_run, daemon=True, name="bus-watchdog")`.
- `_run()`: compute `poll_interval = max(0.5, min(timeout_seconds / 3, 10.0))`; loop `_stop_event.wait(timeout=poll_interval)` → `check_once()` → break if fired.
- `stop()`: set `_stop_event`; join thread with 5 s timeout; set `_thread = None`.

**`_sd_notify_watchdog()`**: if `NOTIFY_SOCKET` env is set, open `socket.AF_UNIX/SOCK_DGRAM`, connect to that path, send `b"WATCHDOG=1\n"`. Swallow `OSError`. Pure stdlib, no new deps, no-op on macOS/Windows.

**Config** (`BusConfig` in `core.py`): append `watchdog_timeout_seconds: int = 90` after `slow_deliver_warn_seconds`. Non-positive values are legal (disables the watcher); no `__post_init__` change needed.

**Runner** (`runner.py`): add `watchdog_timeout_seconds=int(os.environ.get("BUS_WATCHDOG_TIMEOUT_SECONDS", bus_cfg_raw.get("watchdog_timeout_seconds", 90)))` to the existing `BusConfig(...)` constructor call (lines 86-101), immediately after `slow_deliver_warn_seconds=...` and before `supervisor=supervisor`. Env var overrides YAML; YAML overrides the default 90, matching the existing `BUS_SLOW_DELIVER_WARN_SECONDS` pattern.

**CLI wiring** (`cli.py`): Import `Watchdog` at the top of the file. In `_run_bus`, after `build_bus_from_config`:
```
watchdog = Watchdog(bus.config.watchdog_timeout_seconds,
                    heartbeat_path=bus.config.storage_path.parent / "watchdog_heartbeat")
watchdog.start()
```
Wrap the existing `try: await bus.start() ... finally:` block so `watchdog.stop()` is called in the `finally` alongside `http_host.stop()`. Add `watchdog.heartbeat()` as the first statement inside the `while not stop_event.is_set():` body of both `_ttl_loop` and `_redelivery_loop`.

**`bus status` freshness** (`_status` in `cli.py`): After the degraded-endpoints block, read `bus.config.storage_path.parent / "watchdog_heartbeat"`. If it exists, parse the ISO timestamp, compute `age_s = (datetime.now(UTC) - last_beat).total_seconds()`, print `f"last heartbeat: {age_s:.0f}s ago"`. If it doesn't exist, print `"last heartbeat: no file (bus not running or watchdog disabled)"`. Silently ignore parse errors (print `"last heartbeat: unreadable"`).

**Tests**: All FakeClock tests call `check_once()` directly — no thread created, no sleep, no timeout in CI. One `@pytest.mark.slow` class uses a real 1 s timeout + `threading.Event.wait(timeout=5)` to exercise the actual OS thread.

## Sub-requests (topologically sorted)

1. **`bus/core.py`**: Append `watchdog_timeout_seconds: int = 90` field to `BusConfig` immediately after `slow_deliver_warn_seconds: float = 5.0`.

2. **Create `bus/watchdog.py`**:

   ```python
   """Liveness watchdog — OS-thread heartbeat monitor (issue #304).

   Detects hung-but-alive bus daemons (live process, wedged event loop)
   and forces an OS restart via os._exit(non-zero). An OS thread (never
   an asyncio task) performs the check so a wedged loop cannot suppress it.

   Usage in production (_run_bus):
       watchdog = Watchdog(bus.config.watchdog_timeout_seconds,
                           heartbeat_path=bus.config.storage_path.parent / "watchdog_heartbeat")
       watchdog.start()
       try:
           # ...
           # call watchdog.heartbeat() at the top of each loop iteration
       finally:
           watchdog.stop()

   Usage in tests (FakeClock, no thread):
       clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
       exits: list[int] = []
       wd = Watchdog(60, clock=clock, exit_fn=exits.append)
       wd.heartbeat()
       clock.advance(61)
       wd.check_once()
       assert exits == [1]
   """

   from __future__ import annotations

   import logging
   import os
   import socket
   import threading
   from collections.abc import Callable
   from datetime import datetime
   from pathlib import Path

   from agent_core.clock import Clock, SystemClock

   log = logging.getLogger(__name__)


   def _sd_notify_watchdog() -> None:
       """Write WATCHDOG=1 to NOTIFY_SOCKET (systemd WatchdogSec integration).

       No-op when NOTIFY_SOCKET is not set (macOS, Windows, non-systemd Linux).
       OSError is silently swallowed — sd_notify is best-effort observability;
       the self-terminate path does not depend on it.
       """
       notify_socket = os.environ.get("NOTIFY_SOCKET")
       if not notify_socket:
           return
       try:
           with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
               sock.connect(notify_socket)
               sock.sendall(b"WATCHDOG=1\n")
       except OSError:
           pass


   class Watchdog:
       """OS-thread heartbeat monitor. Pure Python, cross-platform, unit-testable.

       Disabled entirely when ``timeout_seconds <= 0``. When enabled, a
       dedicated OS thread calls ``check_once()`` every
       ``max(0.5, min(timeout / 3, 10))`` seconds; fires ``exit_fn(1)``
       (default ``os._exit(1)``) when the loop stops bumping ``heartbeat()``.
       """

       def __init__(
           self,
           timeout_seconds: int,
           *,
           clock: Clock | None = None,
           exit_fn: Callable[[int], None] | None = None,
           heartbeat_path: Path | None = None,
       ) -> None:
           self._timeout_seconds = timeout_seconds
           self._clock: Clock = clock or SystemClock()
           self._exit_fn: Callable[[int], None] = (
               exit_fn if exit_fn is not None else os._exit
           )
           self._heartbeat_path = heartbeat_path
           self._last_progress: datetime = self._clock.now()
           self._fired = False
           self._stop_event = threading.Event()
           self._thread: threading.Thread | None = None

       # ------------------------------------------------------------------
       # Public API — called from the asyncio event loop
       # ------------------------------------------------------------------

       def heartbeat(self) -> None:
           """Bump the progress timestamp.

           Call once per core event-loop iteration (top of _ttl_loop and
           _redelivery_loop in cli.py). Also writes the heartbeat file and
           pings systemd if configured.
           """
           now = self._clock.now()
           self._last_progress = now
           if self._heartbeat_path is not None:
               try:
                   self._heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                   self._heartbeat_path.write_text(now.isoformat(), encoding="utf-8")
               except OSError:
                   pass
           _sd_notify_watchdog()

       def start(self) -> None:
           """Start the watcher OS thread. No-op when ``timeout_seconds <= 0``."""
           if self._timeout_seconds <= 0:
               return
           self._last_progress = self._clock.now()
           self._fired = False
           self._stop_event.clear()
           self._thread = threading.Thread(
               target=self._run,
               daemon=True,
               name="bus-watchdog",
           )
           self._thread.start()

       def stop(self) -> None:
           """Signal and join the watcher thread (5 s timeout)."""
           self._stop_event.set()
           if self._thread is not None:
               self._thread.join(timeout=5)
               self._thread = None

       # ------------------------------------------------------------------
       # Synchronous liveness check — exposed for testing
       # ------------------------------------------------------------------

       def check_once(self) -> None:
           """Check liveness; fire if overdue. Idempotent after the first fire.

           The OS thread calls this in a polling loop. Tests call it directly
           with an advanced FakeClock — no thread needed for unit tests.
           """
           if self._fired:
               return
           elapsed = (self._clock.now() - self._last_progress).total_seconds()
           if elapsed >= self._timeout_seconds:
               self._fired = True
               log.critical(
                   "WatchdogFired: no progress for %.1fs (threshold=%ds); terminating process",
                   elapsed,
                   self._timeout_seconds,
               )
               self._exit_fn(1)

       # ------------------------------------------------------------------
       # OS thread body
       # ------------------------------------------------------------------

       def _run(self) -> None:
           """Poll until timeout fires or stop_event is set."""
           poll_interval = max(0.5, min(self._timeout_seconds / 3, 10.0))
           while not self._stop_event.wait(timeout=poll_interval):
               self.check_once()
               if self._fired:
                   return


   __all__ = ["Watchdog"]
   ```

3. **`bus/runner.py`**: Add `watchdog_timeout_seconds` to the `BusConfig(...)` constructor call (lines 86–101). Immediately after the `slow_deliver_warn_seconds=float(...)` line and before `supervisor=supervisor`, insert:

   ```python
   watchdog_timeout_seconds=int(
       os.environ.get(
           "BUS_WATCHDOG_TIMEOUT_SECONDS",
           bus_cfg_raw.get("watchdog_timeout_seconds", 90),
       )
   ),
   ```

   No import change needed (`os` is already imported at line 4 of `runner.py`).

4. **`bus/cli.py`**: Two changes.

   **4a. Import `Watchdog`** — add to the existing imports block (after the `from agent_core.bus.runner import ...` line):

   ```python
   from agent_core.bus.watchdog import Watchdog
   ```

   **4b. Update `_run_bus`** — full replacement of the `_run_bus` coroutine body. The only additions are: Watchdog construction before `http_host.start()`, `watchdog.start()` call, `watchdog.heartbeat()` at the top of each loop, and `watchdog.stop()` in the outer `finally`:

   ```python
   async def _run_bus(config_path: Path) -> None:
       bus, http_host = await build_bus_from_config(config_path)
       if http_host is not None:
           await http_host.start()
       watchdog = Watchdog(
           bus.config.watchdog_timeout_seconds,
           heartbeat_path=bus.config.storage_path.parent / "watchdog_heartbeat",
       )
       watchdog.start()
       try:
           await bus.start()

           stop_event = asyncio.Event()

           def _shutdown(*_):
               stop_event.set()

           loop = asyncio.get_running_loop()
           try:
               loop.add_signal_handler(signal.SIGINT, _shutdown)
               loop.add_signal_handler(signal.SIGTERM, _shutdown)
           except NotImplementedError:
               pass  # Windows — SIGINT raises KeyboardInterrupt directly.

           endpoint_count = len(bus._endpoints_by_name)  # pragma: no cover
           host_str = f" + http on :{http_host.port}" if http_host else ""  # pragma: no cover
           console.print(  # pragma: no cover
               f"[green]bus running[/green] — {endpoint_count} endpoint(s){host_str}; "
               "press Ctrl+C to stop."
           )

           async def _ttl_loop():
               while not stop_event.is_set():
                   watchdog.heartbeat()
                   try:
                       await bus.run_ttl_sweep_once()
                   except Exception:
                       log.exception("TTL sweep failed")
                   try:
                       await asyncio.wait_for(
                           stop_event.wait(), timeout=bus.config.ttl_sweep_seconds
                       )
                   except TimeoutError:
                       pass

           async def _redelivery_loop():
               while not stop_event.is_set():
                   watchdog.heartbeat()
                   try:
                       await bus.run_redelivery_sweep_once()
                   except Exception:
                       log.exception("redelivery sweep failed")
                   try:
                       await bus.run_supervisor_tick_once()
                   except Exception:
                       log.exception("supervisor tick failed")
                   try:
                       await asyncio.wait_for(
                           stop_event.wait(), timeout=bus.config.redelivery_sweep_seconds
                       )
                   except TimeoutError:
                       pass

           sweeps = [asyncio.create_task(_ttl_loop()), asyncio.create_task(_redelivery_loop())]
           try:
               await stop_event.wait()
           except KeyboardInterrupt:
               stop_event.set()
           finally:
               for t in sweeps:
                   t.cancel()
               await asyncio.gather(*sweeps, return_exceptions=True)
               await bus.stop()
       finally:
           watchdog.stop()
           if http_host is not None:
               await http_host.stop()
           console.print("[yellow]bus stopped[/yellow]")
   ```

   **4c. Update `_status`** — add heartbeat-freshness section after the degraded-endpoints block (inside the existing `try` body, before `finally: await store.close()`). The `datetime` import is already present at line 9 of `cli.py` (`from datetime import UTC, datetime, timedelta`):

   ```python
       # Heartbeat freshness (watchdog_heartbeat file written by _run_bus).
       heartbeat_path = bus.config.storage_path.parent / "watchdog_heartbeat"
       if heartbeat_path.exists():
           try:
               ts_str = heartbeat_path.read_text(encoding="utf-8").strip()
               last_beat = datetime.fromisoformat(ts_str)
               age_s = (datetime.now(UTC) - last_beat).total_seconds()
               console.print(f"last heartbeat: {age_s:.0f}s ago")
           except (ValueError, OSError):
               console.print("last heartbeat: [dim]unreadable[/dim]")
       else:
           console.print(
               "last heartbeat: [dim]no file (bus not running or watchdog disabled)[/dim]"
           )
   ```

5. **Create `packages/core/tests/bus/test_watchdog.py`**:

   ```python
   """Unit tests for bus/watchdog.py (issue #304).

   Covers:
   - check_once() fires exactly once when elapsed >= timeout (FakeClock, no thread)
   - check_once() is idempotent after first fire
   - healthy loop never fires (heartbeat before each check_once)
   - non-positive timeout: start() is a no-op, stop() is safe
   - heartbeat() writes ISO timestamp to the heartbeat file
   - @pytest.mark.slow: thread fires without event-loop involvement (proves off-loop)
   """

   from __future__ import annotations

   import logging
   import threading
   import time
   from datetime import UTC, datetime

   import pytest

   from agent_core.bus.watchdog import Watchdog
   from agent_core.clock import FakeClock

   _START = datetime(2026, 1, 1, tzinfo=UTC)


   # ---------------------------------------------------------------------------
   # FakeClock-based check_once() tests (fast, no threads, no real sleep)
   # ---------------------------------------------------------------------------


   class TestWatchdogCheckOnce:
       def test_fires_when_elapsed_exceeds_timeout(self) -> None:
           exits: list[int] = []
           clock = FakeClock(start=_START)
           wd = Watchdog(60, clock=clock, exit_fn=exits.append)
           wd.heartbeat()
           clock.advance(61)
           wd.check_once()
           assert exits == [1]

       def test_logs_critical_watchdog_fired(self, caplog) -> None:
           clock = FakeClock(start=_START)
           wd = Watchdog(60, clock=clock, exit_fn=lambda _: None)
           wd.heartbeat()
           clock.advance(61)
           with caplog.at_level(logging.CRITICAL, logger="agent_core.bus.watchdog"):
               wd.check_once()
           assert any("WatchdogFired" in r.message for r in caplog.records)

       def test_idempotent_after_first_fire(self) -> None:
           exits: list[int] = []
           clock = FakeClock(start=_START)
           wd = Watchdog(60, clock=clock, exit_fn=exits.append)
           wd.heartbeat()
           clock.advance(61)
           wd.check_once()
           wd.check_once()  # second call — must be a no-op
           wd.check_once()  # third call — must be a no-op
           assert exits == [1]

       def test_does_not_fire_before_threshold(self) -> None:
           exits: list[int] = []
           clock = FakeClock(start=_START)
           wd = Watchdog(60, clock=clock, exit_fn=exits.append)
           wd.heartbeat()
           clock.advance(59)
           wd.check_once()
           assert exits == []

       def test_fires_at_exactly_threshold(self) -> None:
           exits: list[int] = []
           clock = FakeClock(start=_START)
           wd = Watchdog(60, clock=clock, exit_fn=exits.append)
           wd.heartbeat()
           clock.advance(60)
           wd.check_once()
           assert exits == [1]


   # ---------------------------------------------------------------------------
   # Healthy-loop tests — heartbeat before each check_once, never fires
   # ---------------------------------------------------------------------------


   class TestWatchdogHeartbeat:
       def test_healthy_loop_never_fires(self) -> None:
           """Heartbeat resets the timer; advancing clock but re-heartbeating never fires."""
           exits: list[int] = []
           clock = FakeClock(start=_START)
           wd = Watchdog(60, clock=clock, exit_fn=exits.append)
           for _ in range(10):
               wd.heartbeat()
               clock.advance(30)  # advance to 30s within window
               wd.check_once()   # should NOT fire (only 30s since last beat)
           assert exits == []

       def test_heartbeat_writes_iso_timestamp_to_file(self, tmp_path) -> None:
           clock = FakeClock(start=_START)
           hb_file = tmp_path / "watchdog_heartbeat"
           wd = Watchdog(60, clock=clock, heartbeat_path=hb_file)
           wd.heartbeat()
           assert hb_file.exists()
           content = hb_file.read_text(encoding="utf-8").strip()
           assert content == _START.isoformat()

       def test_heartbeat_overwrites_file_on_second_call(self, tmp_path) -> None:
           clock = FakeClock(start=_START)
           hb_file = tmp_path / "watchdog_heartbeat"
           wd = Watchdog(60, clock=clock, heartbeat_path=hb_file)
           wd.heartbeat()
           clock.advance(10)
           wd.heartbeat()
           content = hb_file.read_text(encoding="utf-8").strip()
           # should contain the advanced timestamp, not _START
           assert content != _START.isoformat()


   # ---------------------------------------------------------------------------
   # Disabled-watchdog tests (timeout_seconds <= 0)
   # ---------------------------------------------------------------------------


   class TestWatchdogDisabled:
       def test_zero_timeout_does_not_start_thread(self) -> None:
           wd = Watchdog(0, exit_fn=lambda _: None)
           wd.start()
           assert wd._thread is None

       def test_negative_timeout_also_disabled(self) -> None:
           wd = Watchdog(-1, exit_fn=lambda _: None)
           wd.start()
           assert wd._thread is None

       def test_stop_on_disabled_watchdog_is_safe(self) -> None:
           wd = Watchdog(0, exit_fn=lambda _: None)
           wd.start()
           wd.stop()  # must not raise


   # ---------------------------------------------------------------------------
   # OS-thread tests (real time; excluded from fast suite)
   # ---------------------------------------------------------------------------


   @pytest.mark.slow
   class TestWatchdogThread:
       def test_thread_fires_without_heartbeat(self) -> None:
           """Thread fires with no asyncio involvement — proves the off-loop design.

           Timeout 1 s; poll_interval = max(0.5, min(1/3, 10)) = 0.5 s.
           First poll at ~0.5 s: 0.5 s elapsed >= 1 s? No.
           Second poll at ~1.0 s: 1.0 s elapsed >= 1 s? Yes → fire.
           Wait up to 5 s for the fired_event to confirm.
           """
           exits: list[int] = []
           fired_event = threading.Event()

           def _recording_exit(code: int) -> None:
               exits.append(code)
               fired_event.set()

           wd = Watchdog(1, exit_fn=_recording_exit)
           wd.start()
           # No heartbeat() — the watchdog should fire on its own
           fired = fired_event.wait(timeout=5)
           wd.stop()
           assert fired, "watchdog thread did not fire within 5 s"
           assert exits == [1]

       def test_thread_does_not_fire_with_regular_heartbeats(self) -> None:
           """Thread stays quiet while heartbeats are frequent relative to timeout."""
           exits: list[int] = []
           wd = Watchdog(10, exit_fn=exits.append)  # 10 s threshold
           wd.start()
           # Pump heartbeats every 50 ms for 300 ms total — well within the 10 s threshold
           for _ in range(6):
               wd.heartbeat()
               time.sleep(0.05)
           wd.stop()
           assert exits == []
   ```

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/core.py` | Append `watchdog_timeout_seconds: int = 90` field to `BusConfig` after `slow_deliver_warn_seconds` |
| `packages/core/src/agent_core/bus/watchdog.py` | **New file**: `_sd_notify_watchdog()` function; `Watchdog` class with `heartbeat()`, `start()`, `stop()`, `check_once()`, `_run()` |
| `packages/core/src/agent_core/bus/runner.py` | Add `watchdog_timeout_seconds=int(os.environ.get("BUS_WATCHDOG_TIMEOUT_SECONDS", bus_cfg_raw.get("watchdog_timeout_seconds", 90)))` to `BusConfig(...)` constructor call |
| `packages/core/src/agent_core/bus/cli.py` | Add `from agent_core.bus.watchdog import Watchdog`; wire `Watchdog` construction + `start()`/`stop()` into `_run_bus`; add `watchdog.heartbeat()` to both sweep loops; add heartbeat-age display to `_status` |
| `packages/core/tests/bus/test_watchdog.py` | **New file**: 11 FakeClock unit tests + 2 `@pytest.mark.slow` OS-thread tests |

No other files change. `core.py`, `supervisor.py`, `handle.py`, `persistence.py`, and all endpoint packages are untouched.

## Alternatives considered

1. **asyncio task instead of OS thread**: A `asyncio.create_task(_watchdog_loop())` would be simpler to write, but a task blocked on the same event loop is suppressed by the very condition we want to detect — a wedged loop cannot advance any task, including the watchdog. Ruled out as fundamentally wrong for the hung-loop class.

2. **`signal.alarm` (SIGALRM)**: POSIX only (`SIGALRM` not available on Windows); sends the signal to the process and requires a signal handler. Ruled out: cross-platform requirement excludes it, and signal handlers have significant re-entrancy restrictions.

3. **Separate supervisor process (OS-level watchdog)**: A separate process `pgrep`-ing the daemon and checking heartbeat mtime. Ruled out for this ticket — that is Theme B's B-2/B-3 work (OS-dispatch layer + Windows Service). B-1 is in-process and portable; the separate process layer is layered on top of it.

4. **Expose `watchdog_timeout_seconds` as its own top-level YAML key** (not under `bus:`): Inconsistent with every other bus config knob (`slow_deliver_warn_seconds`, `ttl_sweep_seconds`, etc.), which live under `bus:`. Ruled out for consistency.

## Open questions

1. The referenced design spec (`docs/superpowers/specs/2026-07-14-portable-supervisor-lifecycle-design.md`) was not found in the worktree. This spec is derived from the issue body alone, which is detailed and internally consistent. If the design spec contains constraints not reproduced in the issue body, the Worker should read it once available and flag conflicts.

2. The heartbeat file path (`storage_path.parent / "watchdog_heartbeat"`) is implicitly coupled to `BusConfig.storage_path`. If an operator relocates `storage_path` to a non-standard directory, the heartbeat file moves with it, which `daemon status` (reading `daemon.pid` at `home_for(inst)`) would not know about. If the Theme E tray-icon work needs a fixed-location file, a `BusConfig.heartbeat_path` field can be added then. No action needed for this ticket.

## Out of scope

- OS-level process restart supervision (B-2 OS-dispatch layer, B-3 Windows Service) — sibling tickets of #304 under #265
- Making `_status` read the heartbeat path from the daemon home instead of `storage_path.parent` — sufficient for this ticket; refine when Theme E consumes the signal
- Exposing the watchdog as a field in the persistent `supervisor_state` SQLite table — Theme E work
- tray-icon integration or OS toast on watchdog fire
- macOS `launchd` `KeepAlive` / Windows SCM `FailureActions` — those belong to B-2/B-3
- Watchdog integration with `EndpointSupervisor` — `EndpointSupervisor` handles per-endpoint failures (T3/T4); this ticket handles whole-loop wedge detection only
