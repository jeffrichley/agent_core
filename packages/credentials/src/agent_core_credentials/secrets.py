"""secrets.get() — vault-first, env-fallback secret accessor.

Resolution order:
  1. Vault entry with ``service == name`` — returns ``cred.password``.
  2. ``os.environ.get(name)`` — env fallback for migration safety.
  3. Raises ``SecretNotFoundError`` if neither source has the secret.

Vault failures (no vault file, bad password, any pykeepass error) are caught,
logged at DEBUG, and fall through silently to the env fallback.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core_credentials.store import CredentialStore

log = logging.getLogger(__name__)


class SecretNotFoundError(Exception):
    """Raised when neither the vault nor os.environ contains the named secret."""


def _open_store() -> CredentialStore:
    """Open the credential store against the default vault path.

    Isolated into its own function so tests can monkeypatch it without
    touching the real vault.
    """
    from agent_core_credentials import default_vault_path
    from agent_core_credentials.store import CredentialStore

    return CredentialStore(default_vault_path())


def get(name: str) -> str:
    """Return the secret named *name*.

    Resolution order:
    1. Vault entry whose ``service`` field equals *name*.
    2. ``os.environ[name]`` (migration fallback — works while vault is
       being populated).
    3. Raises :exc:`SecretNotFoundError`.

    Any exception thrown while opening or querying the vault is caught
    and logged at DEBUG so that a missing / locked vault never hard-fails
    a call that could succeed from the environment.
    """
    # --- vault path ---
    try:
        store = _open_store()
        cred = store.get(name)
        if cred is not None and cred.password:
            return cred.password
    except Exception:
        log.debug("vault lookup failed for %r — falling through to env", name, exc_info=True)

    # --- env fallback ---
    value = os.environ.get(name)
    if value:
        return value

    raise SecretNotFoundError(
        f"Secret {name!r} not found in vault or environment. "
        "Populate the vault entry or set the env var before starting."
    )
