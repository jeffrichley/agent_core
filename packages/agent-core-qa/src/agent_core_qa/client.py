"""DaemonClient — thin HTTP wrapper for the agent_core daemon.

Tool-shaped: stateless across calls, no connection pooling beyond what
httpx does internally, no retry, no session caching.
"""

from __future__ import annotations

import socket
import time
import urllib.parse
from typing import Any

import httpx


class _FakeTCPResponse:
    """Mimics a minimal httpx.Response for callers of health_check().

    Used when health_check() falls back to a TCP-connect check because
    the daemon has no dedicated HTTP liveness route. status_code=200
    means the port is accepting connections; status_code=503 means it
    isn't (or the connect timed out).
    """

    def __init__(self, *, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class DaemonClient:
    """HTTP client for the agent_core daemon.

    Targets the daemon's HTTP API (default http://127.0.0.1:8787 for the
    Phase 3.5 test instance). One client per scenario; no shared state.

    Preflight finding: the daemon's HTTPHost (Starlette+Uvicorn) has no
    dedicated liveness HTTP route — it only mounts MCP endpoint paths.
    health_check() therefore uses a TCP-connect check on the bind port
    rather than a GET to a specific path. A successful TCP connect means
    the daemon process is up and its HTTP server is accepting connections.
    """

    def __init__(self, base_url: str, *, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    def health_check(self) -> _FakeTCPResponse:
        """TCP-connect check on the daemon's bind port.

        TCP-connect fallback: the daemon's HTTPHost (Starlette+Uvicorn)
        exposes no dedicated liveness route — only MCP endpoint mounts.
        A successful TCP connect on the bind port indicates the HTTP
        server is accepting connections. Returns a response-like object
        with status_code=200 on success or status_code=503 on failure.
        """
        parsed = urllib.parse.urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        try:
            conn = socket.create_connection((host, port), timeout=self._timeout)
            conn.close()
            return _FakeTCPResponse(status_code=200, text="TCP connect OK")
        except OSError as exc:
            return _FakeTCPResponse(status_code=503, text=str(exc))

    def send_envelope(self, envelope: dict[str, Any]) -> httpx.Response:
        """POST an envelope to the daemon's bus-publish endpoint.

        Implementer: discover the exact route + payload shape via preflight
        on the daemon's HTTP API. Likely something like POST /bus/publish
        with the envelope JSON as body.
        """
        # Path placeholder — implementer verifies via preflight before
        # using send_envelope.
        return httpx.post(
            self.base_url + "/bus/publish",
            json=envelope,
            timeout=self._timeout,
        )

    def poll_envelopes(
        self,
        predicate,
        *,
        timeout: float = 5.0,
        interval: float = 0.1,
    ) -> dict[str, Any] | None:
        """Poll the daemon's bus state for an envelope matching predicate.

        Returns the first match within timeout, else None. Implementer:
        verify the poll path via preflight.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = httpx.get(self.base_url + "/bus/inbox", timeout=interval)
            if resp.status_code == 200:
                for env in resp.json().get("envelopes", []):
                    if predicate(env):
                        return env
            time.sleep(interval)
        return None
