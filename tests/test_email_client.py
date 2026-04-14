"""Tests for the shared agentmail client setup."""

import pytest

from agent_core.email.client import get_client, get_inbox_id


def test_get_client_with_api_key(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test-key-123")
    client = get_client()
    assert client is not None


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
