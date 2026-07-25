"""Tests for BusAuthMiddleware (Dβ-2b) — table-driven over off/warn/enforce."""

from __future__ import annotations

import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent_core.bus.auth.middleware import BusAuthMiddleware
from agent_core.bus.auth.pubkey_registry import PubkeyRegistry, build_pubkey_registry
from agent_core.bus.config import EndpointEntryConfig

# ── helpers ──────────────────────────────────────────────────────────────────

def _generate_keypair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    pem = public.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    return private, pem


def _mint_token(private_key, sub: str, exp_offset_seconds: int = 60) -> str:
    return jwt.encode(
        {"sub": sub, "exp": int(time.time()) + exp_offset_seconds},
        private_key,
        algorithm="EdDSA",
    )


def _make_registry(being_to_pem: dict[str, str]) -> PubkeyRegistry:
    entries = [
        EndpointEntryConfig.model_validate(
            {"type": "builtin.stub", "name": name, "pubkey_pem": pem}
        )
        for name, pem in being_to_pem.items()
    ]
    return build_pubkey_registry(entries)


async def _stub_app(scope, receive, send):
    """ASGI app that always returns 200."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _call(middleware, path: str, bearer: str | None = None) -> int:
    """Drive the middleware and return the HTTP response status code."""
    headers = []
    if bearer is not None:
        headers.append([b"authorization", f"Bearer {bearer}".encode()])
    scope = {"type": "http", "path": path, "headers": headers}
    responses: list[dict] = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        responses.append(msg)

    await middleware(scope, receive, send)
    start = next(r for r in responses if r["type"] == "http.response.start")
    return start["status"]


# ── mode: off ────────────────────────────────────────────────────────────────

class TestOffMode:
    def _make(self, registry=None):
        return BusAuthMiddleware(_stub_app, pubkey_registry=registry, bus_auth_mode="off")

    @pytest.mark.asyncio
    async def test_off_no_bearer_passes(self):
        mw = self._make()
        assert await _call(mw, "/mcp/pepper/") == 200

    @pytest.mark.asyncio
    async def test_off_garbage_bearer_still_passes(self):
        mw = self._make()
        assert await _call(mw, "/mcp/pepper/", bearer="not-a-jwt") == 200

    @pytest.mark.asyncio
    async def test_off_non_mcp_path_passes(self):
        mw = self._make()
        assert await _call(mw, "/notify/pepper") == 200


# ── mode: warn ───────────────────────────────────────────────────────────────

class TestWarnMode:
    def _make(self, registry: PubkeyRegistry):
        return BusAuthMiddleware(_stub_app, pubkey_registry=registry, bus_auth_mode="warn")

    @pytest.mark.asyncio
    async def test_warn_no_bearer_passes_and_logs(self, caplog):
        priv, pem = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        with caplog.at_level(logging.WARNING, logger="agent_core.bus.auth.middleware"):
            status = await _call(mw, "/mcp/pepper/")
        assert status == 200
        assert any("unauthenticated" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_warn_valid_token_passes(self):
        priv, pem = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        token = _mint_token(priv, "pepper")
        assert await _call(mw, "/mcp/pepper/", bearer=token) == 200

    @pytest.mark.asyncio
    async def test_warn_invalid_signature_passes_and_logs(self, caplog):
        priv, pem = _generate_keypair()
        other_priv, _ = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        bad_token = _mint_token(other_priv, "pepper")  # signed with wrong key
        with caplog.at_level(logging.WARNING, logger="agent_core.bus.auth.verify"):
            status = await _call(mw, "/mcp/pepper/", bearer=bad_token)
        assert status == 200
        assert any(caplog.records)  # warning was emitted

    @pytest.mark.asyncio
    async def test_warn_expired_token_passes_and_logs(self, caplog):
        priv, pem = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        expired_token = _mint_token(priv, "pepper", exp_offset_seconds=-10)
        with caplog.at_level(logging.WARNING, logger="agent_core.bus.auth.verify"):
            status = await _call(mw, "/mcp/pepper/", bearer=expired_token)
        assert status == 200
        assert any("expired" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_warn_sub_mismatch_passes_and_logs(self, caplog):
        """Valid token for pepper at wren's path — warn mode passes."""
        priv_p, pem_p = _generate_keypair()
        priv_w, pem_w = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem_p, "wren": pem_w}))
        pepper_token = _mint_token(priv_p, "pepper")
        with caplog.at_level(logging.WARNING):
            status = await _call(mw, "/mcp/wren/", bearer=pepper_token)
        assert status == 200
        assert any(caplog.records)  # warning was emitted by verify_bearer_jwt

    @pytest.mark.asyncio
    async def test_warn_no_registered_key_passes_and_logs(self, caplog):
        mw = self._make(_make_registry({}))  # empty registry
        with caplog.at_level(logging.WARNING, logger="agent_core.bus.auth.middleware"):
            status = await _call(mw, "/mcp/pepper/", bearer="something")
        assert status == 200
        assert any("no public key" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_warn_non_mcp_path_passes_without_logging(self, caplog):
        mw = self._make(_make_registry({}))
        with caplog.at_level(logging.WARNING):
            status = await _call(mw, "/notify/pepper")
        assert status == 200
        assert not caplog.records


# ── mode: enforce ────────────────────────────────────────────────────────────

class TestEnforceMode:
    def _make(self, registry: PubkeyRegistry):
        return BusAuthMiddleware(_stub_app, pubkey_registry=registry, bus_auth_mode="enforce")

    @pytest.mark.asyncio
    async def test_enforce_no_bearer_returns_401(self):
        priv, pem = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        assert await _call(mw, "/mcp/pepper/") == 401

    @pytest.mark.asyncio
    async def test_enforce_valid_token_passes(self):
        priv, pem = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        token = _mint_token(priv, "pepper")
        assert await _call(mw, "/mcp/pepper/", bearer=token) == 200

    @pytest.mark.asyncio
    async def test_enforce_invalid_signature_returns_401(self):
        priv, pem = _generate_keypair()
        other_priv, _ = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        bad_token = _mint_token(other_priv, "pepper")
        assert await _call(mw, "/mcp/pepper/", bearer=bad_token) == 401

    @pytest.mark.asyncio
    async def test_enforce_expired_token_returns_401(self):
        priv, pem = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        expired = _mint_token(priv, "pepper", exp_offset_seconds=-10)
        assert await _call(mw, "/mcp/pepper/", bearer=expired) == 401

    @pytest.mark.asyncio
    async def test_enforce_valid_token_for_being_a_rejected_at_being_b_path(self):
        """The explicit cross-being path-identity test required by the issue."""
        priv_p, pem_p = _generate_keypair()
        priv_w, pem_w = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem_p, "wren": pem_w}))
        # Mint a valid token for pepper (signed with pepper's private key, sub="pepper").
        pepper_token = _mint_token(priv_p, "pepper")
        # Present it at wren's path — must be rejected.
        assert await _call(mw, "/mcp/wren/", bearer=pepper_token) == 401

    @pytest.mark.asyncio
    async def test_enforce_no_registered_key_returns_401(self):
        mw = self._make(_make_registry({}))  # no keys registered
        assert await _call(mw, "/mcp/pepper/", bearer="any-token") == 401

    @pytest.mark.asyncio
    async def test_enforce_non_mcp_path_passes(self):
        """Non-MCP paths (/notify/<agent>) are never gated."""
        mw = self._make(_make_registry({}))
        assert await _call(mw, "/notify/pepper") == 200

    @pytest.mark.asyncio
    async def test_enforce_garbage_bearer_returns_401(self):
        priv, pem = _generate_keypair()
        mw = self._make(_make_registry({"pepper": pem}))
        assert await _call(mw, "/mcp/pepper/", bearer="not.a.jwt") == 401


# ── lifespan passthrough ─────────────────────────────────────────────────────

class TestNonHttpPassthrough:
    @pytest.mark.asyncio
    async def test_lifespan_scope_bypasses_auth(self):
        """Lifespan scopes must not be inspected — auth is HTTP-only."""
        events: list[dict] = []

        async def _lifespan_app(scope, receive, send):
            events.append(scope)

        mw = BusAuthMiddleware(
            _lifespan_app, pubkey_registry=None, bus_auth_mode="enforce"
        )
        await mw({"type": "lifespan"}, lambda: None, lambda m: None)
        assert events  # inner app was called
