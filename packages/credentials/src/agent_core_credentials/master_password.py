"""Master password store — OS keyring (primary) + owner-only encrypted file (fallback).

Strategy pattern: ``_KeyringStore`` and ``EncryptedFileStore`` each implement
``get() -> str | None`` and ``set(password: str) -> None``.  ``_get_backend()``
selects the strategy at runtime by probing the keyring.  ``get_master_password``
and ``set_master_password`` are the public entry-points.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import keyring
import keyring.errors
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEYRING_SERVICE: str = "agent-core"
_ENV_PATH: Path = Path.home() / ".agent-core" / ".env"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _username_for(vault_path: Path) -> str:
    """Return the per-vault keyring username (absolute path string)."""
    return str(vault_path.resolve())


def _hash_for(vault_path: Path) -> str:
    """Return first 12 hex chars of sha256(resolved vault path) for filename suffixes."""
    digest = hashlib.sha256(str(vault_path.resolve()).encode()).hexdigest()
    return digest[:12]


# ---------------------------------------------------------------------------
# EncryptedFileStore — owner-only Fernet-encrypted file fallback
# ---------------------------------------------------------------------------


class EncryptedFileStore:
    """Owner-only encrypted file fallback for headless Linux/CI environments.

    Two files are written next to the vault:
      - ``_key_path``: 32-byte Fernet key (generated once, chmod 0o600).
      - ``_data_path``: Fernet-encrypted master password (chmod 0o600).

    Both files use a hash suffix so multiple vaults in the same directory
    don't collide.
    """

    def __init__(self, vault_path: Path) -> None:
        h = _hash_for(vault_path)
        self._key_path: Path = vault_path.parent / f".vault-key-{h}"
        self._data_path: Path = vault_path.parent / f".vault-pass-{h}"

    def _load_key(self) -> bytes:
        """Load or generate the Fernet key.  Writes 0o600 on creation."""
        if self._key_path.exists():
            return self._key_path.read_bytes()
        key = Fernet.generate_key()
        self._key_path.write_bytes(key)
        os.chmod(self._key_path, 0o600)
        return key

    def get(self) -> str | None:
        """Return the decrypted password, or None if absent or unreadable."""
        if not self._data_path.exists():
            return None
        key = self._load_key()
        f = Fernet(key)
        try:
            ciphertext = self._data_path.read_bytes()
            return f.decrypt(ciphertext).decode("utf-8")
        except InvalidToken:
            # Key was regenerated (old key deleted); old ciphertext is unreadable.
            logger.debug(
                "EncryptedFileStore: InvalidToken — key/data file mismatch; returning None",
            )
            return None

    def set(self, password: str) -> None:
        """Encrypt and persist the password.  Both files are chmod 0o600."""
        key = self._load_key()
        f = Fernet(key)
        ciphertext = f.encrypt(password.encode("utf-8"))
        self._data_path.write_bytes(ciphertext)
        os.chmod(self._data_path, 0o600)


# ---------------------------------------------------------------------------
# _KeyringStore — OS keyring (primary) backend
# ---------------------------------------------------------------------------


class _KeyringStore:
    """OS keyring backend (macOS Keychain, Windows Credential Manager, Secret Service)."""

    def __init__(self, vault_path: Path) -> None:
        self._username: str = _username_for(vault_path)

    def get(self) -> str | None:
        """Return the password from the keyring, or None if not stored."""
        return keyring.get_password(KEYRING_SERVICE, self._username)

    def set(self, password: str) -> None:
        """Persist the password in the keyring."""
        keyring.set_password(KEYRING_SERVICE, self._username, password)


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------


def _get_backend(vault_path: Path) -> _KeyringStore | EncryptedFileStore:
    """Return the appropriate backend by probing the OS keyring.

    A harmless read is performed on a non-secret probe key.  If ``get_password``
    returns ``None`` (no entry exists — the normal case) the keyring is
    functional; ``_KeyringStore`` is returned.  If it raises any exception
    (``NoKeyringError`` on headless Linux/CI, or any other error), fall back
    to ``EncryptedFileStore``.
    """
    try:
        keyring.get_password("__agent-core-probe__", "__probe__")
        logger.debug("_get_backend: OS keyring available; using _KeyringStore")
        return _KeyringStore(vault_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_get_backend: keyring unavailable (%s); using EncryptedFileStore", exc, exc_info=True)
        return EncryptedFileStore(vault_path)


# ---------------------------------------------------------------------------
# .env migration helpers
# ---------------------------------------------------------------------------


def _read_env_file_password(env_path: Path) -> str | None:
    """Parse env_path line by line; return AGENT_CORE_VAULT_PASSWORD value or None."""
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("AGENT_CORE_VAULT_PASSWORD="):
            value = stripped[len("AGENT_CORE_VAULT_PASSWORD="):]
            return value if value else None
    return None


def _scrub_env_file_password(env_path: Path) -> None:
    """Remove all non-comment AGENT_CORE_VAULT_PASSWORD= lines from env_path.

    No-op if the file does not exist.  Idempotent.
    """
    if not env_path.exists():
        return
    original = env_path.read_text()
    lines = original.splitlines(keepends=True)
    kept = [
        line
        for line in lines
        if not (
            not line.strip().startswith("#")
            and "AGENT_CORE_VAULT_PASSWORD=" in line
        )
    ]
    env_path.write_text("".join(kept))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_master_password(
    vault_path: Path,
    env_path: Path = _ENV_PATH,
) -> str | None:
    """Return the master password for vault_path, migrating from .env if needed.

    Resolution order:
    1. Backend (keyring or encrypted file).
    2. Plaintext ``AGENT_CORE_VAULT_PASSWORD`` in ``env_path`` — triggers a
       one-time migration: write to backend, scrub from ``.env``.
    3. Return ``None`` if neither source has a password.
    """
    backend = _get_backend(vault_path)
    stored = backend.get()
    if stored:
        return stored

    env_password = _read_env_file_password(env_path)
    if env_password:
        logger.debug("get_master_password: migrating password from .env to backend")
        backend.set(env_password)
        _scrub_env_file_password(env_path)
        return env_password

    return None


def set_master_password(vault_path: Path, password: str) -> None:
    """Persist the master password for vault_path in the active backend."""
    _get_backend(vault_path).set(password)
