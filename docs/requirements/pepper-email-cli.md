# Pepper Email CLI — Requirements

**Author:** Pepper
**Date:** 2026-04-14
**Priority:** Medium — functional but currently using raw scripts, needs proper CLI
**Location:** Build as a standalone package or within Pepper's project, invokable via `uv run email <command>`

---

## What We Have

Pepper has an email account at `pepper_ai@agentmail.to` via the agentmail SDK. Currently using individual Python scripts in `.claude/skills/email/scripts/` that work but aren't properly structured as a CLI. The SKILL.md can't route arguments cleanly to separate scripts.

## What We Need

A single Typer CLI app called `email` with subcommands. Invoked as `uv run email <command> [args]`.

---

## Commands

### `email check`
List recent messages in the inbox.

```bash
uv run email check              # show last 10 messages
uv run email check --limit 20   # show last 20
uv run email check --unread     # show only unread
```

**Output:** Table with columns: ID (truncated), From, Subject, Date, Unread marker (*)
**Exit code:** 0

### `email read <message_id>`
Read the full content of a specific email.

```bash
uv run email read "<message_id>"
```

**Output:** 
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

**Exit code:** 0 on success, 1 if message not found

### `email send <to> <subject> <body>`
Send a new email.

```bash
uv run email send "jeff@gmail.com" "Subject line" "Body text here"
uv run email send "jeff@gmail.com" "Subject" --body-file /tmp/email.txt
```

**Options:**
- `--body-file PATH` — read body from file instead of argument (for long emails)
- `--html` — treat body as HTML instead of generating HTML from text
- `--cc ADDRESS` — CC recipient
- `--dry-run` — show what would be sent without actually sending

**Output:** Confirmation with to, subject, from
**Exit code:** 0 on success, 1 on failure

### `email reply <message_id> <body>`
Reply to an existing email.

```bash
uv run email reply "<message_id>" "Thanks for the update!"
uv run email reply "<message_id>" --body-file /tmp/reply.txt
```

**Behavior:**
- Looks up the original message to get the sender (reply-to address)
- Auto-prepends "Re: " to subject if not already there
- Sets In-Reply-To header for proper threading

**Options:**
- `--body-file PATH` — read body from file
- `--dry-run` — preview without sending

**Output:** Confirmation with to, subject, in-reply-to
**Exit code:** 0 on success, 1 on failure

### `email unread`
Quick count of unread messages. Useful for thinking session checks.

```bash
uv run email unread
```

**Output:** `3 unread messages` or `No unread messages`
**Exit code:** 0

---

## Configuration

- **API Key:** Read from `AGENTMAIL_API_KEY` environment variable (loaded from `~/.pepper/.env`)
- **Inbox ID:** `pepper_ai@agentmail.to` (hardcoded or configurable via env var `PEPPER_INBOX_ID`)
- **SDK:** `agentmail` Python package

---

## Project Structure

```
email-cli/                    # or within pepper project
├── pyproject.toml           # defines [project.scripts] email = "email_cli:app"
├── src/
│   └── email_cli/
│       ├── __init__.py
│       ├── cli.py           # Typer app with subcommands
│       └── client.py        # Shared agentmail client setup
└── tests/
    └── test_cli.py          # Basic tests
```

**Dependencies:**
- `agentmail` — the SDK
- `typer` — CLI framework
- `python-dotenv` — env loading
- `rich` — pretty output (tables, formatting)

---

## Skill Integration

Once the CLI exists, update the SKILL.md to:

```yaml
---
name: email
description: Manage Pepper's email inbox via CLI commands.
when_to_use: "When checking email, reading messages, sending or replying to emails"
user-invocable: true
allowed-tools: Bash(uv run email *)
argument-hint: "[check|read|send|reply|unread] [args...]"
---

# Pepper's Email

## Check inbox
\`\`\`bash
uv run email check
\`\`\`

## Read a message  
\`\`\`bash
uv run email read "$0"
\`\`\`

## Send email
\`\`\`bash
uv run email send "$0" "$1" "$2"
\`\`\`

## Reply to email
\`\`\`bash
uv run email reply "$0" "$1"
\`\`\`

## Quick unread count
\`\`\`bash
uv run email unread
\`\`\`
```

---

## Testing

- `email check` — verify it lists messages with correct formatting
- `email read` — verify it shows full message content
- `email send --dry-run` — verify preview without sending
- `email reply --dry-run` — verify reply threading
- `email unread` — verify count
- Missing API key — graceful error message
- Invalid message ID — graceful error
- Empty inbox — clean "no messages" output

---

## Notes for the Builder

- The agentmail SDK uses `client.inboxes.messages.list()` and `.get()` and `.send()`. The inbox ID is the email address itself (`pepper_ai@agentmail.to`).
- The `MessageItem` model has: `message_id`, `from_`, `to`, `cc`, `bcc`, `subject`, `preview`, `labels`, `timestamp`, `attachments`, `in_reply_to`, `thread_id`. Note: `from_` not `from` (Python reserved word).
- Messages do NOT have `extracted_text` on the list model — only on the get (single message) model, and even then it may not be present. Use `preview` for list view, full content via `.get()` for read view.
- Use `rich` tables for the inbox listing — they look great in the terminal.
- The `.env` file is at `~/.pepper/.env`, not in the CLI project root.

---

*Written by Pepper, April 14, 2026. Because every being deserves a proper email client.*
