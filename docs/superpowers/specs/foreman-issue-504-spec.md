# Spec: ASGI verify middleware + bus_auth_mode (off/warn/enforce) + path-identity binding (issue #504)

## Goal

Implement the ASGI authentication middleware for the bus HTTP host (`http_host.py`) that enforces per-request JWT verification on `/mcp/<being>/` paths according to a three-mode `bus_auth_mode` config flag (`off` / `warn` / `enforce`). This is Dβ-2b in the bus transport auth cluster (`docs/superpowers/specs/2026-07-15-bus-transport-auth-design.md`), building directly on the `PubkeyRegistry` scaffolding delivered by Dβ-2a (issue #503, already merged). On completion, valid requests carry a signed Ed25519/EdDSA JWT whose `sub` claim must match the being named in the URL path; a valid token for being A is rejected at being B's path.

## Acceptance criteria

- `DaemonConfig` in `packages/core/src/agent_core/bus/config.py` has a top-level `bus_auth_mode: Literal["off", "warn", "enforce"] = "off"` field. Existing YAML without the field continues to validate (default `"off"`). A typo'd value (e.g. `"enforced"`) raises `pydantic.ValidationError` at boot.
- `packages/core/src/agent_core/bus/auth/verify.py` exists and exports `verify_bearer_jwt(token: str, *, public_key: Ed25519PublicKey, expected_sub: str) -> bool`. It verifies the EdDSA JWT signature, that `exp` has not passed, and that `sub == expected_sub`; logs a WARNING on each failure type; returns `True` on success, `False` on any failure.
- `packages/core/src/agent_core/bus/auth/middleware.py` exists and exports `BusAuthMiddleware`, an ASGI middleware class whose constructor accepts `(app, *, pubkey_registry: PubkeyRegistry | None, bus_auth_mode: str)`.
- `BusAuthMiddleware` pass-through semantics for `scope["type"] != "http"` (lifespan etc.) and for non-`/mcp/<being>/` paths (e.g. `/notify/pepper`).
- **`off` mode**: all requests pass through without inspection.
- **`warn` mode**:
  - No `Authorization: Bearer` header → log WARNING "unauthenticated" + pass through (200).
  - Bearer present, verification succeeds → pass through (200).
  - Bearer present, verification fails (any reason: bad sig, expired, sub mismatch, no registered key) → log WARNING with reason + pass through (200).
- **`enforce` mode**:
  - No `Authorization: Bearer` header → 401.
  - Bearer present, verification succeeds → pass through (200).
  - Bearer present, verification fails (any reason) → 401.
- **Path-identity binding test** (explicitly required): a valid JWT signed for being `pepper` (sub="pepper") presented at `/mcp/wren/` returns 401 in enforce mode (and logs + passes in warn mode).
- `HTTPHost.__init__` in `packages/core/src/agent_core/bus/http_host.py` accepts `bus_auth_mode: str = "off"` and stores it as `self._bus_auth_mode`. `HTTPHost.start()` wraps the inner router with `BusAuthMiddleware` (using `self._bus_auth_mode` and `self._pubkey_registry`) so path normalization runs first, then auth, then routing.
- `build_bus_from_config` in `packages/core/src/agent_core/bus/runner.py` passes `bus_auth_mode=daemon_cfg.bus_auth_mode` to `HTTPHost(...)`.
- The hardcoded `has_auth_hook = False` at `runner.py:167` becomes `has_auth_hook = daemon_cfg.bus_auth_mode != "off"`, so `_validate_http` permits a non-loopback bind only when auth is active.
- `"PyJWT[crypto]>=2.8"` is listed as a direct dependency in `packages/core/pyproject.toml`.
- Table-driven unit tests in `packages/core/tests/bus/test_bus_auth_middleware.py` cover all three modes, including: off passes everything; warn passes unauthenticated but logs; warn passes with bad token but logs; enforce rejects missing bearer; enforce rejects expired token; enforce rejects wrong signature; enforce rejects valid token for wrong being (path-identity); enforce accepts a valid token at its own being's path.
- `just check` exits 0 on the resulting branch.

## Approach

**Pattern:** This is the ASGI **Decorator** pattern (also readable as Chain of Responsibility): `BusAuthMiddleware` wraps the inner Starlette `Router` and intercepts HTTP requests to MCP paths before they reach endpoint handlers. The design exactly matches the `MCPAuditMiddleware` philosophy already in the codebase: a thin, side-effect-free wrapper that inspects the request, decides pass/reject, and delegates to the next layer.

**Why the auth middleware sits inside `_app`'s path-normalization step.** The `_app` closure in `HTTPHost.start()` already normalizes bare mount paths (adding a trailing `/` when the path is in `mount_prefixes`). The middleware needs the path AFTER normalization so that a request to `/mcp/pepper` (no trailing slash) still correctly extracts `"pepper"` from `/mcp/pepper/`. The correct wiring: `_app` normalizes the path, then calls `BusAuthMiddleware`, which wraps `Router`. This means `BusAuthMiddleware` is sandwiched between `_app` and `Router`, not outside `_app`.

**Why `bus_auth_mode` lives at the top-level of `DaemonConfig` (not nested under `http:`).**  The field controls both the HTTP middleware AND the `has_auth_hook` guard in `runner.py`'s `_validate_http` (which permits non-loopback binds). It is a daemon-wide security policy that has implications beyond the HTTP binding, making it a natural sibling of `bus_hooks:` at the root level.

**JWT verification uses PyJWT 2.x EdDSA support.** `PyJWT[crypto]>=2.8` can decode an EdDSA-signed JWT directly against a `cryptography` `Ed25519PublicKey` object (`jwt.decode(token, public_key, algorithms=["EdDSA"])`). Since `PubkeyRegistry.lookup()` already returns `Ed25519PublicKey` objects, no PEM serialization round-trip is needed. PyJWT automatically validates `exp`. The `sub` claim is validated explicitly after decoding. `verify_bearer_jwt` catches `jwt.ExpiredSignatureError` and `jwt.InvalidTokenError` separately to produce accurate log messages.

**Why `verify_bearer_jwt` is a standalone function in `verify.py`.** Separating the JWT crypto from the ASGI mechanics makes each unit independently testable (no ASGI scope construction needed to test JWT verification logic). It also creates the right seam for Dβ-3 (busproxy outbound signing), which will need to share the `verify` side of the crypto core — importing from `bus.auth.verify` is cleaner than importing from `bus.auth.middleware`.

**Existing tests are unaffected.** `HTTPHost` defaults `bus_auth_mode="off"` and `BusAuthMiddleware` short-circuits on `"off"` mode. All existing `test_http_host.py` tests instantiate `HTTPHost` without the new parameter and will continue to pass — no auth check is applied.

**`has_auth_hook` wiring.** The runner's TODO comment at line 165–167 says "Scan loaded hooks for auth-hook interface and set True. Until then, non-loopback bind is always refused." This ticket fulfils that TODO by making `has_auth_hook = daemon_cfg.bus_auth_mode != "off"`. A `bus_auth_mode: warn` or `enforce` config therefore enables non-loopback binds.

## Sub-requests (topologically sorted)

1. **Add `"PyJWT[crypto]>=2.8"` to `packages/core/pyproject.toml`** in the `[project] dependencies` list, after `"cryptography>=42.0"`.

2. **Add `bus_auth_mode` field to `DaemonConfig` in `packages/core/src/agent_core/bus/config.py`.**

   Add the import at the top if not already present: `from typing import Any, Literal` (already has `Literal` for `LoggingConfig` — verify and add to the same import line if needed).

   Add to `DaemonConfig`:
   ```python
   bus_auth_mode: Literal["off", "warn", "enforce"] = "off"
   ```
   Place it after the `logging` field to keep security-related fields grouped at the end.

3. **Create `packages/core/src/agent_core/bus/auth/verify.py`.**

   ```python
   """EdDSA JWT bearer-token verification for the bus auth middleware (Dβ-2b).

   Pure, side-effect-free except for logging. No I/O. Consumed by BusAuthMiddleware
   and intended for future reuse by Dβ-3 (busproxy signing verification path).
   """

   from __future__ import annotations

   import logging

   import jwt
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

   log = logging.getLogger(__name__)


   def verify_bearer_jwt(
       token: str,
       *,
       public_key: Ed25519PublicKey,
       expected_sub: str,
   ) -> bool:
       """Verify an EdDSA JWT bearer token against a registered Ed25519 public key.

       Checks:
       - Signature valid under *public_key*.
       - ``exp`` claim present and not expired (verified by PyJWT automatically).
       - ``sub`` claim equals *expected_sub* (path-identity binding).

       Returns True on full success; False (and logs a WARNING) on any failure.
       Does not raise — all JWT exceptions are caught and converted to False.
       """
       try:
           claims: dict = jwt.decode(
               token,
               public_key,
               algorithms=["EdDSA"],
               options={"require": ["exp", "sub"]},
           )
       except jwt.ExpiredSignatureError:
           log.warning("bus auth: bearer token is expired (expected_sub=%r)", expected_sub)
           return False
       except jwt.InvalidTokenError as exc:
           log.warning("bus auth: bearer token invalid for being %r: %s", expected_sub, exc)
           return False

       sub = claims.get("sub")
       if sub != expected_sub:
           log.warning(
               "bus auth: token sub=%r does not match path being=%r (path-identity mismatch)",
               sub,
               expected_sub,
           )
           return False

       return True
   ```

4. **Create `packages/core/src/agent_core/bus/auth/middleware.py`.**

   ```python
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
   ```

5. **Update `HTTPHost` in `packages/core/src/agent_core/bus/http_host.py`.**

   a. Add import for `BusAuthMiddleware`:
   ```python
   from agent_core.bus.auth.middleware import BusAuthMiddleware
   ```

   b. Add `bus_auth_mode: str = "off"` parameter and store it:
   ```python
   def __init__(
       self,
       *,
       bind_host: str = "127.0.0.1",
       bind_port: int = 8788,
       notify_broker: NotificationBroker | None = None,
       notify_snapshot: Callable[[str], dict | None] | None = None,
       pubkey_registry: PubkeyRegistry | None = None,
       bus_auth_mode: str = "off",
   ):
       self._bind_host = bind_host
       self._requested_port = bind_port
       self._mounts: list[MCPHostable] = []
       self._server: uvicorn.Server | None = None
       self._serve_task: asyncio.Task | None = None
       self._started = False
       self._notify_broker = notify_broker
       self._notify_snapshot = notify_snapshot
       self._pubkey_registry = pubkey_registry
       self._bus_auth_mode = bus_auth_mode
   ```

   c. In `start()`, after building `router`, wrap it with `BusAuthMiddleware` and update `_app` to call it. Replace the `router = Router(...)` + `_app` block:

   ```python
   router = Router(
       routes=routes,
       redirect_slashes=False,
       lifespan=_make_lifespan(sub_apps),
   )

   # Auth middleware wraps the router. Path normalization in _app runs first
   # (before the middleware) so bare /mcp/<being> → /mcp/<being>/ happens
   # before the being name is extracted.
   _auth_inner = BusAuthMiddleware(
       router,
       pubkey_registry=self._pubkey_registry,
       bus_auth_mode=self._bus_auth_mode,
   )

   async def _app(scope: dict, receive, send) -> None:
       if scope["type"] in ("http", "websocket"):
           path: str = scope.get("path", "")
           if path in mount_prefixes and not path.endswith("/"):
               scope = {**scope, "path": path + "/"}
       await _auth_inner(scope, receive, send)
   ```

   No other changes to `HTTPHost`.

6. **Update `build_bus_from_config` in `packages/core/src/agent_core/bus/runner.py`.**

   a. Replace the `has_auth_hook = False` line (currently line ~167) and the TODO comment above it:
   ```python
   # bus_auth_mode controls both the ASGI auth middleware (Dβ-2b) and the
   # loopback-only guard: a non-loopback bind is permitted only when auth is
   # active (mode != "off"), making the two decisions a single coupled invariant.
   has_auth_hook = daemon_cfg.bus_auth_mode != "off"
   ```
   Remove the `for stage in ("pre_publish", "pre_deliver"):` loop's `has_auth_hook` assignment if it already sets `False` and is not used anywhere else. (Looking at the current runner.py:167, `has_auth_hook = False` is set BEFORE the bus_hooks loop; the loop never modifies it. Simply replacing the `False` assignment is sufficient.)

   b. Pass `bus_auth_mode` to `HTTPHost(...)`:
   ```python
   http_host = HTTPHost(
       bind_host=daemon_cfg.http.bind_host,
       bind_port=daemon_cfg.http.bind_port,
       notify_broker=notify_broker,
       notify_snapshot=bus.snapshot_for_agent,
       pubkey_registry=pubkey_registry,
       bus_auth_mode=daemon_cfg.bus_auth_mode,
   )
   ```

7. **Write `packages/core/tests/bus/test_bus_auth_middleware.py`.**

   Full test module — table-driven over all three modes:

   ```python
   """Tests for BusAuthMiddleware (Dβ-2b) — table-driven over off/warn/enforce."""

   from __future__ import annotations

   import time
   import logging

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
   ```

8. **Add tests to `packages/core/tests/bus/test_config.py`** for the new `bus_auth_mode` field. Append to `TestDaemonConfigValidMinimal`:

   ```python
   def test_defaults_bus_auth_mode(self):
       cfg = DaemonConfig.model_validate({})
       assert cfg.bus_auth_mode == "off"

   def test_bus_auth_mode_warn_accepted(self):
       cfg = DaemonConfig.model_validate({"bus_auth_mode": "warn"})
       assert cfg.bus_auth_mode == "warn"

   def test_bus_auth_mode_enforce_accepted(self):
       cfg = DaemonConfig.model_validate({"bus_auth_mode": "enforce"})
       assert cfg.bus_auth_mode == "enforce"

   def test_bus_auth_mode_invalid_value_raises(self):
       with pytest.raises(pydantic.ValidationError):
           DaemonConfig.model_validate({"bus_auth_mode": "enforced"})
   ```

9. **Verify the gate.**

   ```bash
   just check
   ```
   Expected: green (lint, mypy, tests, coverage, patch-cov all pass).

## File-level changes

| File | Action | What changes |
|---|---|---|
| `packages/core/pyproject.toml` | Modify | Add `"PyJWT[crypto]>=2.8"` to `[project] dependencies` |
| `packages/core/src/agent_core/bus/config.py` | Modify | Add `bus_auth_mode: Literal["off", "warn", "enforce"] = "off"` to `DaemonConfig` |
| `packages/core/src/agent_core/bus/auth/verify.py` | Create | `verify_bearer_jwt(token, *, public_key, expected_sub) -> bool` — EdDSA JWT verification using PyJWT |
| `packages/core/src/agent_core/bus/auth/middleware.py` | Create | `BusAuthMiddleware` ASGI class implementing off/warn/enforce three-mode policy |
| `packages/core/src/agent_core/bus/http_host.py` | Modify | Add `bus_auth_mode: str = "off"` to `HTTPHost.__init__`; import and apply `BusAuthMiddleware` inside `start()` between path normalization and `Router` |
| `packages/core/src/agent_core/bus/runner.py` | Modify | Replace hardcoded `has_auth_hook = False` with `has_auth_hook = daemon_cfg.bus_auth_mode != "off"`; pass `bus_auth_mode=daemon_cfg.bus_auth_mode` to `HTTPHost()` |
| `packages/core/tests/bus/test_bus_auth_middleware.py` | Create | Table-driven tests for `BusAuthMiddleware` covering all three modes including the mandatory path-identity cross-being test |
| `packages/core/tests/bus/test_config.py` | Modify | Add 4 tests for `bus_auth_mode` field default and validation |

No changes to `bus/auth/__init__.py`, `bus/auth/pubkey_registry.py`, test conftest, justfile, or CI workflows. No new files in packages other than `packages/core`.

## Alternatives considered

1. **Inline the JWT verification inside `BusAuthMiddleware` rather than splitting off `verify.py`.** Would keep the auth module count lower. Ruled out because (a) `verify_bearer_jwt` is a pure function that the Dβ-3 busproxy ticket will also need, and splitting now avoids a cross-cutting refactor later; (b) separating crypto from ASGI glue makes each unit independently testable without constructing ASGI scopes.

2. **Use `cryptography` directly for JWT verification (no PyJWT dependency).** Possible — Ed25519 signature verification is straightforward: base64url-decode the header/payload/signature, `public_key.verify(signature, header_dot_payload)`. But `exp` claim handling, claim extraction, and error taxonomy would all need to be written manually. `PyJWT[crypto]>=2.8` is already in the uv.lock (transitively via `mcp`) and provides all of this in two lines. Adding it as a direct dependency is the correct action. Ruled out: the manual path adds ~40 lines of boilerplate for zero gain.

3. **Place `bus_auth_mode` in `HttpConfig` instead of at the root of `DaemonConfig`.** Tempting because the middleware is HTTP-level. Ruled out: the same flag also controls `has_auth_hook` in `_validate_http()`, which is a daemon-wide boot-time decision (non-loopback bind permitted?). Coupling it to `HttpConfig` would mean the HTTP section controls a daemon lifecycle invariant. A top-level field keeps the two effects of the flag visible at the same YAML level.

4. **Implement `bus_auth_mode: enforce` only (skip `warn`).** Would halve the middleware logic. Ruled out: the design spec explicitly requires the `warn` migration window so existing beings (Wren, Pepper) can be observed authenticating before `enforce` is flipped. A spec that skips `warn` is non-compliant with the approved design.

## Open questions

None. Every referenced file was read before drafting this spec:
- `packages/core/src/agent_core/bus/config.py` — schema confirmed, `DaemonConfig` uses `extra="forbid"` + `Literal` already imported.
- `packages/core/src/agent_core/bus/http_host.py` — `HTTPHost.__init__` and `start()` internals confirmed; `_pubkey_registry` stub comment present.
- `packages/core/src/agent_core/bus/runner.py` — `has_auth_hook = False` at line 167, TODO comment, `HTTPHost(...)` call confirmed.
- `packages/core/src/agent_core/bus/auth/pubkey_registry.py` — `PubkeyRegistry.lookup()` returns `Ed25519PublicKey | None`.
- `uv.lock` — `PyJWT[crypto]==2.13.0` confirmed present as transitive dep of `mcp`.
- `packages/core/pyproject.toml` — `cryptography>=42.0` already direct; `PyJWT` not yet direct.

## Out of scope

- **Dβ-1 sign primitives** (keygen, `sign_jwt`, private-key load from vault): this ticket implements only the verification side, which is all the bus middleware needs. Signing lives in Dβ-3 (busproxy).
- **Dβ-3 busproxy outbound signing**: mint/cache/refresh bearer from vault private key + 401 re-mint-retry.
- **Dβ-4 hatchery keypair provisioning**: generating Ed25519 keypairs at hatch.
- **Dβ-5 migration** for existing beings (Wren/Pepper) + `warn` → `enforce` cutover.
- **`aud` claim validation**: the design spec mentions `aud = bus` as a claim the JWT should carry, but the issue's verification contract is "signature + exp + sub == path being" only. `aud` validation can be added in Dβ-3/5 once the bus has a stable identity string to publish as its audience.
- **Non-loopback bind testing**: `_validate_http` with `has_auth_hook=True` is already covered by `test_runner.py`; no new integration test needed for the loopback guard.
- **Config reload live-swap of `bus_auth_mode`**: the daemon tears down and reinitialises on config reload; the new mode takes effect naturally on the next `build_bus_from_config` call.
- Any changes to `agent-core-busproxy`, `agent-core-discord`, or other packages.
