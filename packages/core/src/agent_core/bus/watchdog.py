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
