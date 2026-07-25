"""ASGI authentication middleware for /mcp/<being>/ paths (Dβ-2b).

Implements the three-mode bus_auth_mode policy:
  off     — pass all requests through without inspection.
  warn    — verify if bearer present; log unauthenticated; never reject.
  enforce — require a valid bearer; reject (401) on missing/invalid token.

Identity binding: the JWT sub claim must equal the being named in the path.
A valid token for being A is rejected at being B's path (Decision 2 of the
bus transport auth design).
"""

from __future__ import annotations

import logging
import re

from agent_core.bus.auth.pubkey_registry import PubkeyRegistry
from agent_core.bus.auth.verify import verify_bearer_jwt

log = logging.getLogger(__name__)

# Matches /mcp/<being> or /mcp/<being>/...  — captures the being name.
_MCP_PATH_RE = re.compile(r"^/mcp/([^/]+)")


def _extract_being(path: str) -> str | None:
    """Return the being name from an /mcp/<being>/... path, or None."""
    m = _MCP_PATH_RE.match(path)
    return m.group(1) if m else None


def _extract_bearer(headers: list) -> str | None:
    """Return the Bearer token from the ASGI headers list, or None."""
    for name, value in headers:
        if name.lower() == b"authorization":
            decoded = value.decode("latin-1", errors="replace")
            lower = decoded.lower()
            if lower.startswith("bearer "):
                return decoded[7:].strip()
    return None


async def _send_401(send) -> None:
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            [b"content-type", b"application/json"],
            [b"www-authenticate", b'Bearer error="unauthorized"'],
        ],
    })
    await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})


class BusAuthMiddleware:
    """ASGI middleware that enforces bus_auth_mode on /mcp/<being>/ paths."""

    def __init__(
        self,
        app,
        *,
        pubkey_registry: PubkeyRegistry | None,
        bus_auth_mode: str,
    ) -> None:
        self._app = app
        self._registry = pubkey_registry
        self._mode = bus_auth_mode

    async def __call__(self, scope, receive, send) -> None:
        # Non-HTTP scopes (lifespan, websocket) and off mode bypass auth.
        if scope["type"] != "http" or self._mode == "off":
            await self._app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        being = _extract_being(path)
        if being is None:
            # Not an /mcp/<being>/ path — pass through.
            await self._app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        token = _extract_bearer(headers)

        if self._mode == "warn":
            if token is None:
                log.warning(
                    "bus auth: unauthenticated request to /mcp/%s/ — warn mode, passing",
                    being,
                )
                await self._app(scope, receive, send)
                return
            # Bearer present in warn mode: verify, but always pass through.
            self._verify(token, being)  # logs on failure; result is advisory only
            await self._app(scope, receive, send)

        else:  # enforce
            if token is None:
                log.warning("bus auth: missing bearer for /mcp/%s/ — 401", being)
                await _send_401(send)
                return
            if not self._verify(token, being):
                log.warning("bus auth: token rejected for /mcp/%s/ — 401", being)
                await _send_401(send)
                return
            await self._app(scope, receive, send)

    def _verify(self, token: str, being: str) -> bool:
        """Verify the token against the being's registered public key."""
        if self._registry is None:
            log.warning("bus auth: no pubkey registry — cannot verify token for %r", being)
            return False
        pubkey = self._registry.lookup(being)
        if pubkey is None:
            log.warning(
                "bus auth: no public key registered for being %r — treating as unauthenticated",
                being,
            )
            return False
        return verify_bearer_jwt(token, public_key=pubkey, expected_sub=being)
