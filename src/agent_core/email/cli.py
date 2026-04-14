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


@email_app.command("check")
def check(
    limit: int = typer.Option(10, help="Number of messages to show."),
    unread: bool = typer.Option(False, help="Show only unread messages."),
) -> None:
    """List recent messages in the inbox."""
    client = get_client()
    inbox_id = get_inbox_id()

    kwargs = {"limit": limit}
    if unread:
        kwargs["labels"] = "unread"

    response = client.inboxes.messages.list(inbox_id, **kwargs)

    if not response.messages:
        typer.echo("No messages")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("From", max_width=30)
    table.add_column("Subject", max_width=40)
    table.add_column("Date", max_width=20)
    table.add_column("", max_width=1)  # unread marker

    for msg in response.messages:
        marker = "*" if "unread" in (msg.labels or []) else ""
        msg_id = msg.message_id[:12] if msg.message_id else ""
        from_addr = msg.from_ or ""
        subject = msg.subject or "(no subject)"
        ts = msg.timestamp[:16] if msg.timestamp else ""
        table.add_row(msg_id, from_addr, subject, ts, marker)

    console.print(table)


@email_app.command("read")
def read(
    message_id: str = typer.Argument(help="ID of the message to read."),
) -> None:
    """Read the full content of a specific email."""
    client = get_client()
    inbox_id = get_inbox_id()

    try:
        msg = client.inboxes.messages.get(inbox_id, message_id)
    except Exception as e:
        typer.echo(f"Error: Could not read message {message_id}: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"From: {msg.from_}")
    typer.echo(f"To: {', '.join(msg.to) if msg.to else ''}")
    if msg.cc:
        typer.echo(f"CC: {', '.join(msg.cc)}")
    typer.echo(f"Subject: {msg.subject or '(no subject)'}")
    typer.echo(f"Date: {msg.timestamp}")
    typer.echo(f"Labels: {', '.join(msg.labels) if msg.labels else ''}")
    typer.echo("-" * 60)
    typer.echo(msg.text or msg.html or "(no body)")

    if msg.attachments:
        typer.echo(f"\nAttachments: {len(msg.attachments)}")
        for att in msg.attachments:
            filename = getattr(att, "filename", None) or getattr(att, "name", "unknown")
            typer.echo(f"  - {filename}")
