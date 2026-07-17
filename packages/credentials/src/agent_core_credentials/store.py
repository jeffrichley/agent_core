"""KeePass credential store — pykeepass wrapper."""

from __future__ import annotations

import os
from pathlib import Path

from pykeepass import PyKeePass, create_database  # type: ignore[import-untyped]

from agent_core_credentials.models import Credential, CredentialSummary


class CredentialStore:
    """CRUD operations on an encrypted KeePass vault.

    Each operation opens the vault, performs the action, and saves/closes.
    The master password comes from the AGENT_CORE_VAULT_PASSWORD env var.

    Args:
        vault_path: Path to the .kdbx file. Created on first write.
    """

    def __init__(self, vault_path: Path) -> None:
        """Initialize the store with a path to the vault file."""
        self.vault_path = vault_path

    def _get_password(self) -> str:
        """Read the master password from the environment."""
        password = os.environ.get("AGENT_CORE_VAULT_PASSWORD")
        if not password:
            msg = (
                "AGENT_CORE_VAULT_PASSWORD environment variable is not set. "
                "Add it to ~/.agent-core/.env or set it in your shell."
            )
            raise ValueError(msg)
        return password

    def _open(self) -> PyKeePass:
        """Open the vault, creating it if it doesn't exist."""
        password = self._get_password()
        if not self.vault_path.exists():
            return create_database(str(self.vault_path), password=password)
        return PyKeePass(str(self.vault_path), password=password)

    def get(self, service: str) -> Credential | None:
        """Retrieve a credential by service name."""
        kp = self._open()
        entry = kp.find_entries(title=service, first=True)
        if entry is None:
            return None
        return Credential(
            service=entry.title,
            username=entry.username or "",
            password=entry.password or "",
            url=entry.url or "",
            notes=entry.notes or "",
            mtime=entry.mtime,
        )

    def set(
        self,
        service: str,
        username: str,
        password: str,
        url: str = "",
        notes: str = "",
    ) -> None:
        """Store or overwrite a credential."""
        kp = self._open()
        existing = kp.find_entries(title=service, first=True)
        if existing:
            existing.username = username
            existing.password = password
            existing.url = url
            existing.notes = notes
        else:
            kp.add_entry(
                kp.root_group,
                title=service,
                username=username,
                password=password,
                url=url,
                notes=notes,
            )
        kp.save()

    def list(self) -> list[CredentialSummary]:
        """List all stored credentials without passwords."""
        kp = self._open()
        return [
            CredentialSummary(
                service=entry.title,
                username=entry.username or "",
                url=entry.url or "",
            )
            for entry in kp.entries
        ]

    def delete(self, service: str) -> bool:
        """Delete a credential by service name."""
        kp = self._open()
        entry = kp.find_entries(title=service, first=True)
        if entry is None:
            return False
        kp.delete_entry(entry)
        kp.save()
        return True
