"""Shared agentmail client setup.

Loads API key from AGENTMAIL_API_KEY env var. Fails fast if missing.
Inbox ID defaults to pepper_ai@agentmail.to, overridable via PEPPER_INBOX_ID.
"""

import os
from pathlib import Path

import typer
from dotenv import load_dotenv

# Load .env from ~/.pepper/.env if it exists (for API keys)
_pepper_env = Path.home() / ".pepper" / ".env"
if _pepper_env.exists():
    load_dotenv(_pepper_env)


def get_client():
    """Create and return an AgentMail sync client.

    Reads AGENTMAIL_API_KEY from environment (or ~/.pepper/.env). Exits with error if missing.
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
