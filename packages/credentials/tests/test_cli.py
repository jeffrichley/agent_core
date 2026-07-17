"""Tests for agent-core creds CLI commands."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from agent_core_credentials.cli import creds_app

runner = CliRunner()


def test_creds_set_and_get(tmp_path, monkeypatch):
    """Set a credential then get it back."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    result = runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret123\nhttps://apex.com\nTest notes\n",
    )
    assert result.exit_code == 0
    assert "Saved" in result.output

    result = runner.invoke(creds_app, ["get", "apex"])
    assert result.exit_code == 0
    assert "jeff@test.com" not in result.output
    assert "Length:" in result.output
    # Default mode should NOT show password
    assert "secret123" not in result.output


def test_creds_get_json(tmp_path, monkeypatch):
    """Get with --json returns metadata only, no secret."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret123\n\n\n",
    )
    result = runner.invoke(creds_app, ["get", "apex", "--json"])
    assert result.exit_code == 0
    assert "secret123" not in result.output
    data = json.loads(result.output)
    assert data == {"name": "apex", "exists": True, "length": 9}
    assert "password" not in data


def test_creds_get_not_found(tmp_path, monkeypatch):
    """Get nonexistent service exits with code 1."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    result = runner.invoke(creds_app, ["get", "nonexistent"])
    assert result.exit_code == 1


def test_creds_list(tmp_path, monkeypatch):
    """List shows service names."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret\n\n\n",
    )
    runner.invoke(
        creds_app,
        ["set", "etsy"],
        input="jeff@etsy.com\nsecret\n\n\n",
    )
    result = runner.invoke(creds_app, ["list"])
    assert result.exit_code == 0
    assert "apex" in result.output
    assert "etsy" in result.output


def test_creds_list_json(tmp_path, monkeypatch):
    """List --json returns JSON without passwords."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret\n\n\n",
    )
    result = runner.invoke(creds_app, ["list", "--json"])
    assert result.exit_code == 0
    assert "jeff@test.com" in result.output
    assert "secret" not in result.output


def test_creds_delete(tmp_path, monkeypatch):
    """Delete removes a credential."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret\n\n\n",
    )
    result = runner.invoke(creds_app, ["delete", "apex"])
    assert result.exit_code == 0
    assert "Deleted" in result.output

    result = runner.invoke(creds_app, ["get", "apex"])
    assert result.exit_code == 1


def test_creds_delete_not_found(tmp_path, monkeypatch):
    """Delete nonexistent service exits with code 1."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    result = runner.invoke(creds_app, ["delete", "nonexistent"])
    assert result.exit_code == 1


def test_creds_init_creates_vault(tmp_path, monkeypatch):
    """Init creates the .kdbx file and stores password via set_master_password (not .env)."""
    calls: list[tuple] = []

    def fake_set_master_password(vault_path, password):
        calls.append((vault_path, password))

    vault_path = tmp_path / "credentials.kdbx"
    env_file = tmp_path / ".env"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)
    monkeypatch.setattr("agent_core_credentials.cli._env_path", env_file)
    monkeypatch.setattr("agent_core_credentials.cli.set_master_password", fake_set_master_password)

    result = runner.invoke(
        creds_app,
        ["init"],
        input="mypassword\nmypassword\n",
    )
    assert result.exit_code == 0
    assert "Created" in result.output
    assert vault_path.exists()
    # Password must NOT be written to .env
    assert not env_file.exists()
    # set_master_password must have been called with the correct password
    assert len(calls) == 1
    assert calls[0][1] == "mypassword"


def test_creds_init_already_exists(tmp_path, monkeypatch):
    """Init refuses to overwrite an existing vault."""
    vault_path = tmp_path / "credentials.kdbx"
    vault_path.touch()
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    result = runner.invoke(creds_app, ["init"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_creds_init_password_mismatch(tmp_path, monkeypatch):
    """Init rejects mismatched passwords."""
    vault_path = tmp_path / "credentials.kdbx"
    env_file = tmp_path / ".env"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)
    monkeypatch.setattr("agent_core_credentials.cli._env_path", env_file)
    monkeypatch.setattr(
        "agent_core_credentials.cli.set_master_password",
        lambda vault_path, password: None,
    )

    result = runner.invoke(
        creds_app,
        ["init"],
        input="password1\npassword2\n",
    )
    assert result.exit_code == 1
    assert "match" in result.output.lower()


def test_creds_no_password_env(tmp_path, monkeypatch):
    """Commands fail gracefully when no master password is available."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: None
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)
    monkeypatch.setattr(
        "agent_core_credentials.cli._env_path", tmp_path / "nonexistent.env"
    )

    result = runner.invoke(creds_app, ["list"])
    assert result.exit_code == 1
    assert "No master password found" in result.output


def test_creds_get_text_shows_length(tmp_path, monkeypatch):
    """Text mode shows Length and char count; password and username are absent."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret123\n\n\n",
    )
    result = runner.invoke(creds_app, ["get", "apex"])
    assert result.exit_code == 0
    assert "Length:" in result.output
    assert "9" in result.output
    assert "secret123" not in result.output
    assert "jeff@test.com" not in result.output


def test_creds_get_json_schema(tmp_path, monkeypatch):
    """JSON output has exactly the keys name/exists/length, no password."""
    monkeypatch.setattr(
        "agent_core_credentials.store.get_master_password", lambda vault_path: "testpass"
    )
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret123\n\n\n",
    )
    result = runner.invoke(creds_app, ["get", "apex", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert set(data.keys()) == {"name", "exists", "length"}
    assert data["length"] == 9
    assert "secret123" not in result.output
