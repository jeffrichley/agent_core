"""Session-scoped pytest fixture for auto-starting the test-instance daemon.

Provides `source_daemon` — a session-scoped fixture that starts the `test`
instance daemon from the source workspace, health-polls until ready, yields
the daemon URL, and kills the process tree on teardown.

Track A reuse pattern (issue #394): a published-install gate can load this
fixture with `pytest_plugins = ["agent_core_qa.fixtures"]` in its own
conftest and wire a different autouse wrapper without coupling the two suites.

Design per spec §B3:
  - Idempotent start: TCP-probe port 8787 first; if already live (developer
    manually running), skip start AND skip teardown kill.
  - Always runs `daemon init --force --instance test` when port is cold so
    the config is regenerated from the current template on every cold start.
  - Health-polls 127.0.0.1:8787 with 0.2 s interval, up to 30 s.
  - Teardown via `agent-core daemon stop --instance test` (calls kill_tree
    internally — no orphaned child processes).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Generator

import pytest

_DAEMON_HOST = "127.0.0.1"
_DAEMON_PORT = 8787
_DAEMON_URL = f"http://{_DAEMON_HOST}:{_DAEMON_PORT}"

_POLL_INTERVAL = 0.2  # seconds between liveness probes
_POLL_TIMEOUT = 30.0  # seconds before fixture fails with a diagnostic


def _tcp_probe(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """Return True if host:port accepts a TCP connection within `timeout` seconds."""
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
        conn.close()
        return True
    except OSError:
        return False


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command, capturing output, raising on non-zero exit."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture(scope="session")
def source_daemon() -> Generator[str, None, None]:
    """Session-scoped fixture: auto-start the test-instance daemon from source.

    Idempotent: if port 8787 is already accepting connections (developer has a
    daemon running), the fixture skips the start and, crucially, skips the
    teardown kill — it never pulls the rug from under a manually-started daemon.

    When the port is cold, the fixture:
      1. Runs `agent-core daemon init --force --instance test` to regenerate
         `~/.agent-core-test/agent_core.yaml` from the current config template.
         The `--force` flag overwrites any existing config, so repeated local
         `pytest` invocations never fail because the config file already exists.
      2. Runs `agent-core daemon start --instance test` (fire-and-forget; the
         daemon process runs detached after writing its PID file).
      3. Polls TCP on 127.0.0.1:8787 at 0.2 s intervals up to 30 s, then
         raises RuntimeError with a diagnostic message if the port never opens.

    Teardown (only when the fixture started the daemon):
      - Runs `agent-core daemon stop --instance test`, which internally calls
        `kill_tree(pid)` (psutil recursive kill + wait_procs) so no orphaned
        child processes leak after the session.

    Yields the daemon base URL ``http://127.0.0.1:8787`` so dependents can
    pass it to DaemonClient or use it directly.
    """
    already_live = _tcp_probe(_DAEMON_HOST, _DAEMON_PORT)

    if not already_live:
        # Regenerate the config from the current template (--force overwrites).
        _run([sys.executable, "-m", "agent_core.cli", "daemon", "init", "--force", "--instance", "test"])

        # Start the daemon (detached; returns immediately after writing PID).
        _run([sys.executable, "-m", "agent_core.cli", "daemon", "start", "--instance", "test"])

        # Health-poll: wait until port 8787 accepts connections.
        deadline = time.monotonic() + _POLL_TIMEOUT
        while not _tcp_probe(_DAEMON_HOST, _DAEMON_PORT):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"test daemon did not become ready on {_DAEMON_HOST}:{_DAEMON_PORT} "
                    f"within {_POLL_TIMEOUT:.0f} s after `agent-core daemon start --instance test`. "
                    "Check ~/.agent-core-test/daemon.log for details."
                )
            time.sleep(_POLL_INTERVAL)

    try:
        yield _DAEMON_URL
    finally:
        if not already_live:
            # Stop only if this fixture started the daemon.
            subprocess.run(
                [sys.executable, "-m", "agent_core.cli", "daemon", "stop", "--instance", "test"],
                capture_output=True,
                text=True,
                check=False,  # idempotent: don't raise if already stopped
            )
