"""Tests for the credential store."""

import pytest

from agent_core_credentials.models import Credential, CredentialSummary
from agent_core_credentials.store import CredentialStore


def test_credential_fields():
    """Credential dataclass holds all fields."""
    cred = Credential(
        service="apex",
        username="jeff@example.com",
        password="secret",
        url="https://apex.example.com",
        notes="Test",
    )
    assert cred.service == "apex"
    assert cred.password == "secret"


def test_credential_summary_excludes_password():
    """CredentialSummary has no password field."""
    summary = CredentialSummary(service="apex", username="jeff@example.com")
    assert not hasattr(summary, "password")


@pytest.fixture()
def vault(tmp_path):
    """Create a temp vault with a known master password via DI."""
    vault_path = tmp_path / "credentials.kdbx"
    return CredentialStore(vault_path, _master_password="testpass")


def test_set_and_get(vault):
    """Store and retrieve a credential."""
    vault.set("apex", "jeff@test.com", "secret123", "https://apex.com", "notes")
    cred = vault.get("apex")
    assert cred is not None
    assert cred.service == "apex"
    assert cred.username == "jeff@test.com"
    assert cred.password == "secret123"
    assert cred.url == "https://apex.com"
    assert cred.notes == "notes"


def test_get_missing_returns_none(vault):
    """Getting a nonexistent service from an existing vault returns None."""
    vault.set("seed", "u@test.com", "p")  # create the vault first
    assert vault.get("nonexistent") is None


def test_set_overwrites_existing(vault):
    """Setting a service that exists overwrites it."""
    vault.set("apex", "old@test.com", "old")
    vault.set("apex", "new@test.com", "new")
    cred = vault.get("apex")
    assert cred is not None
    assert cred.username == "new@test.com"
    assert cred.password == "new"


def test_list_credentials(vault):
    """List returns summaries without passwords."""
    vault.set("apex", "jeff@test.com", "secret1")
    vault.set("etsy", "jeff@etsy.com", "secret2", "https://etsy.com")
    summaries = vault.list()
    assert len(summaries) == 2
    names = {s.service for s in summaries}
    assert names == {"apex", "etsy"}
    for s in summaries:
        assert not hasattr(s, "password")


def test_delete_existing(vault):
    """Delete removes a credential and returns True."""
    vault.set("apex", "jeff@test.com", "secret")
    assert vault.delete("apex") is True
    assert vault.get("apex") is None


def test_delete_missing_returns_false(vault):
    """Delete of a nonexistent service in an existing vault returns False."""
    vault.set("seed", "u@test.com", "p")  # create the vault first
    assert vault.delete("nonexistent") is False


def test_reads_do_not_create_vault(tmp_path, monkeypatch):
    """get/list/delete must NOT create an empty vault when it's absent.

    Silently creating an empty .kdbx on a read masks a missing/misconfigured
    vault and can shadow the real one. They must raise FileNotFoundError, and no
    file may appear. Only the write path (set) creates. Adversarial review #8.
    """
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password",
        lambda vault_path: "pw",
    )
    vault_path = tmp_path / "credentials.kdbx"
    store = CredentialStore(vault_path)

    for op in (lambda: store.get("x"), store.list, lambda: store.delete("x")):
        with pytest.raises(FileNotFoundError):
            op()
        assert not vault_path.exists()  # no empty vault was created

    # The write path DOES create it.
    store.set("apex", "jeff@test.com", "secret")
    assert vault_path.exists()
    assert store.get("apex") is not None


def test_creates_vault_on_first_set(vault, tmp_path):
    """The .kdbx file is created on first set if it doesn't exist."""
    vault_path = tmp_path / "credentials.kdbx"
    assert not vault_path.exists()
    vault.set("apex", "jeff@test.com", "secret")
    assert vault_path.exists()


def test_missing_password_env_raises(tmp_path, monkeypatch):
    """Store raises ValueError with new message when no password is available."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password",
        lambda vault_path: None,
    )
    store = CredentialStore(tmp_path / "credentials.kdbx")
    with pytest.raises(ValueError, match="No master password found"):
        store.get("anything")
