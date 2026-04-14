# Email CLI Design Spec

**Date:** 2026-04-14
**Status:** Approved
**Author:** Pepper + Jeff + Claude

## Overview

A Typer subcommand group on the existing `agent-core` CLI for managing Pepper's agentmail inbox. Five commands: check, read, send, reply, unread. Hardcoded to the agentmail SDK — no provider abstraction. MCP server deferred to ChannelProvider work.

## Structure

```
src/agent_core/email/
├── __init__.py
├── cli.py          # Typer subcommand group
└── client.py       # Shared agentmail client setup
```

Registered as a subcommand on the existing app:
```python
# In src/agent_core/cli.py
from agent_core.email.cli import email_app
app.add_typer(email_app, name="email")
```

## Commands

### `agent-core email check`

List recent messages in the inbox.

```bash
agent-core email check              # last 10 messages
agent-core email check --limit 20   # last 20
agent-core email check --unread     # only unread
```

Output: Rich table with columns: ID (truncated), From, Subject, Date, Unread marker (*)
Exit code: 0

### `agent-core email read <message_id>`

Read the full content of a specific email.

```bash
agent-core email read "<message_id>"
```

Output:
```
From: sender@example.com
To: pepper_ai@agentmail.to
Subject: Re: Hello
Date: 2026-04-14 19:00
Labels: received, unread
------------------------------------------------------------
[email body text]

Attachments: 2
  - file1.pdf
  - image.png
```

Exit code: 0 on success, 1 if not found

### `agent-core email send <to> <subject> <body>`

Send a new email.

```bash
agent-core email send "jeff@gmail.com" "Subject line" "Body text"
agent-core email send "jeff@gmail.com" "Subject" --body-file /tmp/email.txt
```

Options:
- `--body-file PATH` — read body from file instead of argument
- `--html` — treat body as HTML
- `--cc ADDRESS` — CC recipient
- `--dry-run` — preview without sending

Output: Confirmation with to, subject, from
Exit code: 0 on success, 1 on failure

### `agent-core email reply <message_id> <body>`

Reply to an existing email.

```bash
agent-core email reply "<message_id>" "Thanks for the update!"
agent-core email reply "<message_id>" --body-file /tmp/reply.txt
```

Behavior:
- Looks up original message for sender (reply-to address)
- Auto-prepends "Re: " to subject if not already there
- Sets In-Reply-To header for proper threading

Options:
- `--body-file PATH` — read body from file
- `--dry-run` — preview without sending

Output: Confirmation with to, subject, in-reply-to
Exit code: 0 on success, 1 on failure

### `agent-core email unread`

Quick count of unread messages.

```bash
agent-core email unread
```

Output: "3 unread messages" or "No unread messages"
Exit code: 0

## Client Module

`client.py` handles shared agentmail setup:

- Reads `AGENTMAIL_API_KEY` from environment variable
- Inbox ID: `pepper_ai@agentmail.to` (from `PEPPER_INBOX_ID` env var, defaults to `pepper_ai@agentmail.to`)
- Creates and returns the agentmail client
- Fails fast with clear error if API key is missing

## Dependencies

Add to `pyproject.toml`:
- `agentmail` — the SDK

## Auth

API key loaded from `AGENTMAIL_API_KEY` environment variable. For Pepper, this is set in `~/.pepper/.env`. The CLI loads it via `python-dotenv` (already a dependency).

## Pepper Skill Integration

A SKILL.md file in `~/.pepper/.claude/skills/email/` will reference the CLI:

```bash
uv run --directory E:/workspaces/ai/agents/agent_core agent-core email check
uv run --directory E:/workspaces/ai/agents/agent_core agent-core email read "<id>"
uv run --directory E:/workspaces/ai/agents/agent_core agent-core email send "<to>" "<subject>" "<body>"
uv run --directory E:/workspaces/ai/agents/agent_core agent-core email reply "<id>" "<body>"
uv run --directory E:/workspaces/ai/agents/agent_core agent-core email unread
```

## SDK Notes (from Pepper)

- Uses `client.inboxes.messages.list()`, `.get()`, `.send()`
- Inbox ID is the email address itself (`pepper_ai@agentmail.to`)
- `MessageItem` model fields: `message_id`, `from_`, `to`, `cc`, `bcc`, `subject`, `preview`, `labels`, `timestamp`, `attachments`, `in_reply_to`, `thread_id`
- Note: `from_` not `from` (Python reserved word)
- List model has `preview` only — full content via `.get()` for read view
- Use `rich` tables for inbox listing

## Testing

- `check` — verify table formatting with messages
- `read` — verify full message display
- `send --dry-run` — verify preview without sending
- `reply --dry-run` — verify reply threading
- `unread` — verify count
- Missing API key — graceful error message
- Invalid message ID — graceful error
- Empty inbox — clean "no messages" output

## Future

- MCP server when ChannelProvider is built
- Integration with Pepper's channel server for inbound email routing
