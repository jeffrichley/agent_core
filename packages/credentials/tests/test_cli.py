"""Tests for agent-core creds CLI commands."""

from typer.testing import CliRunner

from agent_core_credentials.cli import creds_app

runner = CliRunner()


def test_creds_set_and_get(tmp_path, monkeypatch):
    """Set a credential then get it back."""
    monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")
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
    assert "jeff@test.com" in result.output
    # Default mode should NOT show password
    assert "secret123" not in result.output


def test_creds_get_json(tmp_path, monkeypatch):
    """Get with --json includes password."""
    monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    runner.invoke(
        creds_app,
        ["set", "apex"],
        input="jeff@test.com\nsecret123\n\n\n",
    )
    result = runner.invoke(creds_app, ["get", "apex", "--json"])
    assert result.exit_code == 0
    assert "secret123" in result.output


def test_creds_get_not_found(tmp_path, monkeypatch):
    """Get nonexistent service exits with code 1."""
    monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    result = runner.invoke(creds_app, ["get", "nonexistent"])
    assert result.exit_code == 1


def test_creds_list(tmp_path, monkeypatch):
    """List shows service names."""
    monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")
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
    monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")
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
    monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")
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
    monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)

    result = runner.invoke(creds_app, ["delete", "nonexistent"])
    assert result.exit_code == 1


def test_creds_init_creates_vault(tmp_path, monkeypatch):
    """Init creates the .kdbx file and writes password to .env."""
    monkeypatch.delenv("AGENT_CORE_VAULT_PASSWORD", raising=False)
    vault_path = tmp_path / "credentials.kdbx"
    env_file = tmp_path / ".env"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)
    monkeypatch.setattr("agent_core_credentials.cli._env_path", env_file)

    result = runner.invoke(
        creds_app,
        ["init"],
        input="mypassword\nmypassword\n",
    )
    assert result.exit_code == 0
    assert "Created" in result.output
    assert vault_path.exists()
    assert "AGENT_CORE_VAULT_PASSWORD=" in env_file.read_text()


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

    result = runner.invoke(
        creds_app,
        ["init"],
        input="password1\npassword2\n",
    )
    assert result.exit_code == 1
    assert "match" in result.output.lower()


def test_creds_no_password_env(tmp_path, monkeypatch):
    """Commands fail gracefully when AGENT_CORE_VAULT_PASSWORD is not set."""
    monkeypatch.delenv("AGENT_CORE_VAULT_PASSWORD", raising=False)
    vault_path = tmp_path / "credentials.kdbx"
    monkeypatch.setattr("agent_core_credentials.cli._vault_path", vault_path)
    monkeypatch.setattr(
        "agent_core_credentials.cli._env_path", tmp_path / "nonexistent.env"
    )

    result = runner.invoke(creds_app, ["list"])
    assert result.exit_code == 1
    assert "AGENT_CORE_VAULT_PASSWORD" in result.output
