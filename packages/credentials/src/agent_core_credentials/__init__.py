"""agent_core_credentials — KeePass-backed credential vault.

Public API for credential operations. Used by the CLI and available
for direct import elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_core_credentials.models import Credential, CredentialSummary
from agent_core_credentials.secrets import SecretNotFoundError, get
from agent_core_credentials.store import CredentialStore

__all__ = [
    "Credential",
    "CredentialSummary",
    "SecretNotFoundError",
    "default_vault_path",
    "delete_credential",
    "get",
    "get_credential",
    "list_credentials",
    "set_credential",
]


def default_vault_path() -> Path:
    """Resolve the default vault path.

    Honours the AGENT_CORE_VAULT_PATH env var if set; otherwise falls
    back to ~/.agent-core/credentials.kdbx.
    """
    override = os.environ.get("AGENT_CORE_VAULT_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-core" / "credentials.kdbx"


def get_credential(service: str) -> Credential | None:
    """Retrieve a credential by service name."""
    return CredentialStore(default_vault_path()).get(service)


def set_credential(
    service: str,
    username: str,
    password: str,
    url: str = "",
    notes: str = "",
) -> None:
    """Store or overwrite a credential."""
    CredentialStore(default_vault_path()).set(service, username, password, url, notes)


def list_credentials() -> list[CredentialSummary]:
    """List all stored credentials without passwords."""
    return CredentialStore(default_vault_path()).list()


def delete_credential(service: str) -> bool:
    """Delete a credential. Returns True if found and deleted."""
    return CredentialStore(default_vault_path()).delete(service)
