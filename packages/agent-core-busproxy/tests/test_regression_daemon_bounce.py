"""#91 regression: a daemon restart must not strand a live session.

A single long-lived busproxy Client stays connected across a full
daemon bounce. Because every tool call mints a fresh backend session,
the post-restart call recovers without any client re-initialize.

The backend runs as a real SEPARATE PROCESS on an OS-assigned free port
(never the real daemon's port) so killing it and starting a fresh one is
a faithful daemon bounce (process dies → new process binds the port),
free of in-process event-loop/lifespan races. The test only ever kills
its own child process.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from agent_core_busproxy.proxy import build_busproxy
from fastmcp import Client

_BACKEND = Path(__file__).parent / "_backend_server.py"

# Both tests spawn real backend subprocesses (cold Python + fastmcp import +
# socket bind) — slow by nature, so keep them out of the default fast lane.
# See the `slow` marker definition in pyproject.toml.
pytestmark = pytest.mark.slow


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _port_open(port: int) -> bool:
    try:
        fut = asyncio.open_connection("127.0.0.1", port)
        _r, w = await asyncio.wait_for(fut, timeout=1.0)
    except (TimeoutError, OSError):
        return False
    w.close()
    return True


async def _spawn_backend(port: int, *, script: Path = _BACKEND) -> subprocess.Popen:
    """Start the disposable backend subprocess and wait until it listens.

    Readiness budget is generous (30s) because cold Python + fastmcp
    import + bind is slow on a loaded host — e.g. CI under xdist, or this
    suite running inside a resource-contended container (the foreman
    daemon's worker check). The old 10s budget passed on a dedicated
    runner but flaked under contention, where bind routinely took ~8-10s.

    stderr is captured (not discarded) so a genuine startup *failure*
    surfaces in the raised error instead of a blind "did not become
    ready" — the original DEVNULL hid the difference between slow-bind
    and crash-on-start.
    """
    stderr = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [sys.executable, str(script), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=stderr,
    )

    def _captured_stderr() -> str:
        try:
            stderr.seek(0)
            return stderr.read().decode("utf-8", "replace").strip() or "<empty>"
        except Exception:  # pragma: no cover - diagnostic best-effort
            return "<unavailable>"

    for _ in range(300):  # ~30s bounded readiness (load-tolerant)
        if proc.poll() is not None:
            raise RuntimeError(
                f"backend exited early (rc={proc.returncode}); "
                f"stderr:\n{_captured_stderr()}"
            )
        if await _port_open(port):
            return proc
        await asyncio.sleep(0.1)
    proc.kill()
    raise RuntimeError(
        f"backend did not become ready within 30s; stderr:\n{_captured_stderr()}"
    )


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_spawn_backend_surfaces_crash_with_stderr(tmp_path: Path) -> None:
    """A crash-on-start must raise with the cause named AND the stderr text.

    This is the diagnostic the DEVNULL -> TemporaryFile change exists to
    provide: the old code blindly reported "did not become ready" whether the
    backend was slow or dead. A backend that writes to stderr and exits
    non-zero should surface both the exit code and that stderr in the error.
    """
    crasher = tmp_path / "crasher.py"
    crasher.write_text(
        "import sys\n"
        "sys.stderr.write('boom: backend refused to start\\n')\n"
        "sys.exit(3)\n"
    )
    with pytest.raises(RuntimeError) as excinfo:
        await _spawn_backend(_free_port(), script=crasher)
    message = str(excinfo.value)
    assert "exited early" in message
    assert "rc=3" in message
    assert "boom: backend refused to start" in message


# The two internal readiness budgets (spawn #1 + recovery spawn #2) sum to
# ~60s worst-case, which would collide with the global 60s pytest-timeout and
# reintroduce the very flake this test guards against. Give a per-test ceiling
# with real headroom over the worst-case internal sum + spawn/teardown overhead.
@pytest.mark.timeout(150)
@pytest.mark.asyncio
async def test_session_survives_backend_bounce() -> None:
    port = _free_port()
    proxy = build_busproxy(agent="agent", daemon_url=f"http://127.0.0.1:{port}")

    backend = await _spawn_backend(port)  # daemon #1 (own child, own port)
    second: subprocess.Popen | None = None
    try:
        async with Client(proxy) as client:  # one long-lived client
            r1 = await client.call_tool("list_endpoints", {})
            assert any(e["name"] == "discord" for e in r1.data)

            _kill(backend)  # real daemon bounce: process dies
            backend = None  # type: ignore[assignment]

            # Daemon-down window: SAME client, no re-handshake → transient.
            mid = await client.call_tool("list_endpoints", {})
            assert mid.structured_content["transient"] is True

            # Fresh daemon process on the SAME port (OS released it on the
            # kill above — no in-process race). The spec contract is
            # fail-fast + the agent RETRIES on {transient}, so model that:
            # poll the same client until it recovers, bounded. #91 is the
            # property that the session *eventually recovers* across the
            # bounce with no re-handshake.
            second = await _spawn_backend(port)
            recovered = False
            for _ in range(60):  # ~30s bounded retry budget (load-tolerant)
                try:
                    res = await client.call_tool("list_endpoints", {})
                except Exception:
                    await asyncio.sleep(0.5)
                    continue
                sc = res.structured_content
                if isinstance(sc, dict) and sc.get("transient"):
                    await asyncio.sleep(0.5)
                    continue
                assert any(e["name"] == "discord" for e in res.data)
                recovered = True
                break
            assert recovered, "session did not recover after daemon restart"
    finally:
        if backend is not None:
            _kill(backend)
        if second is not None:
            _kill(second)
