"""Tests for the shared agentmail client setup."""

import pytest
from agentmail import AgentMail

import agent_core_credentials.secrets as secrets
from agent_core.email.client import get_client, get_inbox_id


class _NoVaultStore:
    """Stand-in CredentialStore that has no entries, forcing the env fallback."""

    def get(self, name: str) -> None:
        return None


def test_get_client_with_api_key(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test-key-123")
    client = get_client()
    assert client is not None


def test_get_client_builds_real_agentmail_from_resolved_key(monkeypatch):
    """Exercise the real get_client() factory end-to-end (no mock of get_client).

    Only the external vault I/O is isolated (via secrets._open_store); the real
    secrets.get env-fallback and the real AgentMail constructor both execute, so
    the assertion is against the genuine returned client object.
    """
    monkeypatch.setattr(secrets, "_open_store", lambda: _NoVaultStore())
    monkeypatch.setenv("AGENTMAIL_API_KEY", "direct-factory-key")

    client = get_client()

    assert isinstance(client, AgentMail)
    # A fully-constructed real client exposes its resource namespaces.
    assert hasattr(client, "inboxes")


def test_get_client_missing_api_key(monkeypatch):
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        get_client()


def test_get_inbox_id_default(monkeypatch):
    monkeypatch.delenv("PEPPER_INBOX_ID", raising=False)
    assert get_inbox_id() == "pepper_ai@agentmail.to"


def test_get_inbox_id_custom(monkeypatch):
    monkeypatch.setenv("PEPPER_INBOX_ID", "custom@agentmail.to")
    assert get_inbox_id() == "custom@agentmail.to"
