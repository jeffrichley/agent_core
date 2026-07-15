"""Unit tests for bus/watchdog.py (issue #304).

Covers:
- check_once() fires exactly once when elapsed >= timeout (FakeClock, no thread)
- check_once() is idempotent after first fire
- healthy loop never fires (heartbeat before each check_once)
- non-positive timeout: start() is a no-op, stop() is safe
- heartbeat() writes ISO timestamp to the heartbeat file
- _sd_notify_watchdog() sends to NOTIFY_SOCKET and swallows errors
- start() with positive timeout creates an OS thread; stop() joins and clears it
- @pytest.mark.slow: thread fires without event-loop involvement (proves off-loop)
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from datetime import UTC, datetime

import pytest

from agent_core.bus.watchdog import Watchdog, _sd_notify_watchdog
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
            wd.check_once()  # should NOT fire (only 30s since last beat)
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

    def test_heartbeat_ignores_oserror_when_path_is_directory(self, tmp_path) -> None:
        """OSError during file write is silently swallowed (covers watchdog.py:105-106)."""
        clock = FakeClock(start=_START)
        # Create hb_path as a *directory* so write_text() raises IsADirectoryError (OSError).
        hb_dir = tmp_path / "hb"
        hb_dir.mkdir()
        wd = Watchdog(60, clock=clock, heartbeat_path=hb_dir)
        wd.heartbeat()  # must not raise


# ---------------------------------------------------------------------------
# _sd_notify_watchdog() tests
# ---------------------------------------------------------------------------


class TestSdNotifyWatchdog:
    """Tests for the _sd_notify_watchdog() helper (covers watchdog.py:52-57)."""

    def test_no_op_when_notify_socket_not_set(self, monkeypatch) -> None:
        """No NOTIFY_SOCKET → function returns silently without touching sockets."""
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        _sd_notify_watchdog()  # must not raise

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="AF_UNIX / systemd NOTIFY_SOCKET is Unix-only; the helper is a no-op on Windows",
    )
    def test_sends_watchdog_notification_to_real_socket(self, tmp_path, monkeypatch) -> None:
        """Happy-path: NOTIFY_SOCKET is a valid DGRAM socket → WATCHDOG=1 is delivered."""
        sock_path = str(tmp_path / "notify.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(sock_path)
            server.setblocking(False)
            monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
            _sd_notify_watchdog()
            data = server.recv(1024)
        assert data == b"WATCHDOG=1\n"

    def test_swallows_oserror_when_socket_path_invalid(self, monkeypatch, tmp_path) -> None:
        """NOTIFY_SOCKET set to a non-existent path → OSError is swallowed silently."""
        monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "no_such.sock"))
        _sd_notify_watchdog()  # must not raise


# ---------------------------------------------------------------------------
# start() / stop() with a real OS thread (fast — thread exits immediately)
# ---------------------------------------------------------------------------


class TestWatchdogStartStop:
    """Tests that start() creates a thread and stop() joins it (covers watchdog.py:113-128)."""

    def test_start_creates_thread_and_stop_joins_it(self) -> None:
        """start() with positive timeout creates the OS thread; stop() clears it."""
        wd = Watchdog(1000, exit_fn=lambda _: None)
        assert wd._thread is None
        wd.start()
        assert wd._thread is not None
        # stop() sets the stop_event — the thread's wait() returns immediately and exits.
        wd.stop()
        assert wd._thread is None

    def test_run_exits_immediately_when_stop_event_is_preset(self) -> None:
        """Calling _run() with stop_event already set returns without iterating the loop."""
        wd = Watchdog(60, exit_fn=lambda _: None)
        wd._stop_event.set()
        wd._run()  # must return immediately (no sleep) — covers watchdog.py:158-159


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
