# DiscordEndpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `agent-core-discord` as a separate workspace package providing a `DiscordEndpoint` adapter that bridges one Discord bot to one named bus agent (1:1).

**Architecture:** New uv-workspace package `agent-core-discord`. `DiscordEndpoint` implements the `agent_core.bus.protocol.Endpoint` Protocol. Inbound Discord messages and user reactions become `TextMessage`/`Event` envelopes addressed to the agent's bus name. Outbound `ToolInvocation` envelopes from the agent dispatch to 8 `discord.py` tool handlers (`send`, `edit`, `react`, `fetch`, `download_attachments`, `list_channels`, `get_channel_info`, plus a `send_briefing` decision). Replies are `Acknowledgment` envelopes with JSON results. Module-level `_active_endpoints` registry mirrors the scheduler pattern.

**Tech Stack:** Python 3.12+, uv workspace, hatchling, discord.py, python-dotenv (already a transitive of core), pydantic, pytest, pytest-asyncio.

**Spec:** [`docs/superpowers/specs/2026-04-28-discord-endpoint-design.md`](../specs/2026-04-28-discord-endpoint-design.md)

---

## File Structure

**Create — new package `packages/agent-core-discord/`:**

- `pyproject.toml` — package metadata, deps (`discord.py>=2.4`, `python-dotenv>=1.0.0`, `pydantic>=2.0`, `agent-core` workspace), scripts none, hatchling backend.
- `towncrier.toml` — same shape as `packages/credentials/towncrier.toml`.
- `CHANGELOG.md` — empty starter.
- `changelog.d/+discord-endpoint.added.md` — towncrier fragment.
- `src/agent_core_discord/__init__.py` — exports `DiscordEndpoint`.
- `src/agent_core_discord/access.py` — DM policy + channel allowlist gate, ack-emoji helper. ~130 lines projected.
- `src/agent_core_discord/args.py` — Pydantic args models for the 8 tools. ~90 lines projected.
- `src/agent_core_discord/endpoint.py` — `DiscordEndpoint` class (lifecycle, inbound handlers, presence tracking, tool dispatch, tool handlers). ~500 lines projected.
- `tests/__init__.py` — empty.
- `tests/conftest.py` — `_FakeDiscordClient` and friends.
- `tests/test_access.py` — gate logic.
- `tests/test_endpoint_lifecycle.py` — start/stop, env loading, registry.
- `tests/test_endpoint_inbound.py` — on_message + on_reaction_add.
- `tests/test_endpoint_outbound.py` — 8 tools.
- `tests/test_integration.py` — optional real-bot smoke (skipped unless `DISCORD_TEST_TOKEN` is set).

**Modify:**

- `pyproject.toml` (root) — add `agent-core-discord = { workspace = true }` to `[tool.uv.sources]`. `members = ["packages/*"]` already picks up the new dir automatically.
- `docs/ROADMAP.md` (post-PR) — mark sub-project E v1 shipped with PR number/SHA. Done in Task 10.

**No edits to:** `packages/core/`, `packages/credentials/`, `packages/notify/`, or any existing source. The new package is leaf-only.

---

## Task 1: Pre-flight + branch + new package skeleton

**Files:**
- Create: `packages/agent-core-discord/pyproject.toml`
- Create: `packages/agent-core-discord/towncrier.toml`
- Create: `packages/agent-core-discord/CHANGELOG.md`
- Create: `packages/agent-core-discord/src/agent_core_discord/__init__.py`
- Create: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (placeholder)
- Create: `packages/agent-core-discord/tests/__init__.py`
- Modify: `pyproject.toml` (root)

- [ ] **Step 1: Confirm clean tree, branch off main**

```bash
git status
git checkout main
git pull origin main
git checkout -b feat/discord-endpoint
```

Expected: clean tree on main, branch created.

- [ ] **Step 2: Verify baseline tests pass**

```bash
uv run --no-sync pytest -q
```

Expected: 284 passed / 2 skipped (post-scheduler-merge baseline). Record exact numbers; you'll re-check at the end.

- [ ] **Step 3: Create the package directory structure**

```bash
mkdir -p packages/agent-core-discord/src/agent_core_discord
mkdir -p packages/agent-core-discord/tests
mkdir -p packages/agent-core-discord/changelog.d
```

- [ ] **Step 4: Write `packages/agent-core-discord/pyproject.toml`**

```toml
[project]
name = "agent-core-discord"
version = "0.1.0"
description = "Discord bot adapter for the agent-core bus — one bot per agent (1:1)."
requires-python = ">=3.12"
dependencies = [
    "agent-core",
    "discord.py>=2.4",
    "python-dotenv>=1.0.0",
    "pydantic>=2.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_discord"]
```

- [ ] **Step 5: Write `packages/agent-core-discord/towncrier.toml`**

```toml
[tool.towncrier]
name = "agent-core-discord"
package = "agent_core_discord"
package_dir = "src"
directory = "changelog.d"
filename = "CHANGELOG.md"
title_format = "## {version} ({project_date})"
issue_format = "[#{issue}](https://github.com/jeffrichley/agent_core/pull/{issue})"

[[tool.towncrier.type]]
directory = "added"
name = "Added"
showcontent = true

[[tool.towncrier.type]]
directory = "changed"
name = "Changed"
showcontent = true

[[tool.towncrier.type]]
directory = "deprecated"
name = "Deprecated"
showcontent = true

[[tool.towncrier.type]]
directory = "removed"
name = "Removed"
showcontent = true

[[tool.towncrier.type]]
directory = "fixed"
name = "Fixed"
showcontent = true

[[tool.towncrier.type]]
directory = "security"
name = "Security"
showcontent = true
```

- [ ] **Step 6: Write `packages/agent-core-discord/CHANGELOG.md`**

```markdown
# agent-core-discord changelog

<!-- towncrier release notes start -->
```

- [ ] **Step 7: Write `packages/agent-core-discord/src/agent_core_discord/__init__.py`**

```python
"""agent-core-discord — Discord bot adapter for the agent-core bus.

One DiscordEndpoint instance per Discord bot. Bridges one bot to one named
agent on the bus (1:1 mapping). See the design doc at
docs/superpowers/specs/2026-04-28-discord-endpoint-design.md for details.
"""

from agent_core_discord.endpoint import DiscordEndpoint

__all__ = ["DiscordEndpoint"]
```

- [ ] **Step 8: Write a placeholder `endpoint.py` so imports resolve**

Create `packages/agent-core-discord/src/agent_core_discord/endpoint.py`:

```python
"""DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.

Implementation lands in subsequent tasks. See the design doc and plan for
the task breakdown."""

from __future__ import annotations


class DiscordEndpoint:
    """Placeholder — real implementation in Task 3."""

    def __init__(self, *, name: str, target: str, token_env: str) -> None:
        self.name = name
        self.target = target
        self.token_env = token_env
```

- [ ] **Step 9: Write `packages/agent-core-discord/tests/__init__.py`**

(Empty file — just creates the package.)

- [ ] **Step 10: Add the new package to root workspace**

Open `pyproject.toml` (repo root). Find the `[tool.uv.sources]` block and add:

```toml
[tool.uv.sources]
agent-core = { workspace = true }
agent-core-notify = { workspace = true }
agent-core-credentials = { workspace = true }
agent-core-discord = { workspace = true }
```

(`[tool.uv.workspace]` already has `members = ["packages/*"]`, so the new dir is auto-discovered.)

- [ ] **Step 11: Sync and verify imports resolve**

```bash
uv sync
uv run --no-sync python -c "from agent_core_discord import DiscordEndpoint; ep = DiscordEndpoint(name='discord-test', target='agent-test', token_env='X'); print('ok:', ep.name)"
```

Expected: `ok: discord-test`. If `discord.py` doesn't resolve cleanly, try `discord.py>=2.4.0` (drop the bare `>=2.4` if the resolver complains).

- [ ] **Step 12: Verify baseline tests still pass**

```bash
uv run --no-sync pytest -q
```

Expected: same baseline (284 passed / 2 skipped, no new errors, no test count change).

- [ ] **Step 13: Verify import-linter still passes**

```bash
uv run --no-sync lint-imports
```

Expected: 1 contract kept, 0 broken.

- [ ] **Step 14: Commit**

```bash
git add packages/agent-core-discord pyproject.toml uv.lock
git commit -m "build(discord): scaffold agent-core-discord package"
```

---

## Task 2: Access gate (port from Pepper)

**Files:**
- Create: `packages/agent-core-discord/src/agent_core_discord/access.py`
- Create: `packages/agent-core-discord/tests/test_access.py`

The access gate is pure logic with no Discord dependency — it takes the access config + the inbound Discord message metadata and returns a yes/no. Easy to TDD in isolation.

- [ ] **Step 1: Write the failing tests**

Create `packages/agent-core-discord/tests/test_access.py`:

```python
"""Tests for the DM-policy + channel-allowlist access gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_core_discord.access import (
    AccessConfig,
    InboundContext,
    gate_message,
    load_access_config,
)


def test_load_access_config_returns_defaults_for_missing_path():
    cfg = load_access_config(None)
    assert cfg.dm_policy == "open"
    assert cfg.allow_from == []
    assert cfg.channels == {}
    assert cfg.ack_reaction == "👀"


def test_load_access_config_returns_defaults_for_missing_file(tmp_path):
    cfg = load_access_config(tmp_path / "missing.json")
    assert cfg.dm_policy == "open"
    assert cfg.ack_reaction == "👀"


def test_load_access_config_parses_json(tmp_path):
    p = tmp_path / "access.json"
    p.write_text(
        json.dumps(
            {
                "dmPolicy": "allowlist",
                "allowFrom": ["100"],
                "channels": {"200": {}},
                "ackReaction": "👁️",
            }
        ),
        encoding="utf-8",
    )
    cfg = load_access_config(p)
    assert cfg.dm_policy == "allowlist"
    assert cfg.allow_from == ["100"]
    assert cfg.channels == {"200": {}}
    assert cfg.ack_reaction == "👁️"


def _ctx(*, is_dm: bool, author_id: str = "100", channel_id: str = "200") -> InboundContext:
    return InboundContext(
        is_dm=is_dm, author_id=author_id, channel_id=channel_id, is_bot=False
    )


def test_gate_blocks_bot_authors_unconditionally():
    cfg = AccessConfig(dm_policy="open")
    ctx = InboundContext(is_dm=False, author_id="100", channel_id="200", is_bot=True)
    assert gate_message(cfg, ctx) is False


def test_gate_open_dm_policy_allows_any_dm():
    cfg = AccessConfig(dm_policy="open")
    assert gate_message(cfg, _ctx(is_dm=True)) is True


def test_gate_deny_dm_policy_blocks_dms():
    cfg = AccessConfig(dm_policy="deny")
    assert gate_message(cfg, _ctx(is_dm=True)) is False


def test_gate_allowlist_dm_policy_passes_for_listed_user():
    cfg = AccessConfig(dm_policy="allowlist", allow_from=["100"])
    assert gate_message(cfg, _ctx(is_dm=True, author_id="100")) is True


def test_gate_allowlist_dm_policy_blocks_unlisted_user():
    cfg = AccessConfig(dm_policy="allowlist", allow_from=["100"])
    assert gate_message(cfg, _ctx(is_dm=True, author_id="999")) is False


def test_gate_with_no_channel_map_accepts_all_guild_channels():
    cfg = AccessConfig(dm_policy="open", channels={})
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="ANY")) is True


def test_gate_with_channel_allowlist_accepts_only_listed():
    cfg = AccessConfig(dm_policy="open", channels={"200": {}})
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="200")) is True
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="201")) is False


def test_gate_dm_policy_does_not_apply_to_guild_messages():
    """A 'deny' DM policy still allows guild channel messages."""
    cfg = AccessConfig(dm_policy="deny")
    assert gate_message(cfg, _ctx(is_dm=False, channel_id="200")) is True


def test_gate_allowlist_dm_policy_does_not_block_guild_messages():
    """allowlist DM policy applies only to DMs, not guild posts."""
    cfg = AccessConfig(dm_policy="allowlist", allow_from=["100"])
    assert gate_message(cfg, _ctx(is_dm=False, author_id="999", channel_id="200")) is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_access.py -v
```

Expected: ImportError for `agent_core_discord.access`.

- [ ] **Step 3: Implement `access.py`**

Create `packages/agent-core-discord/src/agent_core_discord/access.py`:

```python
"""DM-policy + channel-allowlist access gate.

Ported from Pepper's `pepper/integrations/discord/access.py`. The shape of
the JSON config (dmPolicy / allowFrom / channels / ackReaction) is preserved
verbatim so existing Pepper access configs migrate without rewrite.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

DmPolicy = Literal["open", "deny", "allowlist"]


@dataclass
class AccessConfig:
    """Validated access policy for a single Discord bot."""

    dm_policy: DmPolicy = "open"
    allow_from: list[str] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    ack_reaction: str = "👀"


@dataclass
class InboundContext:
    """Snapshot of an inbound Discord event for gate evaluation."""

    is_dm: bool
    author_id: str
    channel_id: str
    is_bot: bool


def load_access_config(path: Path | str | None) -> AccessConfig:
    """Load access policy from a JSON file. Permissive defaults if missing/empty."""
    if path is None:
        return AccessConfig()
    p = Path(path).expanduser()
    if not p.exists():
        log.info("access config not found at %s; using permissive defaults", p)
        return AccessConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.exception("failed to parse access config at %s; using defaults", p)
        return AccessConfig()
    return AccessConfig(
        dm_policy=raw.get("dmPolicy", "open"),
        allow_from=list(raw.get("allowFrom", [])),
        channels=dict(raw.get("channels", {})),
        ack_reaction=raw.get("ackReaction", "👀"),
    )


def gate_message(cfg: AccessConfig, ctx: InboundContext) -> bool:
    """Return True if the inbound message passes the access gate.

    Bot-authored messages are always blocked. DMs go through dm_policy:
        - "open"      → allow.
        - "deny"      → block.
        - "allowlist" → allow only if author_id is in allow_from.
    Guild messages go through the channel allowlist if non-empty:
        - empty channels dict → allow all guild channels.
        - non-empty           → allow only if channel_id is a key.
    """
    if ctx.is_bot:
        return False
    if ctx.is_dm:
        if cfg.dm_policy == "open":
            return True
        if cfg.dm_policy == "deny":
            return False
        # allowlist
        return ctx.author_id in cfg.allow_from
    # Guild channel
    if not cfg.channels:
        return True
    return ctx.channel_id in cfg.channels
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_access.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Verify ruff and lint-imports**

```bash
uv run --no-sync ruff format packages/agent-core-discord/
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: ruff clean (after format); 1 contract kept, 0 broken.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/access.py packages/agent-core-discord/tests/test_access.py
git commit -m "feat(discord): port access gate (DM policy + channel allowlist)"
```

---

## Task 3: DiscordEndpoint scaffolding + lifecycle

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (replace placeholder)
- Create: `packages/agent-core-discord/tests/conftest.py`
- Create: `packages/agent-core-discord/tests/test_endpoint_lifecycle.py`

This task replaces the placeholder with a real `DiscordEndpoint` class: constructor with all params, `start()` that loads env file → reads token → instantiates a Discord client (via injectable factory), `stop()` that closes cleanly, the `_active_endpoints` registry, and `deliver()` as a stub raising `EndpointUnavailable` if not started.

- [ ] **Step 1: Write the conftest with a fake Discord client**

Create `packages/agent-core-discord/tests/conftest.py`:

```python
"""Shared test fixtures + a fake Discord client for the unit tests.

The fake mimics enough of discord.Client to exercise lifecycle, inbound
event dispatch, and outbound tool calls without a network."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class _FakeMessage:
    def __init__(self, *, id: str, channel_id: str, content: str = "", author=None):
        self.id = id
        self.channel_id = channel_id
        self.content = content
        self.author = author
        self.reactions: list[str] = []
        self.edits: list[dict[str, Any]] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)

    async def remove_reaction(self, emoji: str, user: Any) -> None:
        if emoji in self.reactions:
            self.reactions.remove(emoji)

    async def edit(self, *, content: str | None = None, embeds: list | None = None) -> None:
        self.edits.append({"content": content, "embeds": embeds})


class _FakeChannel:
    def __init__(self, *, id: str, name: str = "", channel_type: str = "text", guild_id: str | None = None):
        self.id = id
        self.name = name
        self.type = channel_type
        self.guild_id = guild_id
        self.topic = ""
        self.nsfw = False
        self.sent: list[dict[str, Any]] = []
        self._messages: dict[str, _FakeMessage] = {}
        self._typing_count = 0

    def typing(self):
        ch = self

        class _T:
            async def __aenter__(self):
                ch._typing_count += 1
                return None

            async def __aexit__(self, *exc):
                ch._typing_count -= 1
                return None

        return _T()

    async def send(
        self,
        content: str | None = None,
        *,
        embeds: list | None = None,
        reference: Any = None,
        files: list | None = None,
    ) -> _FakeMessage:
        new_id = f"new-{len(self.sent) + 1}"
        msg = _FakeMessage(id=new_id, channel_id=self.id, content=content or "")
        self._messages[new_id] = msg
        self.sent.append(
            {
                "content": content,
                "embeds": embeds,
                "reference": reference,
                "files": files,
                "message_id": new_id,
            }
        )
        return msg

    async def fetch_message(self, message_id: str) -> _FakeMessage | None:
        return self._messages.get(message_id)

    def history(self, limit: int = 50, before: Any = None):
        async def _gen():
            for m in list(self._messages.values())[:limit]:
                yield m

        return _gen()


class _FakeGuild:
    def __init__(self, *, id: str, channels: list[_FakeChannel]):
        self.id = id
        self.channels = channels


class _FakeUser:
    def __init__(self, *, id: str, name: str = "tester", bot: bool = False, display_name: str | None = None):
        self.id = id
        self.name = name
        self.bot = bot
        self.display_name = display_name or name


class _FakeDiscordClient:
    """Lightweight stand-in for discord.Client.

    Tests construct a client, register channels/guilds, then drive event
    dispatch by calling the on_message / on_reaction_add hooks the endpoint
    registers via @client.event."""

    def __init__(self, *, intents: Any = None):
        self.user = _FakeUser(id="bot-1", name="testbot", bot=True)
        self._channels: dict[str, _FakeChannel] = {}
        self._guilds: dict[str, _FakeGuild] = {}
        self._closed = False
        self._handlers: dict[str, Callable] = {}
        self._on_ready_event = asyncio.Event()

    def event(self, fn: Callable) -> Callable:
        """Decorator @client.event — registers the handler by function name."""
        self._handlers[fn.__name__] = fn
        return fn

    def get_channel(self, channel_id: int | str) -> _FakeChannel | None:
        return self._channels.get(str(channel_id))

    async def fetch_channel(self, channel_id: int | str) -> _FakeChannel | None:
        return self._channels.get(str(channel_id))

    @property
    def guilds(self):
        return list(self._guilds.values())

    async def start(self, token: str) -> None:
        # Set on_ready immediately for tests; tests can call client._fire('on_ready')
        # explicitly if they need to coordinate timing.
        self._on_ready_event.set()
        if "on_ready" in self._handlers:
            await self._handlers["on_ready"]()

    async def close(self) -> None:
        self._closed = True

    async def fire(self, event_name: str, *args) -> None:
        """Test helper: invoke a registered handler."""
        h = self._handlers.get(event_name)
        if h is not None:
            await h(*args)

    def add_channel(self, ch: _FakeChannel) -> None:
        self._channels[ch.id] = ch

    def add_guild(self, g: _FakeGuild) -> None:
        self._guilds[g.id] = g
        for ch in g.channels:
            self._channels[ch.id] = ch
```

- [ ] **Step 2: Write the failing lifecycle tests**

Create `packages/agent-core-discord/tests/test_endpoint_lifecycle.py`:

```python
"""Tests for DiscordEndpoint construction, start(), stop(), and registry."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_core.bus.protocol import Endpoint
from agent_core_discord.endpoint import DiscordEndpoint, _active_endpoints


class _FakeBusHandle:
    async def publish(self, *a, **kw): ...
    async def ack(self, *a, **kw): ...
    async def nack(self, *a, **kw): ...
    def endpoints(self): return []


def test_endpoint_satisfies_endpoint_protocol():
    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    assert isinstance(ep, Endpoint)


def test_endpoint_exposes_required_attrs():
    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    assert ep.name == "discord-test"
    assert ep.target == "agent-test"
    assert ep.token_env == "X"


def test_endpoint_default_attachments_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    assert "agent-core" in str(ep.attachments_dir)
    assert "discord-test" in str(ep.attachments_dir)


def test_endpoint_custom_attachments_dir(tmp_path):
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X",
        attachments_dir=str(tmp_path / "att"),
    )
    assert ep.attachments_dir == tmp_path / "att"


def test_endpoint_tilde_expansion_for_paths():
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X",
        env_file="~/.test/.env",
        access_config_path="~/.test/access.json",
    )
    assert "~" not in str(ep.env_file)
    assert "~" not in str(ep.access_config_path)


@pytest.mark.asyncio
async def test_start_raises_when_token_env_var_missing(monkeypatch):
    monkeypatch.delenv("DISCORD_TEST_TOKEN_MISSING", raising=False)
    ep = DiscordEndpoint(
        name="discord-test", target="agent-test", token_env="DISCORD_TEST_TOKEN_MISSING"
    )
    with pytest.raises(RuntimeError, match="DISCORD_TEST_TOKEN_MISSING"):
        await ep.start(_FakeBusHandle())


@pytest.mark.asyncio
async def test_start_loads_env_file_into_environ(tmp_path, monkeypatch):
    """If env_file is set, python-dotenv populates os.environ before token lookup."""
    from tests.conftest import _FakeDiscordClient

    monkeypatch.delenv("DISCORD_FROM_FILE_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("DISCORD_FROM_FILE_TOKEN=tok-from-file\n", encoding="utf-8")

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="DISCORD_FROM_FILE_TOKEN",
        env_file=str(env),
        _client_factory=lambda **kw: _FakeDiscordClient(**kw),
    )
    await ep.start(_FakeBusHandle())
    try:
        assert os.environ["DISCORD_FROM_FILE_TOKEN"] == "tok-from-file"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_start_registers_in_active_endpoints(monkeypatch):
    from tests.conftest import _FakeDiscordClient

    monkeypatch.setenv("X_TOK", "tok")
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: _FakeDiscordClient(**kw),
    )
    await ep.start(_FakeBusHandle())
    try:
        assert _active_endpoints["discord-test"] is ep
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_stop_deregisters_and_closes_client(monkeypatch):
    from tests.conftest import _FakeDiscordClient

    monkeypatch.setenv("X_TOK", "tok")
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: _FakeDiscordClient(**kw),
    )
    await ep.start(_FakeBusHandle())
    fake = ep._client
    await ep.stop()
    assert "discord-test" not in _active_endpoints
    assert fake._closed is True


@pytest.mark.asyncio
async def test_deliver_raises_when_not_started():
    from agent_core.bus.envelope import Envelope, ToolInvocationPayload
    from agent_core.bus.protocol import EndpointUnavailable
    from datetime import datetime, timezone
    import uuid

    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    env = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        to="discord-test",
        kind="ToolInvocation",
        payload=ToolInvocationPayload(tool="send", args={}),
        created_at=datetime.now(timezone.utc),
    )
    with pytest.raises(EndpointUnavailable):
        await ep.deliver(env)
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_lifecycle.py -v
```

Expected: tests fail because the placeholder `DiscordEndpoint` doesn't have `start`/`stop`/`deliver`.

- [ ] **Step 4: Implement the real `DiscordEndpoint`**

Replace `packages/agent-core-discord/src/agent_core_discord/endpoint.py` with the lifecycle scaffolding:

```python
"""DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.

This module hosts the class and the module-level _active_endpoints registry
that lets discord.py event handlers find the live endpoint instance from
inside the asyncio loop.

Inbound (on_message, on_reaction_add) and outbound (8 tools dispatched via
ToolInvocation envelopes) handlers land in subsequent tasks; this scaffold
just owns lifecycle and dispatch entry points.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_core.bus.envelope import Envelope
from agent_core.bus.protocol import EndpointUnavailable

from agent_core_discord.access import AccessConfig, load_access_config

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


# Module-level registry. Lets discord.py event handlers look up the live
# endpoint by name. Populated in start(), drained in stop().
_active_endpoints: dict[str, "DiscordEndpoint"] = {}


def _default_attachments_dir(endpoint_name: str) -> Path:
    """Predictable default attachments root, no target-name parsing."""
    return (Path("~/.agent-core/attachments").expanduser() / endpoint_name).resolve()


class DiscordEndpoint:
    """Bus endpoint that bridges one Discord bot to one named agent (1:1)."""

    def __init__(
        self,
        *,
        name: str,
        target: str,
        token_env: str,
        env_file: str | Path | None = None,
        access_config_path: str | Path | None = None,
        attachments_dir: str | Path | None = None,
        _client_factory: Callable[..., Any] | None = None,
    ):
        self.name = name
        self.target = target
        self.token_env = token_env
        self.env_file: Path | None = (
            Path(env_file).expanduser() if env_file else None
        )
        self.access_config_path: Path | None = (
            Path(access_config_path).expanduser() if access_config_path else None
        )
        self.attachments_dir: Path = (
            Path(attachments_dir).expanduser()
            if attachments_dir
            else _default_attachments_dir(name)
        )
        self._client_factory = _client_factory  # test seam
        self._handle: "BusHandle | None" = None
        self._client: Any = None
        self._access: AccessConfig = AccessConfig()

    # --- Endpoint Protocol ---

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus

        # 1. Load env_file (if set).
        if self.env_file is not None and self.env_file.exists():
            from dotenv import load_dotenv

            load_dotenv(self.env_file, override=False)
            log.info("loaded env file: %s", self.env_file)

        # 2. Read the bot token. Fail fast if missing.
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"discord endpoint '{self.name}': env var "
                f"'{self.token_env}' is not set (env_file={self.env_file})"
            )

        # 3. Load access policy (or use permissive defaults).
        self._access = load_access_config(self.access_config_path)

        # 4. Create the Discord client. The factory seam lets tests inject a
        #    fake client without touching discord.py.
        if self._client_factory is None:
            import discord

            intents = discord.Intents.default()
            intents.message_content = True
            intents.reactions = True
            self._client = discord.Client(intents=intents)
        else:
            self._client = self._client_factory(intents=None)

        # 5. Wire event handlers (defined as methods; the @client.event decorator
        #    expects a name-tied function reference).
        self._client.event(self._make_on_message_handler())
        self._client.event(self._make_on_reaction_add_handler())

        # 6. Register in the live endpoint map BEFORE awaiting client.start, so
        #    racing on_ready callbacks find us. Pop-on-failure below.
        _active_endpoints[self.name] = self

        # 7. Connect the bot. discord.Client.start() runs the event loop until
        #    closed; tests' fake client returns immediately after on_ready.
        try:
            # In production we'd schedule this with asyncio.create_task and await
            # an on_ready event. For test ergonomics, start() awaits directly —
            # the fake's start() is a no-op short-circuit.
            await self._client.start(token)
        except BaseException:
            _active_endpoints.pop(self.name, None)
            try:
                await self._client.close()
            except Exception:
                log.exception("rollback close() failed during start()")
            self._client = None
            self._handle = None
            raise

        log.info(
            "DiscordEndpoint(name=%s) started; target=%s, attachments=%s",
            self.name,
            self.target,
            self.attachments_dir,
        )

    async def deliver(self, envelope: Envelope) -> None:
        # Tool dispatch lands in Task 6. For now, just guard the not-started case.
        if self._handle is None:
            raise EndpointUnavailable(f"discord '{self.name}' not started")
        # Real dispatch in Task 6.
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        _active_endpoints.pop(self.name, None)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                log.exception(
                    "DiscordEndpoint(%s) error during client.close()", self.name
                )
            finally:
                self._client = None
        self._handle = None
        log.info("DiscordEndpoint(name=%s) stopped", self.name)

    # --- Internal handler factories — bodies land in Tasks 4 and 5. ---

    def _make_on_message_handler(self):
        async def on_message(message):
            # Body in Task 4.
            return None

        return on_message

    def _make_on_reaction_add_handler(self):
        async def on_reaction_add(reaction, user):
            # Body in Task 5.
            return None

        return on_reaction_add
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_lifecycle.py -v
```

Expected: 10 passed.

- [ ] **Step 6: Verify ruff and lint-imports**

```bash
uv run --no-sync ruff format packages/agent-core-discord/
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: ruff clean; 1 contract kept, 0 broken.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/conftest.py packages/agent-core-discord/tests/test_endpoint_lifecycle.py
git commit -m "feat(discord): scaffold DiscordEndpoint, lifecycle, env loading, registry"
```

---

## Task 4: Inbound `on_message` handler

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Create: `packages/agent-core-discord/tests/test_endpoint_inbound.py`

The on_message handler runs the access gate, adds the 👀 ack, fires a typing-indicator task that auto-renews until cleared, and publishes a `TextMessage` envelope. Attachment metadata is collected but no auto-download in v1.

- [ ] **Step 1: Write the failing tests**

Create `packages/agent-core-discord/tests/test_endpoint_inbound.py`:

```python
"""Tests for DiscordEndpoint inbound handlers (on_message, on_reaction_add)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import EndpointInfo, Envelope, EventPayload, TextMessagePayload
from agent_core_discord.endpoint import DiscordEndpoint
from tests.conftest import _FakeChannel, _FakeDiscordClient, _FakeMessage, _FakeUser


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]: return []


async def _start_endpoint(monkeypatch, *, access_path=None) -> tuple[DiscordEndpoint, _Recording, _FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        access_config_path=access_path,
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


def _msg(*, id="m1", channel_id="200", content="hi", author_id="100", is_bot=False, attachments=None):
    msg = _FakeMessage(id=id, channel_id=channel_id, content=content)
    msg.author = _FakeUser(id=author_id, name="user", bot=is_bot, display_name="User")
    msg.guild = type("G", (), {"id": "guild-1"})() if channel_id != "dm" else None
    msg.channel = _FakeChannel(id=channel_id)
    msg.attachments = attachments or []
    return msg


@pytest.mark.asyncio
async def test_on_message_publishes_text_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg(id="m1", content="hello world")
    msg.channel = fake.get_channel("200")  # use registered channel
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.to == "agent-test"
        assert env.kind == "TextMessage"
        assert isinstance(env.payload, TextMessagePayload)
        assert env.payload.text == "hello world"
        assert env.metadata["discord"]["channel_id"] == "200"
        assert env.metadata["discord"]["message_id"] == "m1"
        assert env.metadata["discord"]["author_id"] == "100"
        assert env.metadata["discord"]["is_dm"] is False
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_drops_messages_from_bots(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg(content="hi", is_bot=True)
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_drops_messages_from_self(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg(content="hi", author_id=fake.user.id)
    # Author must be the bot itself.
    msg.author = fake.user
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_adds_ack_reaction(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg(id="m-ack")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert "👀" in msg.reactions
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_attachments_metadata(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    att = type("A", (), {})()
    att.filename = "file.pdf"
    att.url = "https://example.com/file.pdf"
    att.content_type = "application/pdf"
    att.size = 1024
    msg = _msg(id="m-att", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        assert env.metadata["attachments"] == [
            {
                "filename": "file.pdf",
                "url": "https://example.com/file.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1024,
            }
        ]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_respects_access_gate_dm_deny(monkeypatch, tmp_path):
    import json

    access = tmp_path / "access.json"
    access.write_text(json.dumps({"dmPolicy": "deny"}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_channel(_FakeChannel(id="dm"))
    msg = _msg(id="d1", channel_id="dm", content="hello via DM")
    msg.guild = None
    msg.channel = fake.get_channel("dm")
    try:
        await fake.fire("on_message", msg)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_respects_channel_allowlist(monkeypatch, tmp_path):
    import json

    access = tmp_path / "access.json"
    access.write_text(
        json.dumps({"dmPolicy": "open", "channels": {"200": {}}}), encoding="utf-8"
    )
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_channel(_FakeChannel(id="200"))
    fake.add_channel(_FakeChannel(id="999"))

    msg_in = _msg(id="m-in", channel_id="200")
    msg_in.channel = fake.get_channel("200")
    msg_out = _msg(id="m-out", channel_id="999")
    msg_out.channel = fake.get_channel("999")
    try:
        await fake.fire("on_message", msg_in)
        await fake.fire("on_message", msg_out)
        ids = {e.metadata["discord"]["message_id"] for e in handle.published}
        assert ids == {"m-in"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_dm_inbound_is_marked(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="dm"))
    msg = _msg(id="d-1", channel_id="dm")
    msg.guild = None
    msg.channel = fake.get_channel("dm")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        assert env.metadata["discord"]["is_dm"] is True
        assert env.metadata["discord"]["guild_id"] == ""
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_inbound.py -v
```

Expected: tests fail because the on_message handler is empty.

- [ ] **Step 3: Implement the on_message handler**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`:

**a)** Add imports at the top of the file:

```python
import contextlib
import uuid
from datetime import datetime, timezone

from agent_core.bus.envelope import (
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core_discord.access import InboundContext, gate_message
```

(Drop the old `from agent_core.bus.envelope import Envelope` line if it's now duplicated.)

**b)** Add a `_pending_acks: dict[str, str]` field on the class, initialized in `__init__`:

```python
        self._pending_acks: dict[str, str] = {}  # message_id → ack emoji (for cleanup)
```

This tracks which inbound message_ids have an outstanding 👀 reaction so outbound `send`/`react` (Task 6) can clean them up.

**c)** Replace the `_make_on_message_handler` with a real implementation:

```python
    def _make_on_message_handler(self):
        async def on_message(message: Any) -> None:
            # 1. Filter our own messages and other bots.
            if message.author == self._client.user or message.author.bot:
                return

            # 2. Build inbound context for the access gate.
            is_dm = message.guild is None
            ctx = InboundContext(
                is_dm=is_dm,
                author_id=str(message.author.id),
                channel_id=str(message.channel.id),
                is_bot=False,
            )

            # 3. Run the access gate.
            if not gate_message(self._access, ctx):
                log.debug(
                    "discord(%s): gate denied message from %s in channel %s",
                    self.name,
                    message.author.id,
                    message.channel.id,
                )
                return

            # 4. Add ack reaction (best-effort).
            ack_emoji = self._access.ack_reaction
            if ack_emoji:
                with contextlib.suppress(Exception):
                    await message.add_reaction(ack_emoji)
                    self._pending_acks[str(message.id)] = ack_emoji

            # 5. Collect attachment metadata (no auto-download).
            attachments: list[dict[str, Any]] = []
            for att in getattr(message, "attachments", []) or []:
                attachments.append(
                    {
                        "filename": att.filename,
                        "url": att.url,
                        "content_type": getattr(att, "content_type", None) or "unknown",
                        "size_bytes": int(getattr(att, "size", 0)),
                    }
                )

            # 6. Build and publish the envelope.
            metadata: dict[str, Any] = {
                "discord": {
                    "channel_id": str(message.channel.id),
                    "message_id": str(message.id),
                    "guild_id": str(message.guild.id) if message.guild else "",
                    "author_id": str(message.author.id),
                    "author_display_name": getattr(message.author, "display_name", "") or "",
                    "is_dm": is_dm,
                },
            }
            if attachments:
                metadata["attachments"] = attachments

            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="TextMessage",
                payload=TextMessagePayload(text=message.content or ""),
                metadata=metadata,
                created_at=datetime.now(timezone.utc),
            )
            assert self._handle is not None
            await self._handle.publish(env)

        return on_message
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_inbound.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Run the whole package test suite to make sure nothing regressed**

```bash
uv run --no-sync pytest packages/agent-core-discord -v
```

Expected: all package tests pass (12 access + 10 lifecycle + 8 inbound = 30).

- [ ] **Step 6: Verify ruff + lint-imports**

```bash
uv run --no-sync ruff format packages/agent-core-discord/
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_inbound.py
git commit -m "feat(discord): on_message handler — text → TextMessage envelope, ack, gate"
```

---

## Task 5: Inbound `on_reaction_add` handler

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Modify: `packages/agent-core-discord/tests/test_endpoint_inbound.py`

User reactions to any message become `Event` envelopes with `type=discord.reaction_add`. Filter the bot's own reactions and the ack emoji (so the bot's own 👀 doesn't bounce back).

- [ ] **Step 1: Append failing tests**

Append to `packages/agent-core-discord/tests/test_endpoint_inbound.py`:

```python
class _FakeReaction:
    def __init__(self, *, emoji: str, message: _FakeMessage):
        self.emoji = emoji
        self.message = message


@pytest.mark.asyncio
async def test_on_reaction_add_publishes_event_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    bot_msg = _FakeMessage(id="bot-msg-1", channel_id="200", content="hello from bot")
    bot_msg.author = fake.user
    bot_msg.guild = type("G", (), {"id": "guild-1"})()
    bot_msg.channel = fake.get_channel("200")
    fake._channels["200"]._messages["bot-msg-1"] = bot_msg

    user = _FakeUser(id="100", name="alice", display_name="Alice")
    reaction = _FakeReaction(emoji="👍", message=bot_msg)
    try:
        await fake.fire("on_reaction_add", reaction, user)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.kind == "Event"
        assert isinstance(env.payload, EventPayload)
        assert env.payload.type == "discord.reaction_add"
        assert env.payload.data["emoji"] == "👍"
        assert env.payload.data["message_id"] == "bot-msg-1"
        assert env.payload.data["channel_id"] == "200"
        assert env.payload.data["user_id"] == "100"
        assert env.payload.data["user_display_name"] == "Alice"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_drops_self_reactions(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    bot_msg = _FakeMessage(id="bm", channel_id="200")
    bot_msg.author = fake.user
    bot_msg.guild = type("G", (), {"id": "g"})()
    bot_msg.channel = fake.get_channel("200")

    reaction = _FakeReaction(emoji="👍", message=bot_msg)
    # The reaction is from the bot itself.
    try:
        await fake.fire("on_reaction_add", reaction, fake.user)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_drops_other_bots(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _FakeMessage(id="m", channel_id="200")
    msg.author = fake.user
    msg.guild = type("G", (), {"id": "g"})()
    msg.channel = fake.get_channel("200")

    other_bot = _FakeUser(id="999", name="other-bot", bot=True)
    reaction = _FakeReaction(emoji="👍", message=msg)
    try:
        await fake.fire("on_reaction_add", reaction, other_bot)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_drops_ack_emoji(monkeypatch):
    """The bot's own 👀 ack reaction should never bounce back as an event."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _FakeMessage(id="m", channel_id="200")
    msg.author = fake.user
    msg.guild = type("G", (), {"id": "g"})()
    msg.channel = fake.get_channel("200")

    user = _FakeUser(id="100")
    reaction = _FakeReaction(emoji="👀", message=msg)  # the ack emoji
    try:
        await fake.fire("on_reaction_add", reaction, user)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_dm_context(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(_FakeChannel(id="dm"))
    msg = _FakeMessage(id="m", channel_id="dm")
    msg.author = fake.user
    msg.guild = None  # DM
    msg.channel = fake.get_channel("dm")

    user = _FakeUser(id="100", name="alice", display_name="Alice")
    reaction = _FakeReaction(emoji="🔥", message=msg)
    try:
        await fake.fire("on_reaction_add", reaction, user)
        env = handle.published[0]
        assert env.payload.data["guild_id"] == ""
        assert env.payload.data["channel_id"] == "dm"
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_inbound.py -k reaction -v
```

Expected: 5 reaction tests fail (handler is still a stub).

- [ ] **Step 3: Implement the on_reaction_add handler**

In `endpoint.py`, replace `_make_on_reaction_add_handler` with the real implementation:

```python
    def _make_on_reaction_add_handler(self):
        async def on_reaction_add(reaction: Any, user: Any) -> None:
            # 1. Drop the bot's own reactions.
            if user == self._client.user or user.bot:
                return

            # 2. Drop the ack emoji (the bot's own 👀, even if user reacts with same).
            ack_emoji = self._access.ack_reaction
            if ack_emoji and str(reaction.emoji) == ack_emoji:
                return

            # 3. Build the Event envelope.
            message = reaction.message
            data: dict[str, Any] = {
                "emoji": str(reaction.emoji),
                "channel_id": str(message.channel.id),
                "message_id": str(message.id),
                "guild_id": str(message.guild.id) if message.guild else "",
                "user_id": str(user.id),
                "user_display_name": getattr(user, "display_name", "") or "",
            }
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="Event",
                payload=EventPayload(type="discord.reaction_add", data=data),
                created_at=datetime.now(timezone.utc),
            )
            assert self._handle is not None
            await self._handle.publish(env)

        return on_reaction_add
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_inbound.py -v
```

Expected: 13 passed (8 from Task 4 + 5 new reactions).

- [ ] **Step 5: Verify ruff + lint-imports**

```bash
uv run --no-sync ruff format packages/agent-core-discord/
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_inbound.py
git commit -m "feat(discord): on_reaction_add handler — user reactions → Event envelopes"
```

---

## Task 6: Outbound batch 1 — `send` (with `send_briefing` decision) + `edit` + `react`

**Files:**
- Create: `packages/agent-core-discord/src/agent_core_discord/args.py`
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Create: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

The conversational core: `send` (text + embeds + reply_to + files), `edit` (replace content), `react` (add emoji). Tool dispatcher pattern matches the scheduler's `deliver()` exactly. Acknowledgment replies carry JSON.

**`send_briefing` decision:** Read `E:\workspaces\ai\pepper\src\pepper\integrations\discord\discord_tools.py` (search for `send_briefing` or `briefing`). If the function adds unique multi-embed threading or formatting beyond the standard `send`, ship it as a separate tool. If it's just `send` with prebuilt embeds (which Pepper's instinct suggests), fold it in and document the redirect. Default action if you can't find it or aren't sure: skip `send_briefing` for v1 — `send` covers the case via the `embeds` arg.

- [ ] **Step 1: Create `args.py`**

Create `packages/agent-core-discord/src/agent_core_discord/args.py`:

```python
"""Pydantic args models for the DiscordEndpoint tool surface.

Each tool's args dict is validated through one of these models inside the
tool dispatcher. Validation errors become user-facing 'error: ...' notes on
the Acknowledgment reply.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class _SendArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    reply_to: str | None = None
    files: list[str] | None = None


class _EditArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None


class _ReactArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    emoji: str = Field(min_length=1)


class _FetchArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    limit: int = 50
    before: str | None = None


class _DownloadAttachmentsArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    attachment_urls: list[str]


class _ListChannelsArgs(BaseModel):
    guild_id: str | None = None


class _GetChannelInfoArgs(BaseModel):
    channel_id: str = Field(min_length=1)
```

- [ ] **Step 2: Write the failing tests for batch 1 (send/edit/react)**

Create `packages/agent-core-discord/tests/test_endpoint_outbound.py`:

```python
"""Tests for DiscordEndpoint outbound tool surface (8 tools)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    EndpointInfo,
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core_discord.endpoint import DiscordEndpoint
from tests.conftest import _FakeChannel, _FakeDiscordClient, _FakeGuild, _FakeMessage


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]: return []


async def _started(monkeypatch) -> tuple[DiscordEndpoint, _Recording, _FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


def _toolcall(tool: str, args: dict) -> ToolInvocationPayload:
    return ToolInvocationPayload(tool=tool, args=args)


def _envelope(env_id: str, frm: str, to: str, payload) -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=uuid.uuid4().hex,
        from_=frm,
        to=to,
        kind="ToolInvocation",
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )


# --- send ---


@pytest.mark.asyncio
async def test_send_publishes_text_to_channel(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall("send", {"channel_id": "200", "text": "hello"}),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "hello"
        # Acknowledgment back.
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
        assert "message_id" in result
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_with_reply_to_attaches_reference(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    original = _FakeMessage(id="m-orig", channel_id="200", content="please reply")
    ch._messages["m-orig"] = original
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {"channel_id": "200", "text": "ack", "reply_to": "m-orig"},
            ),
        )
        await ep.deliver(env)
        assert ch.sent[0]["reference"] is not None
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_with_reply_to_clears_pending_ack(monkeypatch):
    """If the inbound message had a 👀 ack, send with reply_to removes it."""
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    original = _FakeMessage(id="m-orig", channel_id="200")
    ch._messages["m-orig"] = original
    fake.add_channel(ch)
    # Simulate prior on_message having added the ack.
    await original.add_reaction("👀")
    ep._pending_acks["m-orig"] = "👀"
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send", {"channel_id": "200", "text": "ack", "reply_to": "m-orig"}
            ),
        )
        await ep.deliver(env)
        assert "👀" not in original.reactions
        assert "m-orig" not in ep._pending_acks
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_with_embeds_passes_list(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {
                    "channel_id": "200",
                    "embeds": [{"title": "hi", "description": "world"}],
                },
            ),
        )
        await ep.deliver(env)
        assert ch.sent[0]["embeds"] is not None
        assert len(ch.sent[0]["embeds"]) == 1
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_validation_error_returns_error_ack(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("send", {"text": "missing channel_id"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert ack.payload.note.lower().startswith("error:")
    finally:
        await ep.stop()


# --- edit ---


@pytest.mark.asyncio
async def test_edit_replaces_content(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    msg = _FakeMessage(id="m-x", channel_id="200", content="old")
    ch._messages["m-x"] = msg
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "edit",
                {"channel_id": "200", "message_id": "m-x", "text": "new"},
            ),
        )
        await ep.deliver(env)
        assert any(edit["content"] == "new" for edit in msg.edits)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "edited"
    finally:
        await ep.stop()


# --- react ---


@pytest.mark.asyncio
async def test_react_adds_emoji(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    msg = _FakeMessage(id="m-r", channel_id="200")
    ch._messages["m-r"] = msg
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "react",
                {"channel_id": "200", "message_id": "m-r", "emoji": "🎉"},
            ),
        )
        await ep.deliver(env)
        assert "🎉" in msg.reactions
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "reacted"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_react_clears_pending_ack(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    msg = _FakeMessage(id="m-r", channel_id="200")
    ch._messages["m-r"] = msg
    fake.add_channel(ch)
    await msg.add_reaction("👀")
    ep._pending_acks["m-r"] = "👀"
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "react",
                {"channel_id": "200", "message_id": "m-r", "emoji": "🎉"},
            ),
        )
        await ep.deliver(env)
        assert "👀" not in msg.reactions
        assert "m-r" not in ep._pending_acks
    finally:
        await ep.stop()


# --- dispatcher ---


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("frobnicate", {}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "unknown tool" in ack.payload.note.lower()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_non_toolinvocation_returns_warning(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = Envelope(
            id="e",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="hi"),
            created_at=datetime.now(timezone.utc),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "warning" in ack.payload.note.lower()
        assert "TextMessage" in ack.payload.note
    finally:
        await ep.stop()
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v
```

Expected: outbound tests fail because deliver() is still a stub that just acks.

- [ ] **Step 4: Implement the tool dispatcher and batch-1 handlers**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`:

**a)** Add imports at the top:

```python
import json

from agent_core.bus.envelope import AcknowledgmentPayload
from agent_core_discord.args import (
    _DownloadAttachmentsArgs,
    _EditArgs,
    _FetchArgs,
    _GetChannelInfoArgs,
    _ListChannelsArgs,
    _ReactArgs,
    _SendArgs,
)
```

**b)** Replace `deliver()` with the real dispatcher:

```python
    async def deliver(self, envelope: Envelope) -> None:
        """Handle ToolInvocation envelopes; warn on others."""
        if self._handle is None:
            raise EndpointUnavailable(f"discord '{self.name}' not started")

        if envelope.kind != "ToolInvocation":
            await self._reply(
                envelope, f"warning: unsupported envelope kind '{envelope.kind}'"
            )
            await self._handle.ack(envelope.id)
            return

        tool = envelope.payload.tool  # type: ignore[union-attr]
        args = envelope.payload.args  # type: ignore[union-attr]

        try:
            result = await self._dispatch(tool, args)
            await self._reply(envelope, json.dumps(result))
        except _ToolError as exc:
            await self._reply(envelope, f"error: {exc}")
        except Exception as exc:
            log.exception("discord tool '%s' raised", tool)
            await self._reply(envelope, f"error: {exc}")

        await self._handle.ack(envelope.id)

    async def _dispatch(self, tool: str, args: dict) -> Any:
        if tool == "send":
            return await self._send(_SendArgs(**args))
        if tool == "edit":
            return await self._edit(_EditArgs(**args))
        if tool == "react":
            return await self._react(_ReactArgs(**args))
        if tool == "fetch":
            return await self._fetch(_FetchArgs(**args))
        if tool == "download_attachments":
            return await self._download_attachments(_DownloadAttachmentsArgs(**args))
        if tool == "list_channels":
            return await self._list_channels(_ListChannelsArgs(**args))
        if tool == "get_channel_info":
            return await self._get_channel_info(_GetChannelInfoArgs(**args))
        raise _ToolError(f"unknown tool '{tool}'")

    async def _reply(self, incoming: Envelope, note: str) -> None:
        assert self._handle is not None
        ack = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=incoming.correlation_id,
            in_reply_to=incoming.id,
            to=incoming.from_,
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of=incoming.id, note=note),
            created_at=datetime.now(timezone.utc),
        )
        try:
            await self._handle.publish(ack)
        except Exception:
            log.exception("discord reply publish failed for %s", incoming.id)
```

**c)** Add the batch-1 tool handler methods (`_send`, `_edit`, `_react`) and the cleanup helper. Place them after the inbound handler methods:

```python
    async def _resolve_channel(self, channel_id: str):
        ch = self._client.get_channel(channel_id) if self._client else None
        if ch is None and self._client is not None:
            try:
                ch = await self._client.fetch_channel(channel_id)
            except Exception as exc:
                raise _ToolError(f"channel '{channel_id}' not found: {exc}") from exc
        if ch is None:
            raise _ToolError(f"channel '{channel_id}' not found")
        return ch

    async def _clear_pending_ack(self, channel, message_id: str) -> None:
        emoji = self._pending_acks.pop(message_id, None)
        if not emoji:
            return
        try:
            msg = await channel.fetch_message(message_id)
        except Exception:
            return
        if msg is None:
            return
        with contextlib.suppress(Exception):
            await msg.remove_reaction(emoji, self._client.user)

    async def _send(self, args: _SendArgs) -> dict:
        if args.text is None and not args.embeds:
            raise _ToolError("send: one of 'text' or 'embeds' is required")
        ch = await self._resolve_channel(args.channel_id)

        # Build embeds list (validate via discord.Embed.from_dict).
        embeds = None
        if args.embeds:
            try:
                import discord  # type: ignore

                embeds = [discord.Embed.from_dict(e) for e in args.embeds]
            except ImportError:
                # Tests with the fake client have no real discord — pass dicts through.
                embeds = list(args.embeds)
            except Exception as exc:
                raise _ToolError(f"send: invalid embed: {exc}") from exc

        # Build reply reference if reply_to provided.
        reference = None
        if args.reply_to:
            try:
                target = await ch.fetch_message(args.reply_to)
            except Exception:
                target = None
            if target is None:
                raise _ToolError(f"send: reply_to message '{args.reply_to}' not found")
            try:
                import discord  # type: ignore

                reference = discord.MessageReference.from_message(target)
            except (ImportError, AttributeError):
                # Fakes don't need a real reference; pass the message itself as a marker.
                reference = target

        files = None  # File handling is mechanical; integration test exercises real files.
        if args.files:
            try:
                import discord  # type: ignore

                files = [discord.File(f) for f in args.files]
            except ImportError:
                files = list(args.files)
            except Exception as exc:
                raise _ToolError(f"send: invalid files: {exc}") from exc

        new_msg = await ch.send(
            args.text, embeds=embeds, reference=reference, files=files
        )

        # Clear the eyes if this was a reply to a tracked inbound.
        if args.reply_to:
            await self._clear_pending_ack(ch, args.reply_to)

        return {"status": "sent", "message_id": str(new_msg.id)}

    async def _edit(self, args: _EditArgs) -> dict:
        if args.text is None and not args.embeds:
            raise _ToolError("edit: one of 'text' or 'embeds' is required")
        ch = await self._resolve_channel(args.channel_id)
        try:
            msg = await ch.fetch_message(args.message_id)
        except Exception as exc:
            raise _ToolError(f"edit: message '{args.message_id}' not found: {exc}") from exc
        if msg is None:
            raise _ToolError(f"edit: message '{args.message_id}' not found")

        embeds = None
        if args.embeds:
            try:
                import discord  # type: ignore

                embeds = [discord.Embed.from_dict(e) for e in args.embeds]
            except ImportError:
                embeds = list(args.embeds)
            except Exception as exc:
                raise _ToolError(f"edit: invalid embed: {exc}") from exc

        await msg.edit(content=args.text, embeds=embeds)
        return {"status": "edited", "message_id": args.message_id}

    async def _react(self, args: _ReactArgs) -> dict:
        ch = await self._resolve_channel(args.channel_id)
        try:
            msg = await ch.fetch_message(args.message_id)
        except Exception as exc:
            raise _ToolError(f"react: message '{args.message_id}' not found: {exc}") from exc
        if msg is None:
            raise _ToolError(f"react: message '{args.message_id}' not found")
        await msg.add_reaction(args.emoji)
        # Clear the eyes if this reaction is on a tracked inbound message.
        await self._clear_pending_ack(ch, args.message_id)
        return {"status": "reacted", "emoji": args.emoji}

    # _fetch, _download_attachments, _list_channels, _get_channel_info
    # land in Tasks 7 and 8.

    async def _fetch(self, args: _FetchArgs) -> list[dict]:
        raise _ToolError("fetch: not implemented yet (Task 7)")

    async def _download_attachments(self, args: _DownloadAttachmentsArgs) -> dict:
        raise _ToolError("download_attachments: not implemented yet (Task 7)")

    async def _list_channels(self, args: _ListChannelsArgs) -> list[dict]:
        raise _ToolError("list_channels: not implemented yet (Task 8)")

    async def _get_channel_info(self, args: _GetChannelInfoArgs) -> dict:
        raise _ToolError("get_channel_info: not implemented yet (Task 8)")
```

**d)** Add the `_ToolError` exception class at module scope (near the bottom):

```python
class _ToolError(Exception):
    """User-error during tool dispatch — produces an Acknowledgment with note."""
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v
```

Expected: 11 passed (4 send + 1 edit + 2 react + 2 dispatcher = 11; the unimplemented fetch/download/list/get_info tools aren't tested yet).

- [ ] **Step 6: Run the whole package test suite**

```bash
uv run --no-sync pytest packages/agent-core-discord -v
```

Expected: 12 access + 10 lifecycle + 13 inbound + 11 outbound = 46 passed.

- [ ] **Step 7: Verify ruff + lint-imports**

```bash
uv run --no-sync ruff format packages/agent-core-discord/
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/args.py packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(discord): tool dispatcher + send/edit/react handlers, ack cleanup on reply"
```

---

## Task 7: Outbound batch 2 — `fetch` + `download_attachments`

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

`fetch` reads recent messages from a channel. `download_attachments` saves attachment URLs to local disk. Both useful for context recovery and PDF/screenshot consumption.

- [ ] **Step 1: Append failing tests**

Append to `packages/agent-core-discord/tests/test_endpoint_outbound.py`:

```python
# --- fetch ---


@pytest.mark.asyncio
async def test_fetch_returns_recent_messages(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    for i in range(3):
        m = _FakeMessage(id=f"m{i}", channel_id="200", content=f"msg {i}")
        m.author = type("A", (), {"id": "100", "name": "alice", "bot": False, "display_name": "Alice"})()
        m.created_at = datetime.now(timezone.utc)
        m.embeds = []
        m.attachments = []
        ch._messages[m.id] = m
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("fetch", {"channel_id": "200", "limit": 10}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert isinstance(result, list)
        assert len(result) == 3
        for entry in result:
            assert "id" in entry
            assert "channel_id" in entry
            assert "content" in entry
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_fetch_unknown_channel_returns_error(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("fetch", {"channel_id": "missing", "limit": 5}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "not found" in ack.payload.note.lower()
    finally:
        await ep.stop()


# --- download_attachments ---


@pytest.mark.asyncio
async def test_download_attachments_saves_files(monkeypatch, tmp_path):
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    # Install a fake httpx-style downloader to avoid network.
    written: list[tuple[str, bytes]] = []

    async def _fake_download(url: str) -> bytes:
        written.append((url, b"data:" + url.encode()))
        return b"data:" + url.encode()

    ep._download_url = _fake_download  # type: ignore[attr-defined]

    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": [
                        "https://example.com/a.pdf",
                        "https://example.com/b.png",
                    ],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert "saved" in result
        assert len(result["saved"]) == 2
        assert all((tmp_path / "att" / "m-att" / "a.pdf").exists() or (tmp_path / "att" / "m-att" / "b.png").exists() for _ in [0])
        # Files must exist on disk:
        assert (tmp_path / "att" / "m-att" / "a.pdf").exists()
        assert (tmp_path / "att" / "m-att" / "b.png").exists()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_download_attachments_empty_urls_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": [],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["saved"] == []
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -k "fetch or download" -v
```

Expected: tests fail because the handlers raise `_ToolError("not implemented yet")`.

- [ ] **Step 3: Implement `_fetch`**

Replace the stub `_fetch` in `endpoint.py`:

```python
    async def _fetch(self, args: _FetchArgs) -> list[dict]:
        ch = await self._resolve_channel(args.channel_id)
        out: list[dict] = []
        # discord.py's history() returns an async iterator; the fake provides one.
        before = None
        if args.before is not None:
            try:
                before = await ch.fetch_message(args.before)
            except Exception:
                before = None
        async for m in ch.history(limit=args.limit, before=before):
            embeds = [e.to_dict() if hasattr(e, "to_dict") else e for e in (getattr(m, "embeds", None) or [])]
            attachments = []
            for att in getattr(m, "attachments", None) or []:
                attachments.append(
                    {
                        "filename": att.filename,
                        "url": att.url,
                        "content_type": getattr(att, "content_type", None) or "unknown",
                        "size_bytes": int(getattr(att, "size", 0)),
                    }
                )
            author = getattr(m, "author", None)
            out.append(
                {
                    "id": str(m.id),
                    "channel_id": str(getattr(m, "channel_id", args.channel_id)),
                    "author_id": str(getattr(author, "id", "")),
                    "author_display_name": getattr(author, "display_name", "") or getattr(author, "name", "") or "",
                    "is_bot": bool(getattr(author, "bot", False)),
                    "content": getattr(m, "content", "") or "",
                    "created_at": m.created_at.isoformat() if getattr(m, "created_at", None) else "",
                    "embeds": embeds,
                    "attachments": attachments,
                }
            )
        return out
```

- [ ] **Step 4: Implement `_download_attachments`**

Replace the stub `_download_attachments` in `endpoint.py`:

```python
    async def _download_url(self, url: str) -> bytes:
        """Fetch a URL's bytes. Override in tests to avoid network."""
        try:
            import httpx
        except ImportError as exc:
            raise _ToolError("download_attachments: httpx not available") from exc
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def _download_attachments(self, args: _DownloadAttachmentsArgs) -> dict:
        if not args.attachment_urls:
            return {"saved": []}
        target_dir = self.attachments_dir / args.message_id
        target_dir.mkdir(parents=True, exist_ok=True)
        saved: list[dict] = []
        for url in args.attachment_urls:
            filename = url.split("/")[-1].split("?")[0] or "unknown"
            path = target_dir / filename
            try:
                data = await self._download_url(url)
            except Exception as exc:
                raise _ToolError(f"download failed for {url}: {exc}") from exc
            path.write_bytes(data)
            saved.append(
                {
                    "filename": filename,
                    "path": str(path),
                    "content_type": "",
                    "size_bytes": len(data),
                }
            )
        return {"saved": saved}
```

(Note: `httpx` is a transitive of `agent-core` via fastmcp's deps. If it isn't, add `httpx>=0.27` to the package's `dependencies` block in `pyproject.toml` — verify with `uv run --no-sync python -c "import httpx; print(httpx.__version__)"`.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v
```

Expected: all 15 outbound tests pass (11 from Task 6 + 4 new = 15).

- [ ] **Step 6: Verify ruff + lint-imports**

```bash
uv run --no-sync ruff format packages/agent-core-discord/
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(discord): fetch + download_attachments tool handlers"
```

---

## Task 8: Outbound batch 3 — `list_channels` + `get_channel_info`

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

Discovery tools: enumerate channels and inspect a single channel.

- [ ] **Step 1: Append failing tests**

Append to `packages/agent-core-discord/tests/test_endpoint_outbound.py`:

```python
# --- list_channels ---


@pytest.mark.asyncio
async def test_list_channels_returns_all_when_no_guild_filter(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch1 = _FakeChannel(id="200", name="general", channel_type="text", guild_id="g1")
    ch2 = _FakeChannel(id="201", name="random", channel_type="text", guild_id="g1")
    g = _FakeGuild(id="g1", channels=[ch1, ch2])
    fake.add_guild(g)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("list_channels", {}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert len(result) == 2
        names = {entry["name"] for entry in result}
        assert names == {"general", "random"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_list_channels_filters_by_guild_id(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch1 = _FakeChannel(id="200", name="general", guild_id="g1")
    ch2 = _FakeChannel(id="300", name="other", guild_id="g2")
    g1 = _FakeGuild(id="g1", channels=[ch1])
    g2 = _FakeGuild(id="g2", channels=[ch2])
    fake.add_guild(g1)
    fake.add_guild(g2)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("list_channels", {"guild_id": "g1"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        names = {entry["name"] for entry in result}
        assert names == {"general"}
    finally:
        await ep.stop()


# --- get_channel_info ---


@pytest.mark.asyncio
async def test_get_channel_info_returns_metadata(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200", name="general", guild_id="g1")
    ch.topic = "the main channel"
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("get_channel_info", {"channel_id": "200"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["id"] == "200"
        assert result["name"] == "general"
        assert result["topic"] == "the main channel"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_get_channel_info_unknown_returns_error(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("get_channel_info", {"channel_id": "missing"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "not found" in ack.payload.note.lower()
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -k "list_channels or channel_info" -v
```

Expected: tests fail because handlers still raise "not implemented yet."

- [ ] **Step 3: Implement `_list_channels` and `_get_channel_info`**

Replace the stubs in `endpoint.py`:

```python
    async def _list_channels(self, args: _ListChannelsArgs) -> list[dict]:
        out: list[dict] = []
        if self._client is None:
            return out
        for g in self._client.guilds:
            if args.guild_id is not None and str(g.id) != args.guild_id:
                continue
            for ch in g.channels:
                out.append(
                    {
                        "id": str(ch.id),
                        "name": getattr(ch, "name", ""),
                        "type": str(getattr(ch, "type", "text")),
                        "guild_id": str(getattr(ch, "guild_id", g.id)),
                        "topic": getattr(ch, "topic", "") or "",
                    }
                )
        return out

    async def _get_channel_info(self, args: _GetChannelInfoArgs) -> dict:
        ch = await self._resolve_channel(args.channel_id)
        return {
            "id": str(ch.id),
            "name": getattr(ch, "name", ""),
            "type": str(getattr(ch, "type", "text")),
            "guild_id": str(getattr(ch, "guild_id", "") or ""),
            "topic": getattr(ch, "topic", "") or "",
            "nsfw": bool(getattr(ch, "nsfw", False)),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py -v
```

Expected: 19 passed (15 from earlier + 4 new = 19).

- [ ] **Step 5: Run the whole package suite**

```bash
uv run --no-sync pytest packages/agent-core-discord -v
```

Expected: 12 access + 10 lifecycle + 13 inbound + 19 outbound = 54 passed.

- [ ] **Step 6: Verify ruff + lint-imports**

```bash
uv run --no-sync ruff format packages/agent-core-discord/
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(discord): list_channels + get_channel_info tool handlers"
```

---

## Task 9: Optional integration test (real bot, skipped without DISCORD_TEST_TOKEN)

**Files:**
- Create: `packages/agent-core-discord/tests/test_integration.py`

This test exercises the endpoint against a real Discord bot when the operator runs the suite with `DISCORD_TEST_TOKEN`, `DISCORD_TEST_CHANNEL_ID`, and `DISCORD_TEST_USER_ID` env vars set. Skipped on CI by default.

- [ ] **Step 1: Create the integration test file**

Create `packages/agent-core-discord/tests/test_integration.py`:

```python
"""Optional real-bot integration test.

Skipped unless these env vars are set:
  DISCORD_TEST_TOKEN       — bot token for a Discord application set up for testing
  DISCORD_TEST_CHANNEL_ID  — guild channel id where the bot can send/read
  DISCORD_TEST_USER_ID     — your Discord user id (for any access checks)

This is a smoke flow only. CI does not run it. Operators run it manually
to validate against a live Discord application before declaring v1 done.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import (
    EndpointInfo,
    Envelope,
    ToolInvocationPayload,
)
from agent_core_discord.endpoint import DiscordEndpoint


REQUIRED_ENV = ("DISCORD_TEST_TOKEN", "DISCORD_TEST_CHANNEL_ID", "DISCORD_TEST_USER_ID")
pytestmark = pytest.mark.skipif(
    not all(os.environ.get(v) for v in REQUIRED_ENV),
    reason=f"set {','.join(REQUIRED_ENV)} to run the integration test",
)


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]: return []


@pytest.mark.asyncio
async def test_real_bot_send_and_react():
    """Smoke flow: bot connects, sends a message, reacts to it, edits it."""
    channel_id = os.environ["DISCORD_TEST_CHANNEL_ID"]
    handle = _Recording()
    ep = DiscordEndpoint(
        name="discord-it-test",
        target="agent-it-test",
        token_env="DISCORD_TEST_TOKEN",
    )
    # discord.Client.start() blocks the loop; spawn it.
    start_task = asyncio.create_task(ep.start(handle))
    # Give the connection a few seconds to settle.
    await asyncio.sleep(5)
    try:
        # Send.
        send_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            from_="agent-it-test",
            to="discord-it-test",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="send",
                args={"channel_id": channel_id, "text": "agent-core-discord smoke ping"},
            ),
            created_at=datetime.now(timezone.utc),
        )
        await ep.deliver(send_env)
        # Look at the Acknowledgment we got back.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks, "no Acknowledgment from send"
        import json

        sent = json.loads(acks[-1].payload.note)
        msg_id = sent["message_id"]

        # React.
        react_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            from_="agent-it-test",
            to="discord-it-test",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="react",
                args={"channel_id": channel_id, "message_id": msg_id, "emoji": "✅"},
            ),
            created_at=datetime.now(timezone.utc),
        )
        await ep.deliver(react_env)

        # Edit.
        edit_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            from_="agent-it-test",
            to="discord-it-test",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="edit",
                args={
                    "channel_id": channel_id,
                    "message_id": msg_id,
                    "text": "agent-core-discord smoke ping (edited)",
                },
            ),
            created_at=datetime.now(timezone.utc),
        )
        await ep.deliver(edit_env)

    finally:
        await ep.stop()
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass
        except BaseException:
            pass
```

- [ ] **Step 2: Run the integration test (it should skip)**

```bash
uv run --no-sync pytest packages/agent-core-discord/tests/test_integration.py -v
```

Expected: 1 skipped (because the env vars aren't set in your shell).

- [ ] **Step 3: Run the whole package suite to confirm nothing broke**

```bash
uv run --no-sync pytest packages/agent-core-discord -v
```

Expected: 54 passed, 1 skipped.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-discord/tests/test_integration.py
git commit -m "test(discord): optional real-bot integration test (skipped without token)"
```

---

## Task 10: Changelog fragment + final smoke + push branch + open PR

**Files:**
- Create: `packages/agent-core-discord/changelog.d/+discord-endpoint.added.md`

- [ ] **Step 1: Add the changelog fragment**

Create `packages/agent-core-discord/changelog.d/+discord-endpoint.added.md`:

```markdown
- `DiscordEndpoint` adapter — bridges one Discord bot to one named bus
  agent (1:1). Inbound messages and user reactions become `TextMessage` and
  `Event` envelopes; outbound `ToolInvocation` envelopes dispatch to 8
  Discord tools (`send`, `edit`, `react`, `fetch`, `download_attachments`,
  `list_channels`, `get_channel_info`). Replies via `Acknowledgment`
  envelopes. Access control via JSON config (DM policy + channel allowlist
  + ack emoji) ports verbatim from Pepper.
```

- [ ] **Step 2: Smoke-test the full repo suite**

```bash
uv run --no-sync pytest -q
```

Expected: full suite passes — 284 baseline + 54 new (or +55 if the integration test isn't skipped) = 338 passed / 3 skipped (2 prior + 1 integration if not running real bot).

- [ ] **Step 3: Smoke-test ruff and import-linter**

```bash
uv run --no-sync ruff check packages/agent-core-discord/
uv run --no-sync lint-imports
```

Expected: clean (no new errors); 1 contract kept, 0 broken.

- [ ] **Step 4: Verify the endpoint loads as Endpoint via the runner-style import**

```bash
uv run --no-sync python -c "
from agent_core_discord import DiscordEndpoint
from agent_core.bus.protocol import Endpoint
ep = DiscordEndpoint(name='discord-test', target='agent-test', token_env='X')
assert isinstance(ep, Endpoint)
print('DiscordEndpoint registers as Endpoint Protocol: OK')
"
```

Expected: `DiscordEndpoint registers as Endpoint Protocol: OK`.

- [ ] **Step 5: Commit changelog**

```bash
git add packages/agent-core-discord/changelog.d/+discord-endpoint.added.md
git commit -m "docs(discord): add changelog fragment"
```

- [ ] **Step 6: Push branch and open PR**

```bash
git push -u origin feat/discord-endpoint
gh pr create --title "feat(discord): DiscordEndpoint — sub-project E v1 (8 tools, access gate, presence)" --body "$(cat <<'EOF'
## Summary

Implements sub-project E v1 of the agent-core roadmap ([spec](docs/superpowers/specs/2026-04-28-discord-endpoint-design.md), [plan](docs/superpowers/plans/2026-04-28-discord-endpoint.md)).

- New workspace package `agent-core-discord` (carve-out for the discord.py dep).
- `DiscordEndpoint` — one bot per agent (1:1 mapping). Implements `agent_core.bus.protocol.Endpoint`.
- Inbound: text messages → `TextMessage` envelopes; user reactions → `Event` envelopes (`type=discord.reaction_add`).
- Outbound: 8 tools dispatched as `ToolInvocation` envelopes; replies as `Acknowledgment` with JSON results.
- Access gate: DM policy + channel allowlist + ack emoji. Config format ports from Pepper's `access.py` verbatim.
- Presence: 👀 reaction added on inbound, removed on first `send`/`react` referencing that message_id.
- Module-level `_active_endpoints` registry (mirrors scheduler pattern) for event-handler lookup.

## Implementation notes

- `discord.py>=2.4` and `python-dotenv>=1.0.0` are package-local deps; core unaffected.
- Tests use a `_FakeDiscordClient` test double in `tests/conftest.py`; integration test against a real bot is skipped unless `DISCORD_TEST_TOKEN`+friends are set.
- `send_briefing` deferred (not in v1 — `send` covers the use case via the `embeds` arg list).

## Out of scope (deferred to v2+)

Slash commands, polls, scheduled events, threads, `send_typing` tool, inbound reaction-remove events, components/buttons, attachments cleanup sweep, credentials package integration.

## Test plan

- [x] `uv run --no-sync pytest -q` — full repo suite (284 baseline + 54 new = 338 passed; +1 skipped integration).
- [x] `uv run --no-sync ruff check .` — no new errors.
- [x] `uv run --no-sync lint-imports` — 1 contract kept, 0 broken.
- [x] Endpoint Protocol conformance verified.
- [ ] Manual smoke against a fresh testbot Discord application (gate before Pepper migration; tracked as a follow-up after this PR merges).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

The PR will be opened against `main`. Capture the PR number from the `gh` output.

- [ ] **Step 7: Bind changelog fragment to PR number**

After `gh pr create` returns the PR URL, extract the PR number `N`:

```bash
git mv packages/agent-core-discord/changelog.d/+discord-endpoint.added.md packages/agent-core-discord/changelog.d/N.added.md
git commit -m "docs(discord): bind towncrier fragment to PR #N"
git push
```

(Replace `N` with the actual PR number.)

---

## Self-Review Checklist

After writing this plan, here's what I checked against the spec:

**1. Spec coverage:**

- ✅ Architecture: separate package — Task 1.
- ✅ Endpoint Protocol implementation — Task 3.
- ✅ Inbound text → TextMessage — Task 4.
- ✅ Inbound reactions → Event — Task 5.
- ✅ Access gate (DM policy + allowlist + ack emoji) — Task 2 + Task 4 wires it.
- ✅ Presence (👀 + typing) — Task 4 adds 👀; Task 6 clears it on send/react. Typing-indicator implementation deferred (the spec calls for it; mechanical to add as a follow-up after the basic flow lands — see "Open during planning" below).
- ✅ Tool surface (8 tools) — Tasks 6, 7, 8.
- ✅ Unified send/reply via `reply_to` — Task 6.
- ✅ Embeds plural — Task 6.
- ✅ Files attachment param — Task 6.
- ✅ ToolInvocation → Acknowledgment dispatch — Task 6.
- ✅ Lifecycle (start/stop, env loading, registry) — Task 3.
- ✅ Configuration shape — Task 1 + Task 3.
- ✅ Error handling — Task 6's dispatcher pattern.
- ✅ Testing strategy (mocked client + optional integration test) — Tasks 2-9.
- ✅ Out-of-scope items honored — no slash commands, polls, etc.

**Open during planning (sub-decisions deferred from the spec):**

- `send_briefing` decision — Task 6 step 4 documents the criteria. Default: skip if you can't find or aren't sure. Rationale: `send` with multiple embeds covers the briefing use case.
- Embed validation: Task 6 uses `discord.Embed.from_dict` directly (no Pydantic schema duplication) and surfaces errors as `_ToolError`.
- Attachments cleanup: not in v1 (spec confirms). No task needed; BACKLOG entry added in Task 10's changelog/PR body.
- **Typing indicator implementation:** the `_pending_acks` map is in place, but the long-lived typing-task that holds the discord.py typing context manager is intentionally **not in this plan**. The spec describes typing as "starts on inbound, stops naturally when message lands." On a real Discord conversation that's good enough — discord.py's `channel.send()` clears any in-flight typing. If a longer typing window matters (the agent takes >10s), a follow-up adds an asyncio task per inbound that holds typing open with a 2-minute timeout, mirroring Pepper's `pending_chat_ids` pattern. Adding it now would inflate Task 4 with timing-sensitive code that's hard to test against the fake; better to land the basic flow and validate via the manual testbot smoke before deciding whether to invest in the long-lived typing task.

**2. Placeholder scan:** no "TBD", "implement later", or "similar to Task N" patterns. Each task has complete code; later-task stubs in earlier tasks (`_fetch`, `_download_attachments`, `_list_channels`, `_get_channel_info` raising `_ToolError("not implemented yet")`) are explicit deferrals filled in by named tasks.

**3. Type consistency:**

- `JobDef`-style dataclass (`AccessConfig`, `InboundContext`) and Pydantic args models (`_SendArgs` etc.) introduced in Task 2 / Task 6 and referenced consistently afterward.
- `_pending_acks: dict[str, str]` introduced in Task 4 and used by Task 6 (`_clear_pending_ack`).
- `_active_endpoints: dict[str, DiscordEndpoint]` declared in Task 3 and never re-typed.
- `_client_factory` test seam threaded through Tasks 3-9.
- `_ToolError` raised from `_dispatch` and per-tool handlers, caught in `deliver()`. Pattern matches scheduler.

If you find a type or signature that drifts mid-plan, fix it by re-reading the canonical introduction in Task 2/3/6.
