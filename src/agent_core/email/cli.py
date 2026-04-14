"""Email CLI subcommands for managing an agentmail inbox."""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agent_core.email.client import get_client, get_inbox_id

email_app = typer.Typer(
    name="email",
    help="Manage an agentmail inbox — check, read, send, reply.",
    no_args_is_help=True,
)

console = Console()


@email_app.command("unread")
def unread() -> None:
    """Quick count of unread messages."""
    client = get_client()
    inbox_id = get_inbox_id()

    response = client.inboxes.messages.list(inbox_id, labels="unread")
    count = response.count

    if count == 0:
        typer.echo("No unread messages")
    elif count == 1:
        typer.echo("1 unread message")
    else:
        typer.echo(f"{count} unread messages")
