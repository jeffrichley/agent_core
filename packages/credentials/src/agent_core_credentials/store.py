"""KeePass credential store — pykeepass wrapper."""

from __future__ import annotations

from pathlib import Path

from pykeepass import PyKeePass, create_database  # type: ignore[import-untyped]

from agent_core_credentials.master_password import get_master_password
from agent_core_credentials.models import Credential, CredentialSummary


class CredentialStore:
    """CRUD operations on an encrypted KeePass vault.

    Each operation opens the vault, performs the action, and saves/closes.
    The master password is fetched from the OS keyring or owner-only encrypted
    file fallback via ``get_master_password()``.

    Args:
        vault_path: Path to the .kdbx file. Created on first write.
        _master_password: Optional DI override for the master password (tests).
    """

    def __init__(self, vault_path: Path, *, _master_password: str | None = None) -> None:
        """Initialize the store with a path to the vault file."""
        self.vault_path = vault_path
        self._password_override = _master_password

    def _get_password(self) -> str:
        """Return the master password from override DI or the active backend."""
        if self._password_override is not None:
            return self._password_override
        result = get_master_password(self.vault_path)
        if not result:
            msg = (
                "No master password found. Run 'agent-core-creds init' to initialize "
                "the vault, or ensure the OS keyring is accessible."
            )
            raise ValueError(msg)
        return result

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
