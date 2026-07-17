"""Tests for agent_core_credentials.master_password — keyring + fallback store."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import keyring.errors

from agent_core_credentials.master_password import (
    EncryptedFileStore,
    _KeyringStore,
    _get_backend,
    _hash_for,
    _read_env_file_password,
    _scrub_env_file_password,
    _username_for,
    get_master_password,
    set_master_password,
    KEYRING_SERVICE,
)


# ---------------------------------------------------------------------------
# _KeyringStore
# ---------------------------------------------------------------------------


def test_keyring_store_set_and_get(tmp_path, monkeypatch):
    """_KeyringStore.set + get round-trips via a fake in-memory keyring."""
    store: dict[tuple[str, str], str] = {}

    def fake_get_password(service: str, username: str) -> str | None:
        return store.get((service, username))

    def fake_set_password(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    monkeypatch.setattr("keyring.get_password", fake_get_password)
    monkeypatch.setattr("keyring.set_password", fake_set_password)

    vault_path = tmp_path / "vault.kdbx"
    ks = _KeyringStore(vault_path)
    assert ks.get() is None

    ks.set("mysecret")
    assert ks.get() == "mysecret"


# ---------------------------------------------------------------------------
# EncryptedFileStore
# ---------------------------------------------------------------------------


def test_encrypted_file_store_set_and_get(tmp_path):
    """EncryptedFileStore.set('secret') then get() round-trips in tmp_path."""
    vault_path = tmp_path / "vault.kdbx"
    store = EncryptedFileStore(vault_path)
    store.set("s3cr3t")
    assert store.get() == "s3cr3t"


def test_encrypted_file_store_get_returns_none_when_empty(tmp_path):
    """EncryptedFileStore.get() returns None when no data file exists."""
    vault_path = tmp_path / "vault.kdbx"
    store = EncryptedFileStore(vault_path)
    assert store.get() is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only chmod test")
def test_encrypted_file_store_files_are_owner_only(tmp_path):
    """After set(), both key and data files have mode 0o600."""
    vault_path = tmp_path / "vault.kdbx"
    store = EncryptedFileStore(vault_path)
    store.set("s3cr3t")
    assert oct(store._key_path.stat().st_mode & 0o777) == oct(0o600)
    assert oct(store._data_path.stat().st_mode & 0o777) == oct(0o600)


def test_encrypted_file_store_get_returns_none_when_key_deleted(tmp_path):
    """After set(), deleting key file causes get() to return None (InvalidToken caught)."""
    vault_path = tmp_path / "vault.kdbx"
    store = EncryptedFileStore(vault_path)
    store.set("s3cr3t")
    # Delete the key file — next get() generates a fresh key that can't decrypt old data
    store._key_path.unlink()
    assert store.get() is None


# ---------------------------------------------------------------------------
# _get_backend
# ---------------------------------------------------------------------------


def test_get_backend_uses_keyring_when_available(tmp_path, monkeypatch):
    """When keyring.get_password returns None (no error), _get_backend returns _KeyringStore."""
    monkeypatch.setattr("keyring.get_password", lambda *_: None)
    vault_path = tmp_path / "vault.kdbx"
    backend = _get_backend(vault_path)
    assert isinstance(backend, _KeyringStore)


def test_get_backend_falls_back_on_nokeyringerror(tmp_path, monkeypatch):
    """When keyring.get_password raises NoKeyringError, _get_backend returns EncryptedFileStore."""

    def raise_no_keyring(*_):
        raise keyring.errors.NoKeyringError("no keyring")

    monkeypatch.setattr("keyring.get_password", raise_no_keyring)
    vault_path = tmp_path / "vault.kdbx"
    backend = _get_backend(vault_path)
    assert isinstance(backend, EncryptedFileStore)


# ---------------------------------------------------------------------------
# get_master_password
# ---------------------------------------------------------------------------


def test_get_master_password_reads_from_backend(tmp_path, monkeypatch):
    """When backend.get() returns a value, get_master_password() returns it without .env."""
    vault_path = tmp_path / "vault.kdbx"
    fake_backend = MagicMock()
    fake_backend.get.return_value = "from_backend"
    monkeypatch.setattr(
        "agent_core_credentials.master_password._get_backend",
        lambda _: fake_backend,
    )
    env_path = tmp_path / "nonexistent.env"
    result = get_master_password(vault_path, env_path=env_path)
    assert result == "from_backend"
    fake_backend.set.assert_not_called()


def test_get_master_password_migrates_from_env(tmp_path, monkeypatch):
    """When backend empty but .env has the key, get_master_password migrates and returns it."""
    vault_path = tmp_path / "vault.kdbx"
    env_path = tmp_path / ".env"
    env_path.write_text("AGENT_CORE_VAULT_PASSWORD=s3cr3t\n")

    fake_backend = MagicMock()
    fake_backend.get.return_value = None
    monkeypatch.setattr(
        "agent_core_credentials.master_password._get_backend",
        lambda _: fake_backend,
    )

    result = get_master_password(vault_path, env_path=env_path)
    assert result == "s3cr3t"
    fake_backend.set.assert_called_once_with("s3cr3t")
    # Password line should be scrubbed from .env
    assert "AGENT_CORE_VAULT_PASSWORD" not in env_path.read_text()


def test_get_master_password_migration_is_idempotent(tmp_path, monkeypatch):
    """Calling get_master_password() twice when password is in backend doesn't scrub .env again."""
    vault_path = tmp_path / "vault.kdbx"
    env_path = tmp_path / ".env"
    env_path.write_text("AGENT_CORE_VAULT_PASSWORD=s3cr3t\n")

    call_count = [0]

    def make_backend(_):
        call_count[0] += 1
        backend = MagicMock()
        # First call: empty (triggers migration), second call: has value
        if call_count[0] == 1:
            backend.get.return_value = None
        else:
            backend.get.return_value = "s3cr3t"
        return backend

    monkeypatch.setattr(
        "agent_core_credentials.master_password._get_backend",
        make_backend,
    )

    # First call migrates
    result1 = get_master_password(vault_path, env_path=env_path)
    assert result1 == "s3cr3t"
    # After first call, .env no longer has the key
    assert "AGENT_CORE_VAULT_PASSWORD" not in env_path.read_text()

    # Second call: backend now has value, .env scrub would be a no-op anyway
    result2 = get_master_password(vault_path, env_path=env_path)
    assert result2 == "s3cr3t"


def test_get_master_password_returns_none_when_no_source(tmp_path, monkeypatch):
    """Returns None when backend is empty and .env has no password / doesn't exist."""
    vault_path = tmp_path / "vault.kdbx"

    fake_backend = MagicMock()
    fake_backend.get.return_value = None
    monkeypatch.setattr(
        "agent_core_credentials.master_password._get_backend",
        lambda _: fake_backend,
    )

    # Non-existent env_path
    non_existent = tmp_path / "missing.env"
    result = get_master_password(vault_path, env_path=non_existent)
    assert result is None

    # Existing .env without the key
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER_KEY=value\n")
    result2 = get_master_password(vault_path, env_path=env_path)
    assert result2 is None


# ---------------------------------------------------------------------------
# _scrub_env_file_password
# ---------------------------------------------------------------------------


def test_scrub_env_file_password_removes_only_password_line(tmp_path):
    """Scrubbing keeps other keys; removes AGENT_CORE_VAULT_PASSWORD line."""
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\nAGENT_CORE_VAULT_PASSWORD=x\nBAZ=qux\n")
    _scrub_env_file_password(env_path)
    remaining = env_path.read_text()
    assert "FOO=bar" in remaining
    assert "BAZ=qux" in remaining
    assert "AGENT_CORE_VAULT_PASSWORD" not in remaining
