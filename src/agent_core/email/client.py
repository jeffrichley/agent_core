"""Shared agentmail client setup.

Loads API key from AGENTMAIL_API_KEY env var. Fails fast if missing.
Inbox ID defaults to pepper_ai@agentmail.to, overridable via PEPPER_INBOX_ID.
"""

import os

import typer


def get_client():
    """Create and return an AgentMail sync client.

    Reads AGENTMAIL_API_KEY from environment. Exits with error if missing.
    """
    from agentmail import AgentMail

    api_key = os.environ.get("AGENTMAIL_API_KEY")
    if not api_key:
        typer.echo("Error: AGENTMAIL_API_KEY environment variable not set.", err=True)
        raise SystemExit(1)

    return AgentMail(api_key=api_key)


def get_inbox_id() -> str:
    """Return the inbox ID (email address) from env or default."""
    return os.environ.get("PEPPER_INBOX_ID", "pepper_ai@agentmail.to")
