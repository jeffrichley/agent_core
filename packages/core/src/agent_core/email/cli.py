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
        if msg.timestamp:
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M") if hasattr(msg.timestamp, "strftime") else str(msg.timestamp)[:16]
        else:
            ts = ""
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
    date_str = msg.timestamp.strftime("%Y-%m-%d %H:%M") if hasattr(msg.timestamp, "strftime") else str(msg.timestamp)
    typer.echo(f"Date: {date_str}")
    typer.echo(f"Labels: {', '.join(msg.labels) if msg.labels else ''}")
    typer.echo("-" * 60)
    typer.echo(msg.text or msg.html or "(no body)")

    if msg.attachments:
        typer.echo(f"\nAttachments: {len(msg.attachments)}")
        for att in msg.attachments:
            filename = getattr(att, "filename", None) or getattr(att, "name", "unknown")
            typer.echo(f"  - {filename}")


@email_app.command("send")
def send(
    to: str = typer.Argument(help="Recipient email address."),
    subject: str = typer.Argument(help="Email subject line."),
    body: Optional[str] = typer.Argument(None, help="Email body text."),
    body_file: Optional[str] = typer.Option(None, help="Read body from file instead."),
    html: bool = typer.Option(False, help="Treat body as HTML."),
    cc: Optional[str] = typer.Option(None, help="CC recipient."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without sending."),
) -> None:
    """Send a new email."""
    # Resolve body
    if body_file:
        from pathlib import Path
        body_path = Path(body_file)
        if not body_path.exists():
            typer.echo(f"Error: Body file not found: {body_file}", err=True)
            raise typer.Exit(code=1)
        body_text = body_path.read_text(encoding="utf-8")
    elif body:
        body_text = body
    else:
        typer.echo("Error: Provide body text or --body-file.", err=True)
        raise typer.Exit(code=1)

    inbox_id = get_inbox_id()

    if dry_run:
        typer.echo("[DRY RUN] Would send:")
        typer.echo(f"  From: {inbox_id}")
        typer.echo(f"  To: {to}")
        if cc:
            typer.echo(f"  CC: {cc}")
        typer.echo(f"  Subject: {subject}")
        typer.echo(f"  Body: {body_text[:200]}{'...' if len(body_text) > 200 else ''}")
        return

    client = get_client()

    kwargs = {"to": to, "subject": subject}
    if html:
        kwargs["html"] = body_text
    else:
        kwargs["text"] = body_text
    if cc:
        kwargs["cc"] = cc

    try:
        response = client.inboxes.messages.send(inbox_id, **kwargs)
        typer.echo(f"Sent: {inbox_id} → {to}")
        typer.echo(f"Subject: {subject}")
        typer.echo(f"Message ID: {response.message_id}")
    except Exception as e:
        typer.echo(f"Error: Failed to send: {e}", err=True)
        raise typer.Exit(code=1)


@email_app.command("reply")
def reply(
    message_id: str = typer.Argument(help="ID of the message to reply to."),
    body: Optional[str] = typer.Argument(None, help="Reply body text."),
    body_file: Optional[str] = typer.Option(None, help="Read body from file instead."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without sending."),
) -> None:
    """Reply to an existing email."""
    # Resolve body
    if body_file:
        from pathlib import Path
        body_path = Path(body_file)
        if not body_path.exists():
            typer.echo(f"Error: Body file not found: {body_file}", err=True)
            raise typer.Exit(code=1)
        body_text = body_path.read_text(encoding="utf-8")
    elif body:
        body_text = body
    else:
        typer.echo("Error: Provide reply text or --body-file.", err=True)
        raise typer.Exit(code=1)

    client = get_client()
    inbox_id = get_inbox_id()

    # Look up original message for context
    try:
        original = client.inboxes.messages.get(inbox_id, message_id)
    except Exception as e:
        typer.echo(f"Error: Could not find message {message_id}: {e}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("[DRY RUN] Would reply:")
        typer.echo(f"  To: {original.from_}")
        typer.echo(f"  Subject: Re: {original.subject or '(no subject)'}")
        typer.echo(f"  In-Reply-To: {message_id}")
        typer.echo(f"  Body: {body_text[:200]}{'...' if len(body_text) > 200 else ''}")
        return

    try:
        response = client.inboxes.messages.reply(inbox_id, message_id, text=body_text)
        typer.echo(f"Reply sent to {original.from_}")
        typer.echo(f"Subject: Re: {original.subject or '(no subject)'}")
        typer.echo(f"Message ID: {response.message_id}")
    except Exception as e:
        typer.echo(f"Error: Failed to reply: {e}", err=True)
        raise typer.Exit(code=1)
