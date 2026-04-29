# DiscordEndpoint — Design

> **Sub-project:** E — Discord adapter + native attachments (v1).
> See [`ROADMAP.md`](../../ROADMAP.md) for the sub-project table.
>
> **Status:** Approved 2026-04-28.

## Goal

Add a bus endpoint that bridges Discord (one bot) to one named agent on the
agent-core bus. Inbound Discord messages and user reactions become bus
envelopes addressed to the agent; outbound `ToolInvocation` envelopes from the
agent execute against `discord.py` and reply with `Acknowledgment` envelopes.

The endpoint is the agent's user-facing I/O channel — without it, an agent on
the bus has no way to talk to humans. Shipping this is the gate for "fresh
test agent runs end-to-end on the platform" (the validation milestone before
Pepper migration).

## Scope

**In scope (v1):**

- One Discord bot per `DiscordEndpoint` instance (1:1 — bot identity = agent
  identity on the bus).
- Inbound text messages → `TextMessage` envelopes.
- Inbound user reactions → `Event` envelopes.
- Access control (DM policy + channel allowlist + ack emoji).
- Presence indicators (👀 reaction + typing) automatic, no agent involvement.
- 8 outbound tools (Pepper tier 1 + 2): `send`, `edit`, `react`, `fetch`,
  `download_attachments`, `list_channels`, `get_channel_info`,
  `send_briefing` (only if it has unique logic beyond `send`; investigate
  during planning).
- Unified send/reply path (`send` takes optional `reply_to: message_id`).
- Embeds plural (Discord-API-correct list, fixing Pepper's singular bug).
- Files attachment param on `send`.
- Separate package `agent-core-discord` (carve-out from core).

**Explicitly out of scope (defer to v2 or later):**

- Slash commands (`/brief`, `/tasks`, `/focus`, `/status`) — architecturally
  separate from this endpoint's outbound surface; future work.
- Polls (write-only without vote forwarding; broken end-to-end today).
- Scheduled events (unused in production).
- Threads (low usage; `create_thread` deferred).
- `send_typing` tool (typing is automatic on inbound; explicit tool not
  load-bearing).
- Inbound reaction-remove (only reaction-add fires envelopes in v1).
- Components / buttons / select menus.
- Bot Manage Channels permission scope.
- Credentials package integration for token storage (env var sufficient for
  v1).

## Architecture

### Package layout

New uv-workspace package `agent-core-discord` at
`packages/agent-core-discord/`. Follows the carve-out pattern established by
`agent-core-notify` and `agent-core-credentials`. Module name:
`agent_core_discord`.

**Why a separate package:** `discord.py` and `aiohttp` are heavy dependencies
that core users (and other endpoint adapters) shouldn't pay for. Same logic
that drove the notify/credentials carve-outs. The `SchedulerEndpoint` lives
in core because `apscheduler`/`sqlalchemy` are general-purpose; Discord is
domain-specific.

### One bot per agent (1:1)

Each Discord bot has its own `DiscordEndpoint` registered on the bus:

```yaml
endpoints:
  - class: agent_core_discord.DiscordEndpoint
    name: discord-pepper
    description: "Pepper's Discord bot"
    params:
      target: agent-pepper
      token_env: DISCORD_TOKEN_PEPPER
      env_file: ~/.pepper/.env
      access_config_path: ~/.pepper/discord_access.json
      attachments_dir: ~/.pepper/attachments
```

The bus's "endpoint name = identity" model carries cleanly: the bot publishes
as `from=discord-pepper`, the agent publishes back to `to=discord-pepper`. No
shared bots, no internal routing maps.

### Endpoint shape

`DiscordEndpoint` implements `agent_core.bus.protocol.Endpoint`
(`start`/`deliver`/`stop`).

```python
class DiscordEndpoint:
    def __init__(
        self,
        *,
        name: str,
        target: str,
        token_env: str,
        env_file: str | Path | None = None,
        access_config_path: str | Path | None = None,
        attachments_dir: str | Path | None = None,
    ): ...

    async def start(self, bus: BusHandle) -> None: ...
    async def deliver(self, envelope: Envelope) -> None: ...
    async def stop(self) -> None: ...
```

A module-level `_active_endpoints: dict[str, DiscordEndpoint]` map (same
pattern as `SchedulerEndpoint`) lets `discord.py` event handlers find the
right endpoint instance from inside the global event loop.

## Inbound flow (Discord → bus)

### Text messages

```
1. discord.py fires on_message
2. Endpoint runs access gate (DM policy / channel allowlist).
   If denied, drop silently (no envelope).
3. Endpoint adds ackReaction (👀) to the original Discord message.
4. Endpoint starts typing indicator on the channel.
5. Endpoint builds Envelope:
     from = discord-<agent>
     to   = <target>
     kind = TextMessage
     payload = TextMessagePayload(text=<content>, attachments=[...])
     metadata = {
       "discord": {
         "channel_id": str,
         "message_id": str,
         "guild_id": str | "",
         "author_id": str,
         "author_display_name": str,
         "is_dm": bool,
       },
       "attachments": [
         {"filename": str, "url": str, "content_type": str, "size_bytes": int},
         ...
       ]  # if message has attachments; metadata only, no auto-download
     }
6. Endpoint publishes via BusHandle.
```

The agent's first outbound `send` or `react` tool referencing that
`message_id` triggers cleanup: endpoint removes the 👀 reaction (typing
indicator stops naturally when the reply message lands).

### User reactions

```
1. discord.py fires on_reaction_add
2. Filter: drop if user is the bot itself or another bot.
3. Filter: drop if reaction emoji equals ackReaction (the bot's own 👀 ack).
4. Endpoint builds Envelope:
     from = discord-<agent>
     to   = <target>
     kind = Event
     payload = EventPayload(
       type="discord.reaction_add",
       data={
         "emoji": str,
         "channel_id": str,
         "message_id": str,
         "guild_id": str | "",
         "user_id": str,
         "user_display_name": str,
       },
     )
5. Endpoint publishes via BusHandle.
```

This is a typed event the agent can dispatch on (e.g., "user thumbed up the
status post; mark task done"). Cleaner than Pepper's current
`[reacted with X]` TextMessage hack.

`reaction_remove` is **not** forwarded in v1.

### Access control

Ports Pepper's `access.py` config format verbatim — a JSON file at the path
configured via `access_config_path`:

```json
{
  "dmPolicy": "allowlist",
  "allowFrom": ["123456789012345678"],
  "channels": {"234567890123456789": {}},
  "ackReaction": "👀"
}
```

Fields:

- `dmPolicy`: `"allowlist"` (only `allowFrom` user IDs can DM) or `"open"`
  (any user can DM) or `"deny"` (no DMs accepted).
- `allowFrom`: list of Discord user ID strings allowed to DM (when policy is
  `"allowlist"`).
- `channels`: dict whose keys are channel IDs the bot accepts messages from.
  Empty value means "default settings"; future per-channel tuning lives here.
- `ackReaction`: emoji to add as visual ack on every accepted inbound
  message. Empty string disables the ack reaction.

If `access_config_path` is unset, defaults are: open DMs, no channel
allowlist (accept everywhere), `ackReaction="👀"`. Permissive default keeps
v1 setup minimal for fresh test agents.

The gate logic ports from `pepper/integrations/discord/access.py:gate()`
unchanged — same signature, same behavior, same return semantics.

## Outbound flow (bus → Discord)

The agent publishes `ToolInvocation` envelopes to `to=discord-<agent>`. The
endpoint's `deliver()` dispatches to the named tool's handler, executes
against `discord.py`, and replies with an `Acknowledgment` carrying a
JSON-encoded result in `payload.note` (matches the scheduler's pattern).

### Tool surface (8 tools)

#### 1. `send`

Send a message to a channel. Optional reply, embeds, files.

```
args:
  channel_id: str        # required
  text: str | None       # one of text/embeds required
  embeds: list[Embed] | None    # plural list (not Pepper's broken singular)
  reply_to: str | None   # discord message_id; if set, reply to that message
  files: list[str] | None       # local paths or URLs
result:
  {"status": "sent", "message_id": "<new message id>"}
```

Embed shape: dict with the standard Discord fields (`title`, `description`,
`color`, `fields`, `footer`, `timestamp`). The endpoint validates against
`discord.Embed.from_dict` and surfaces validation errors as
`error: <msg>` in the Acknowledgment.

If the inbound `message_id` referenced by `reply_to` has an outstanding 👀
ack, the endpoint removes it after the message lands successfully.

If `send_briefing` turns out to have unique logic beyond `send` (multi-embed
threading, special formatting), it stays as a separate tool. If it's just
`send` with prebuilt embeds, fold it into `send`. Decision deferred to
implementation: read Pepper's `send_briefing` source first, then call.

#### 2. `edit`

Edit an existing bot-authored message.

```
args:
  channel_id: str
  message_id: str
  text: str | None
  embeds: list[Embed] | None
result:
  {"status": "edited", "message_id": "<id>"}
```

#### 3. `react`

Add an emoji reaction to a message.

```
args:
  channel_id: str
  message_id: str
  emoji: str
result:
  {"status": "reacted", "emoji": "..."}
```

If the reaction is on a message that has an outstanding 👀 ack, the
endpoint removes the ack.

#### 4. `fetch`

Read recent messages from a channel.

```
args:
  channel_id: str
  limit: int = 50         # max 200 (Discord API ceiling)
  before: str | None      # message_id — fetch messages older than this
result:
  [
    {
      "id": str,
      "channel_id": str,
      "author_id": str,
      "author_display_name": str,
      "is_bot": bool,
      "content": str,
      "created_at": str (ISO),
      "embeds": [Embed, ...],   # serialized
      "attachments": [{...}, ...],
    },
    ...
  ]
```

Used by the agent for context recovery after compaction or for reading
historical Discord conversation.

#### 5. `download_attachments`

Download attachments referenced in an envelope's metadata.

```
args:
  message_id: str
  channel_id: str
  attachment_urls: list[str]    # from inbound TextMessage metadata.attachments[*].url
result:
  {
    "saved": [
      {"filename": str, "path": str, "content_type": str, "size_bytes": int},
      ...
    ]
  }
```

Files saved under `<attachments_dir>/<message_id>/<filename>`. The
`attachments_dir` defaults to `~/.agent-core/attachments/<endpoint_name>/`
(predictable, no target-name parsing). Override per agent (e.g.
`~/.pepper/attachments`) in the YAML when desired. Cleanup policy:
deferred to v2 (Pepper's 7-day sweep). For v1 the directory just grows.

#### 6. `list_channels`

List channels the bot has access to.

```
args:
  guild_id: str | None    # if set, filter to that guild; else all
result:
  [
    {"id": str, "name": str, "type": str, "guild_id": str | None, "topic": str | ""},
    ...
  ]
```

Used as fallback when the agent loses a channel ID.

#### 7. `get_channel_info`

Inspect a single channel.

```
args:
  channel_id: str
result:
  {"id": str, "name": str, "type": str, "guild_id": str | None,
   "topic": str | "", "nsfw": bool}
```

Used to verify channel existence before sending and to read channel topics.

#### 8. (`send_briefing`)

Decision deferred to implementation. If `send_briefing` adds unique logic
(multi-embed threading, formatting templates) beyond `send`, ship as
separate tool with the same arg schema as Pepper's existing one. If it's
just a thin wrapper, fold into `send` and note the redirect in the
changelog.

### Tool dispatch

Identical pattern to `SchedulerEndpoint`:

```python
async def deliver(self, envelope: Envelope) -> None:
    if self._handle is None:
        raise EndpointUnavailable(...)
    if envelope.kind != "ToolInvocation":
        await self._reply(envelope, f"warning: unsupported kind '{envelope.kind}'")
        await self._handle.ack(envelope.id)
        return
    tool = envelope.payload.tool
    args = envelope.payload.args
    try:
        result = await self._dispatch(tool, args)
        await self._reply(envelope, json.dumps(result))
    except _ToolError as exc:
        await self._reply(envelope, f"error: {exc}")
    except Exception as exc:
        log.exception("discord tool '%s' raised", tool)
        await self._reply(envelope, f"error: {exc}")
    await self._handle.ack(envelope.id)
```

`_reply` builds an `Acknowledgment` envelope addressed to `incoming.from_`
with `in_reply_to=incoming.id` and `correlation_id=incoming.correlation_id`.

Per-tool args validation via Pydantic models (`_SendArgs`, `_EditArgs`,
etc.) — same pattern as scheduler's `_CreateJobArgs` family.

## Lifecycle

### `start()`

```
1. Load env_file (if set) via python-dotenv (override=False).
2. Read token from os.environ[token_env]. Fail fast if missing.
3. Load access_config_path (if set); merge with permissive defaults.
4. Instantiate discord.Client with required intents
   (default + message_content + reactions).
5. Wire on_message and on_reaction_add handlers (closing over `self`).
6. Schedule client.start(token) as an asyncio task on the bus's event loop.
7. Await on_ready (or a deadline — say 30s).
8. Register self in _active_endpoints[name].
```

Failure during any step: roll back partial init (close client if open,
deregister from map), raise.

### `stop()`

```
1. Deregister self from _active_endpoints.
2. await client.close() (idempotent, safe to call after partial init).
3. Cancel any outstanding typing-indicator tasks.
4. Set self._handle = None.
```

### Reconnects

Trust `discord.py`'s built-in reconnect logic. No manual reconnect handling.

## Configuration loading

Per-endpoint params:

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `target` | str | yes | — | Bus name of the agent that receives inbound. |
| `token_env` | str | yes | — | Name of the env var holding the bot token. |
| `env_file` | path | no | None | Optional `.env` file loaded via `python-dotenv`. |
| `access_config_path` | path | no | None | JSON access config; defaults to permissive if unset. |
| `attachments_dir` | path | no | `~/.agent-core/attachments/<endpoint_name>/` | Download root for `download_attachments`. Predictable, no target-name parsing. Override per agent (e.g. `~/.pepper/attachments`) when desired. |

Tilde expansion: applied at endpoint construction, same pattern as
`SchedulerEndpoint`'s `db_path` / `jobs_path`.

`python-dotenv` is already a transitive dep of agent-core; no new
dependency needed.

## Error handling

- **Discord API errors** during a tool call: `discord.HTTPException` → caught
  by `deliver()`'s outer `except Exception`, logged, replied as
  `error: <msg>`. The envelope is acked (no requeue) since retrying a
  bad-request is unlikely to help.
- **Auth failures** at `start()`: raise `EndpointStartError` (a custom
  exception, or just `RuntimeError` with a clear message). The bus's runner
  surfaces this as a daemon startup failure.
- **Bot disconnects** mid-session: discord.py reconnects. During the
  disconnect window, outbound tool calls fail with `discord.ClientException`
  → handled as above. Inbound is silent (no events fire while disconnected).
- **Access-gate denials** (DM policy / channel allowlist): silent drop. No
  envelope, no log spam.
- **`_active_endpoints` race** (rare): if `on_message` fires before
  `start()` completes the registration, we lose that one message.
  Acceptable — happens for at most a fraction of a second on startup.

## Testing

### Unit tests (no network)

`packages/agent-core-discord/tests/test_discord_endpoint.py`:

- Endpoint Protocol conformance (`isinstance(ep, Endpoint)`).
- Constructor: tilde expansion, default attachments_dir, missing required
  params raise.
- Access gate: DM policy variants, channel allowlist hit/miss, ackReaction
  filtering on inbound reactions.
- Inbound message → envelope shape (mocked discord.Message).
- Inbound reaction → Event envelope shape.
- Tool dispatch: each of the 8 tools — happy path + arg validation + Discord
  API error path (mocked discord.Client). Same pattern as the scheduler's
  per-tool tests.
- Acknowledgment shape (correlation_id, in_reply_to, JSON note).
- Lifecycle: rollback on failed start.

Mocked `discord.Client` lives in a test fixture module; tests inject it via
constructor or attribute override.

### Integration test (real bot, optional)

`packages/agent-core-discord/tests/test_discord_integration.py`:

- Skipped unless `DISCORD_TEST_TOKEN` env var is set.
- Connects to a real Discord application (test bot), runs a small smoke
  flow against a designated test channel. Skip in CI by default.

### Smoke validation against testbot

After PR merges, repeat the bus-daemon / scheduler validation pattern: a
fresh testbot agent in `~/.testbot/` with a real Discord bot token,
verifying:

1. Inbound DM reaches the agent as a TextMessage.
2. Agent replies via `send` tool — message lands in Discord.
3. 👀 ack appears on inbound, disappears after agent's reply.
4. User reaction → `Event` envelope with `discord.reaction_add` arrives.
5. `download_attachments` works.

This is the validation gate before any Pepper migration.

## Open questions (resolved at planning time)

These are sub-decisions deferred from the brainstorm to the implementation
plan:

- **`send_briefing`**: read Pepper's source (`integrations/discord/discord_tools.py`)
  during planning. If unique → keep tool. If wrapper → fold into `send`.
- **Embed validation**: Pydantic model for the embed dict, or
  `discord.Embed.from_dict` directly with try/except? Lean toward the latter
  (no schema duplication) but confirm during plan.
- **Attachments cleanup**: not in v1. Add a TODO to `BACKLOG.md` for the
  7-day sweep Pepper has today.

## Out of scope (deferred — tracked for v2+)

- Slash commands (`/brief`, `/tasks`, `/focus`, `/status`).
- Polls (`create_poll`).
- Scheduled events (`create_scheduled_event`, `cancel_scheduled_event`,
  `list_scheduled_events`).
- Threads (`create_thread`).
- `send_typing` tool.
- Inbound reaction-remove events.
- Components / buttons / select menus.
- Bot Manage Channels permission scope.
- Credentials package integration for token storage.
- Attachments cleanup sweep.
- Per-channel access config (the `channels` dict supports it; v1 ignores
  per-channel settings).

## References

- Pepper's current Discord integration at
  `E:\workspaces\ai\pepper\src\pepper\integrations\discord\` — port-by-recreation
  source material. Files in scope: `bot.py`, `access.py`, `chunking.py`,
  `discord_tools.py`, `embeds.py`, `mcp_server.py`. **Not** in scope:
  `slash_commands.py`, `views.py`, `scheduler.db`.
- Scheduler endpoint (`packages/core/src/agent_core/endpoints/scheduler.py`)
  for the canonical Endpoint Protocol + `_active_endpoints` registry +
  ToolInvocation dispatch + Acknowledgment reply pattern.
- Bus envelope kinds at `packages/core/src/agent_core/bus/envelope.py`
  (`TextMessagePayload`, `EventPayload`, `ToolInvocationPayload`,
  `AcknowledgmentPayload`).
- Roadmap entry in `docs/ROADMAP.md` (sub-project E).
