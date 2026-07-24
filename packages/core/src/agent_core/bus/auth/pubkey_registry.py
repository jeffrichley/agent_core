"""Public-key registry: being → Ed25519PublicKey, loaded from endpoint config.

Dβ-2a in the bus transport auth design
(docs/superpowers/specs/2026-07-15-bus-transport-auth-design.md).
"""

from __future__ import annotations

import logging

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from agent_core.bus.config import EndpointEntryConfig

log = logging.getLogger(__name__)


class PubkeyRegistry:
    """Immutable mapping from being name to its registered Ed25519 public key.

    Built from endpoint config at daemon boot / config reload.
    Refresh by calling build_pubkey_registry with the new DaemonConfig entries.
    """

    def __init__(self, keys: dict[str, Ed25519PublicKey]) -> None:
        """Construct from a pre-validated mapping. Not for direct use — call
        build_pubkey_registry() instead."""
        self._keys: dict[str, Ed25519PublicKey] = keys

    def lookup(self, being: str) -> Ed25519PublicKey | None:
        """Return the public key for *being*, or None if not registered."""
        return self._keys.get(being)

    def __len__(self) -> int:
        return len(self._keys)

    def __repr__(self) -> str:
        return f"PubkeyRegistry({sorted(self._keys.keys())!r})"


def build_pubkey_registry(entries: list[EndpointEntryConfig]) -> PubkeyRegistry:
    """Load the pubkey_pem from each endpoint entry into an immutable registry.

    Entries without pubkey_pem are silently skipped.
    Entries with an unparseable or non-Ed25519 PEM are skipped after logging
    an ERROR; the remaining entries are still loaded.

    Args:
        entries: the EndpointEntryConfig list from DaemonConfig.

    Returns:
        A fresh PubkeyRegistry mapping being name → Ed25519PublicKey.
    """
    keys: dict[str, Ed25519PublicKey] = {}
    for entry in entries:
        if entry.pubkey_pem is None:
            continue
        try:
            raw_key = load_pem_public_key(entry.pubkey_pem.encode())
        except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
            log.error(
                "endpoint %r: pubkey_pem is not a valid PEM public key — "
                "being absent from pubkey registry: %s",
                entry.name,
                exc,
            )
            continue
        if not isinstance(raw_key, Ed25519PublicKey):
            log.error(
                "endpoint %r: pubkey_pem is a %s key, expected Ed25519 — "
                "being absent from pubkey registry",
                entry.name,
                type(raw_key).__name__,
            )
            continue
        keys[entry.name] = raw_key
    return PubkeyRegistry(keys)
