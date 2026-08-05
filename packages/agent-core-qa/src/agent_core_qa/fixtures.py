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

import asyncio
import socket
import subprocess
import sys
import time
from collections.abc import Generator

import pytest

from agent_core_qa.client import DaemonClient

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


async def _qa_endpoint_ready(url: str, *, deadline: float) -> bool:
    """Poll the ``qa`` MCP endpoint until it is genuinely started, or ``deadline`` passes.

    The TCP probe only confirms the daemon's HTTP port is bound —
    Starlette/Uvicorn opens the socket before the daemon's endpoints finish
    their ``start()``. A test that fires a ``send`` tool call into that window
    gets ``500 endpoint 'qa' is not started``.

    **The probe must call a tool that requires the started handle.** This gate
    previously polled ``list_pending`` and accepted any ``200``, on the stated
    reasoning that "a 200 from it means the endpoint is genuinely started and
    serving". That was false: ``_call_list_pending`` never touches ``_handle``
    — it reads the in-memory ``_pending`` list and returns — so it answers
    ``200`` with an empty mailbox whether or not the endpoint has started. The
    gate therefore passed on its first poll every time and protected nothing,
    which is why ``endpoint 'qa' is not started`` kept reaching the tests it
    was written to prevent.

    ``consume`` calls ``_require_handle()`` before doing anything, so it fails
    loudly while the endpoint is unstarted. With ``auto_ack=False`` and
    ``max_items=0`` it acks nothing and drops nothing — a pure read, safe to
    call repeatedly as a probe.
    """
    client = DaemonClient(url)
    while time.monotonic() < deadline:
        result = await client.call_tool(
            "consume",
            arguments={"auto_ack": False, "max_items": 0},
        )
        if result.status_code == 200:
            return True
        await asyncio.sleep(_POLL_INTERVAL)
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
        _run(
            [
                sys.executable,
                "-m",
                "agent_core.cli",
                "daemon",
                "init",
                "--force",
                "--instance",
                "test",
            ]
        )

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

    # TCP-open is necessary but not sufficient: the daemon binds its HTTP port
    # before its endpoints finish starting. Gate on the qa endpoint actually
    # serving tool calls so tests never race a half-started daemon (the
    # `endpoint 'qa' is not started` flake). Runs for BOTH the cold-start and
    # already-live paths.
    endpoint_deadline = time.monotonic() + _POLL_TIMEOUT
    if not asyncio.run(_qa_endpoint_ready(_DAEMON_URL, deadline=endpoint_deadline)):
        raise RuntimeError(
            f"daemon HTTP port {_DAEMON_HOST}:{_DAEMON_PORT} opened but the `qa` "
            f"endpoint did not start serving tool calls within {_POLL_TIMEOUT:.0f} s. "
            "Check ~/.agent-core-test/daemon.log for endpoint startup errors."
        )

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
