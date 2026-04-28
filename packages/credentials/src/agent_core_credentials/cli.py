"""agent-core creds CLI — manage stored credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from pykeepass import create_database  # type: ignore[import-untyped]
from rich import print as rprint

from agent_core_credentials import default_vault_path
from agent_core_credentials.store import CredentialStore

creds_app = typer.Typer(
    name="creds",
    help="Manage stored credentials.",
    no_args_is_help=True,
)

# Default paths — overridden in tests via monkeypatch
_vault_path = default_vault_path()
_env_path = Path.home() / ".agent-core" / ".env"


def _load_env() -> None:
    """Load .env into the process environment if not already set."""
    if os.environ.get("AGENT_CORE_VAULT_PASSWORD"):
        return
    if _env_path.exists():
        for line in _env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def _store() -> CredentialStore:
    """Create a CredentialStore for the current vault path."""
    _load_env()
    return CredentialStore(_vault_path)


@creds_app.command("init")
def init_vault() -> None:
    """Initialize the credential vault with a master password."""
    if _vault_path.exists():
        rprint(f"[red]Credential vault already exists at {_vault_path}[/red]")
        raise typer.Exit(1)

    password = typer.prompt("Master password", hide_input=True)
    confirm = typer.prompt("Confirm password", hide_input=True)

    if password != confirm:
        rprint("[red]Passwords do not match.[/red]")
        raise typer.Exit(1)

    _vault_path.parent.mkdir(parents=True, exist_ok=True)
    create_database(str(_vault_path), password=password)

    # Append to .env so agent-core and future CLI calls can unlock the vault
    with open(_env_path, "a", encoding="utf-8") as f:
        f.write(f"\nAGENT_CORE_VAULT_PASSWORD={password}\n")

    rprint(f"[green]Created credential vault at {_vault_path}[/green]")


@creds_app.command("set")
def set_credential(
    service: str = typer.Argument(help="Service name (e.g. apex, etsy)"),
) -> None:
    """Store a credential with secure password input."""
    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)
    url = typer.prompt("URL (optional)", default="")
    notes = typer.prompt("Notes (optional)", default="")

    try:
        _store().set(service, username, password, url, notes)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    rprint(f"[green]Saved {service}[/green]")


@creds_app.command("get")
def get_credential(
    service: str = typer.Argument(help="Service name to retrieve"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON with password"),
) -> None:
    """Retrieve a stored credential."""
    try:
        cred = _store().get(service)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    if cred is None:
        rprint(f"[red]No credential found for {service}[/red]")
        raise typer.Exit(1)

    if as_json:
        print(
            json.dumps(
                {
                    "service": cred.service,
                    "username": cred.username,
                    "password": cred.password,
                    "url": cred.url,
                    "notes": cred.notes,
                }
            )
        )
    else:
        rprint(f"[bold]Service:[/bold]  {cred.service}")
        rprint(f"[bold]Username:[/bold] {cred.username}")
        if cred.url:
            rprint(f"[bold]URL:[/bold]      {cred.url}")
        if cred.notes:
            rprint(f"[bold]Notes:[/bold]    {cred.notes}")


@creds_app.command("list")
def list_credentials(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all stored credentials."""
    try:
        summaries = _store().list()
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    if as_json:
        print(
            json.dumps(
                [
                    {"service": s.service, "username": s.username, "url": s.url}
                    for s in summaries
                ]
            )
        )
    else:
        if not summaries:
            rprint("[yellow]No credentials stored.[/yellow]")
            return
        for s in summaries:
            rprint(s.service)


@creds_app.command("delete")
def delete_credential(
    service: str = typer.Argument(help="Service name to delete"),
) -> None:
    """Delete a stored credential."""
    try:
        deleted = _store().delete(service)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    if not deleted:
        rprint(f"[red]No credential found for {service}[/red]")
        raise typer.Exit(1)

    rprint(f"[green]Deleted {service}[/green]")
