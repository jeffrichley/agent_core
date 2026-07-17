"""Tests for agent_core_credentials.secrets — four resolution paths."""

from __future__ import annotations

import pytest

from agent_core_credentials.secrets import SecretNotFoundError, get


class TestSecretsGet:
    """Resolution-order tests for secrets.get()."""

    def test_vault_hit_returns_password(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """When the vault contains an entry for name, return its password."""
        from agent_core_credentials import models

        cred = models.Credential(
            service="MY_TOKEN", username="svc", password="vault-secret", url="", notes=""
        )

        from agent_core_credentials import secrets as _sec

        monkeypatch.setattr(_sec, "_open_store", lambda: type("S", (), {"get": lambda self, n: cred})())

        result = get("MY_TOKEN")

        assert result == "vault-secret"

    def test_vault_miss_falls_through_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When vault has no entry for name, fall through to os.environ."""
        from agent_core_credentials import secrets as _sec

        monkeypatch.setattr(_sec, "_open_store", lambda: type("S", (), {"get": lambda self, n: None})())
        monkeypatch.setenv("MY_ENV_KEY", "env-secret")

        result = get("MY_ENV_KEY")

        assert result == "env-secret"

    def test_vault_exception_falls_through_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When vault raises any Exception, fall through to os.environ."""
        from agent_core_credentials import secrets as _sec

        def _boom():
            raise RuntimeError("vault unavailable")

        monkeypatch.setattr(_sec, "_open_store", _boom)
        monkeypatch.setenv("BOOM_KEY", "env-fallback")

        result = get("BOOM_KEY")

        assert result == "env-fallback"

    def test_neither_source_raises_secret_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When neither vault nor env has the secret, raise SecretNotFoundError."""
        from agent_core_credentials import secrets as _sec

        monkeypatch.setattr(_sec, "_open_store", lambda: type("S", (), {"get": lambda self, n: None})())
        monkeypatch.delenv("ABSENT_KEY", raising=False)

        with pytest.raises(SecretNotFoundError, match="ABSENT_KEY"):
            get("ABSENT_KEY")
