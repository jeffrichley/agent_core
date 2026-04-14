---
name: email
description: Manage Pepper's email inbox — check, read, send, and reply to emails at pepper_ai@agentmail.to
user-invocable: true
allowed-tools:
  - Bash(uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email *)
argument-hint: "[check|read|send|reply|unread] [args...]"
---

# Pepper's Email

Your email address is `pepper_ai@agentmail.to`. Use the commands below to manage your inbox.

**Important:** NEVER send emails to anyone other than Jeff without explicit permission. Check SOUL.md hard boundaries.

## Check inbox

```bash
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email check
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email check --limit 20
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email check --unread
```

## Read a message

```bash
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email read "<message_id>"
```

## Send email

```bash
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email send "<to>" "<subject>" "<body>"
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email send "<to>" "<subject>" --body-file /path/to/body.txt
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email send "<to>" "<subject>" "<body>" --cc "<cc_address>"
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email send "<to>" "<subject>" "<body>" --html
```

Always use `--dry-run` first when composing important emails:
```bash
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email send "<to>" "<subject>" "<body>" --dry-run
```

## Reply to email

```bash
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email reply "<message_id>" "<body>"
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email reply "<message_id>" --body-file /path/to/reply.txt
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email reply "<message_id>" "<body>" --dry-run
```

## Quick unread count

```bash
uv run --no-sync --directory E:/workspaces/ai/agents/agent_core agent-core email unread
```

## Workflow

1. Check for new mail: `agent-core email check --unread`
2. Read anything important: `agent-core email read "<id>"`
3. Draft a reply with `--dry-run` first
4. If it looks good, send without `--dry-run`
5. For long replies, write to a temp file and use `--body-file`
