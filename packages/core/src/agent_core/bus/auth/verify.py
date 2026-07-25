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
