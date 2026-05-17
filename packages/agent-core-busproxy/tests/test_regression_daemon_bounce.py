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
from pathlib import Path

import pytest
from fastmcp import Client

from agent_core_busproxy.proxy import build_busproxy

_BACKEND = Path(__file__).parent / "_backend_server.py"


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
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


async def _spawn_backend(port: int) -> subprocess.Popen:
    """Start the disposable backend subprocess and wait until it listens."""
    proc = subprocess.Popen(
        [sys.executable, str(_BACKEND), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):  # ~10s bounded readiness
        if proc.poll() is not None:
            raise RuntimeError(f"backend exited early (rc={proc.returncode})")
        if await _port_open(port):
            return proc
        await asyncio.sleep(0.1)
    proc.kill()
    raise RuntimeError("backend did not become ready within 10s")


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
            for _ in range(40):  # ~20s bounded retry budget
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
