"""Disposable test backend for the #91 subprocess regression.

Run as: ``python _backend_server.py <port>``. Serves ONE throwaway
``ClaudeCodeMCPEndpoint`` via the production ``HTTPHost`` on
``127.0.0.1:<port>`` until the process is killed. This is NOT shipped
(lives under tests/) and never touches the real daemon — the regression
always passes an OS-assigned free port and kills only this child.
"""

from __future__ import annotations

import asyncio
import sys

from agent_core.bus.http_host import HTTPHost
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...
    async def publish(self, envelope, to=None) -> None: ...
    async def nack(self, envelope_id, requeue=True) -> None: ...
    def endpoints(self) -> list:
        return [type("E", (), {"name": "discord", "description": "d"})()]


async def _main(port: int) -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    await ep.start(_StubHandle())  # type: ignore[arg-type]
    host = HTTPHost(bind_host="127.0.0.1", bind_port=port)
    host.mount(ep)
    await host.start()
    # Readiness is observed by the parent via a TCP port-probe (this
    # process's stdout is discarded), so nothing to emit here.
    # Serve until the parent kills this process (faithful daemon death).
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main(int(sys.argv[1])))
