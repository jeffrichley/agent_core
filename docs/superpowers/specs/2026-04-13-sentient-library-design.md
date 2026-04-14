# Sentient Library Design Spec

**Date:** 2026-04-13
**Status:** Approved
**Author:** Jeff Richley + Claude

## Vision

Transform the agent_core repository into **Sentient** — an installable, modular Python library for creating persistent AI agents. Each agent is a self-contained directory (e.g., `~/.pepper/`) that a user can `cd` into and launch with any supported CLI coding agent (`claude`, `cursor`, `codex`, etc.). The system uses a monorepo with uv workspaces, strategy patterns for pluggable providers, and an interactive scaffolder that generates exactly what's needed.

The long-term vision includes a managed cloud offering (`sentient-cloud`) where users pay monthly and skip all provider configuration.

## Guiding Principles

- **Self-contained agents** — `cd ~/.pepper && claude` and it just works. No external setup.
- **Strategy pattern everywhere** — LLM providers, auth, adapters, and communication channels are all pluggable protocols. Config selects implementations.
- **Design for all, build for Claude Code first** — The architecture supports every major CLI agent. Phase 1 ships Claude Code only.
- **One source of truth** — The agent's `config.toml` declares pipelines and integrations. Adapters translate it into platform-native configs.
- **Thin adapters** — Adapters generate config files at scaffold time and provide a hook bridge at runtime. They don't contain business logic.

## Repository Structure

```
sentient/                              # Workspace root
├── pyproject.toml                     # uv workspace config (not a package itself)
├── CLAUDE.md                          # Dev instructions for contributors
├── packages/
│   ├── sentient-core/                 # Protocol, models, pipeline engine
│   │   ├── pyproject.toml
│   │   └── src/sentient/core/
│   │       ├── models.py             # ToolResult, ToolConfig, PipelineConfig, AgentManifest
│   │       ├── protocols.py          # LLMProvider, AuthProvider, AdapterProtocol, ChannelProvider
│   │       ├── hooks/
│   │       │   ├── protocol.py       # HookTool protocol
│   │       │   └── pipeline.py       # Pipeline loader/runner/renderer
│   │       ├── transcript.py         # Shared transcript reader
│   │       └── tools/                # Built-in hook tools (TimeInjector, FileInjector, etc.)
│   │
│   ├── sentient-memory/              # Memory compiler, vault, knowledge base
│   │   ├── pyproject.toml            # Depends on sentient-core
│   │   └── src/sentient/memory/
│   │       ├── compiler.py           # Daily logs -> knowledge articles
│   │       ├── query.py              # Knowledge base query
│   │       ├── lint.py               # Health checks
│   │       ├── vault.py              # Vault I/O (read/write memory files)
│   │       ├── config.py             # Path constants
│   │       └── hooks/                # Memory-specific hook tools
│   │           ├── session_start.py  # Inject identity + knowledge index
│   │           ├── session_end.py    # Flush to daily logs
│   │           └── pre_compact.py    # Capture before compaction
│   │
│   ├── sentient-cli/                 # Scaffolder + management CLI
│   │   ├── pyproject.toml            # Depends on core, memory, all adapters
│   │   └── src/sentient/cli/
│   │       ├── app.py                # Typer entrypoint
│   │       ├── new.py                # `sentient new` — interactive agent creator
│   │       ├── start.py              # `sentient start` — launch agent session
│   │       ├── stop.py               # `sentient stop` — kill background session
│   │       └── status.py             # `sentient status` — health check
│   │
│   ├── sentient-mcp/                 # Universal MCP server
│   │   ├── pyproject.toml            # Depends on core, memory
│   │   └── src/sentient/mcp/
│   │       └── server.py             # FastMCP server exposing tools
│   │
│   └── sentient-cloud/               # Managed service client (Phase 4)
│       ├── pyproject.toml            # Depends on core
│       └── src/sentient/cloud/
│           ├── llm.py                # LLMProvider via Sentient API
│           ├── memory.py             # Remote memory storage
│           ├── sync.py               # Local vault <-> cloud sync
│           └── auth.py               # Sentient API key auth
│
├── adapters/
│   ├── sentient-adapter-claude/      # Claude Code adapter
│   │   ├── pyproject.toml            # Depends on sentient-core
│   │   └── src/sentient/adapters/claude/
│   │       ├── generator.py          # Generates .claude/, CLAUDE.md, .mcp.json
│   │       └── hook_bridge.py        # Translates Claude hook I/O <-> pipeline
│   │
│   ├── sentient-adapter-codex/       # Codex CLI adapter (Phase 3)
│   ├── sentient-adapter-cursor/      # Cursor adapter (Phase 3)
│   ├── sentient-adapter-gemini/      # Gemini CLI adapter (Phase 3)
│   ├── sentient-adapter-kiro/        # Kiro CLI adapter (Phase 3)
│   ├── sentient-adapter-goose/       # Goose adapter (Phase 3, MCP-only)
│   └── sentient-adapter-aider/       # Aider adapter (Phase 3, wrapper)
│
├── providers/
│   ├── sentient-llm-anthropic/       # Anthropic SDK LLM provider
│   │   ├── pyproject.toml            # Depends on core + anthropic/claude-agent-sdk
│   │   └── src/sentient/llm/anthropic/
│   │       └── provider.py
│   │
│   ├── sentient-llm-openai/          # OpenAI SDK provider (Phase 3)
│   ├── sentient-llm-google/          # Google GenAI provider (Phase 3)
│   ├── sentient-llm-litellm/         # Universal multi-provider (Phase 3)
│   └── sentient-llm-bedrock/         # AWS Bedrock provider (Phase 3)
│
├── integrations/
│   ├── sentient-discord/             # Discord bot
│   │   ├── pyproject.toml            # Depends on core + channel
│   │   └── src/sentient/discord/
│   │
│   ├── sentient-scheduler/           # APScheduler job system
│   │   ├── pyproject.toml            # Depends on core + channel
│   │   └── src/sentient/scheduler/
│   │
│   ├── sentient-channel/             # Message routing server
│   │   ├── pyproject.toml            # Depends on core
│   │   └── src/sentient/channel/
│   │
│   └── sentient-credentials/         # KeePass credential store
│       ├── pyproject.toml            # Depends on core
│       └── src/sentient/credentials/
│
└── templates/
    └── default/                      # Default agent template
        ├── Memory/
        │   ├── SOUL.md
        │   ├── IDENTITY.md
        │   ├── USER.md
        │   ├── MEMORY.md
        │   ├── OPERATIONS.md
        │   ├── HEARTBEAT.md
        │   ├── HABITS.md
        │   └── TASKS.md
        ├── config.toml.j2            # Jinja2 template for agent config
        └── .gitignore.j2
```

## Workspace Configuration

The workspace root `pyproject.toml` is not a package — it declares the workspace:

```toml
[project]
name = "sentient-workspace"
version = "0.0.0"
requires-python = ">=3.12"

[tool.uv.workspace]
members = [
    "packages/*",
    "adapters/*",
    "providers/*",
    "integrations/*",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "ruff>=0.14",
]
```

## Dependency Graph

```
sentient-core  (no internal deps — pydantic, typer, pyyaml, rich)
    │
    ├── sentient-memory  (+ LLMProvider at runtime, no hard SDK dep)
    │
    ├── sentient-llm-anthropic  (+ anthropic / claude-agent-sdk)
    ├── sentient-llm-openai     (+ openai)              [Phase 3]
    ├── sentient-llm-google     (+ google-genai)         [Phase 3]
    ├── sentient-llm-litellm    (+ litellm)              [Phase 3]
    ├── sentient-llm-bedrock    (+ boto3)                [Phase 3]
    │
    ├── sentient-adapter-claude  (generates .claude/ configs)
    ├── sentient-adapter-cursor  [Phase 3]
    ├── sentient-adapter-codex   [Phase 3]
    ├── sentient-adapter-gemini  [Phase 3]
    ├── sentient-adapter-kiro    [Phase 3]
    ├── sentient-adapter-goose   [Phase 3]
    ├── sentient-adapter-aider   [Phase 3]
    │
    ├── sentient-channel         (standalone message router)
    │   ├── sentient-discord     (+ discord.py)
    │   └── sentient-scheduler   (+ apscheduler)
    │
    ├── sentient-credentials     (+ pykeepass)
    │
    ├── sentient-mcp             (+ mcp sdk, depends on core + memory)
    │
    ├── sentient-cloud           [Phase 4]
    │
    └── sentient-cli             (depends on core + memory + all adapters)
```

Rules:
- **core** has no internal dependencies
- **memory** depends on core only (uses LLMProvider protocol, not a specific SDK)
- **adapters** depend on core only — stateless config generators
- **providers** depend on core + their specific SDK
- **channel** depends on core only — it's the message bus
- **discord, scheduler** depend on core + channel
- **credentials** depends on core only
- **mcp** depends on core + memory
- **cli** knows about everything (lists adapters/integrations for the installer)
- **cloud** depends on core only — another strategy implementation

## Strategy Patterns

Core defines protocols. Packages provide implementations. The agent's `config.toml` selects which strategy to use. Implementations are discovered at runtime via Python entry points (`importlib.metadata.entry_points()`).

### LLM Provider

```python
class LLMProvider(Protocol):
    async def query(self, system: str, prompt: str, max_tokens: int = 4096) -> str: ...
```

Registered via entry points:
```toml
# In sentient-llm-anthropic/pyproject.toml
[project.entry-points."sentient.llm"]
anthropic = "sentient.llm.anthropic:AnthropicProvider"
```

### Auth Provider

```python
class AuthProvider(Protocol):
    def get_credential(self, service: str) -> dict[str, str]: ...
```

Implementations: env vars (built into core), KeePass (`sentient-credentials`), config inline, Sentient Cloud (Phase 4).

### Adapter Protocol

```python
class AdapterProtocol(Protocol):
    name: str                    # "claude", "cursor", etc.
    display_name: str            # "Claude Code", "Cursor", etc.

    def generate(self, agent_dir: Path, manifest: AgentManifest) -> None:
        """Write all platform config files into agent_dir."""
        ...

    def validate(self, agent_dir: Path) -> list[str]:
        """Check generated config is valid. Returns list of issues."""
        ...
```

### Channel Provider

```python
class Message(BaseModel):
    id: str
    channel_type: str            # "discord", "slack", etc.
    channel_id: str              # Platform-specific ID
    sender: str
    content: str
    timestamp: datetime
    attachments: list[Attachment]
    metadata: dict               # Platform-specific extras
    reply_to: str | None

class Attachment(BaseModel):
    filename: str
    url: str
    content_type: str
    size_bytes: int

class ChannelProvider(Protocol):
    name: str

    async def send(self, channel_id: str, content: str,
                   attachments: list[Attachment] | None = None,
                   reply_to: str | None = None) -> Message: ...

    async def fetch(self, channel_id: str, limit: int = 50,
                    since: datetime | None = None) -> list[Message]: ...

    async def edit(self, channel_id: str, message_id: str,
                   content: str) -> Message: ...

    async def react(self, channel_id: str, message_id: str,
                    reaction: str) -> None: ...

    def capabilities(self) -> set[str]: ...
        # e.g. {"send", "fetch", "edit", "react", "threads", "embeds", "polls"}
```

Channel IDs are namespaced: `discord:12345`, `slack:C04ABCDEF`. The channel server parses the prefix and routes to the correct provider. Providers register via entry points:

```toml
[project.entry-points."sentient.channels"]
discord = "sentient.discord:DiscordChannel"
```

## Scaffolder (`sentient new`)

### Interactive Flow

```
$ sentient new pepper ~/.pepper

Creating a new life: Pepper

Core (always included):
  - sentient-core
  - sentient-memory

Integrations:
  [x] Discord
  [x] Scheduler
  [x] Channel Server     (auto-selected: required by Discord, Scheduler)
  [ ] Credentials

CLI Agents:
  [x] Claude Code
  [ ] Cursor
  [ ] Codex CLI
  [ ] Gemini CLI
  [ ] Kiro CLI
  [ ] Goose
  [ ] Aider

LLM Provider:
  (x) Anthropic (recommended for Claude Code)
  ( ) OpenAI
  ( ) LiteLLM (multi-provider)
  ( ) Sentient Cloud

Installing to ~/.pepper/ ...
  - pyproject.toml
  - config.toml
  - Memory/ vault
  - .claude/ hooks + settings
  - uv sync

Pepper is alive. cd ~/.pepper && claude
```

### What Gets Generated

```
~/.pepper/
├── pyproject.toml              # Selected dependencies only
├── config.toml                 # Agent master config
├── .python-version             # 3.12
├── .env                        # Secrets (gitignored)
├── .gitignore
├── Memory/
│   ├── SOUL.md                 # "You are Pepper..."
│   ├── IDENTITY.md             # Name, emoji, role
│   ├── USER.md                 # Blank template for user to fill
│   ├── MEMORY.md               # Empty, grows over time
│   ├── OPERATIONS.md           # Vault map
│   ├── HEARTBEAT.md            # Check schedule
│   ├── HABITS.md               # Daily habits
│   ├── TASKS.md                # Task inbox
│   ├── daily/raw/              # Transcript logs (auto-populated)
│   └── daily/summaries/        # Nightly reflections (auto-populated)
├── knowledge/                  # Compiled knowledge base (grows over time)
│   ├── index.md
│   ├── concepts/
│   ├── connections/
│   └── qa/
├── .claude/                    # Generated by Claude adapter
│   ├── settings.json
│   └── skills/
└── CLAUDE.md                   # Generated — loads identity, sets conventions
```

### Generated `pyproject.toml`

```toml
[project]
name = "pepper"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "sentient-core",
    "sentient-memory",
    "sentient-llm-anthropic",
    "sentient-adapter-claude",
    "sentient-discord",
    "sentient-scheduler",
    "sentient-channel",
]
```

### Generated `config.toml`

```toml
[agent]
name = "Pepper"
version = "0.1.0"

[llm]
provider = "sentient.llm.anthropic"
model = "claude-sonnet-4-20250514"

[llm.auth]
provider = "env"                # reads ANTHROPIC_API_KEY from .env

[memory]
vault_path = "Memory"
knowledge_path = "knowledge"

[pipelines.SessionStart]
tools = [
    { tool = "sentient.core.tools.TimeInjector", params = { format = "%A, %B %d, %Y %I:%M %p %Z" } },
    { tool = "sentient.core.tools.FileInjector", params = { files = ["Memory/IDENTITY.md", "Memory/SOUL.md", "Memory/USER.md"] } },
    { tool = "sentient.memory.hooks.session_start.KnowledgeIndexInjector" },
]

[pipelines.SessionEnd]
tools = [
    { tool = "sentient.memory.hooks.session_end.DailyLogFlusher" },
]

[pipelines.PreCompact]
tools = [
    { tool = "sentient.memory.hooks.pre_compact.ContextCapture" },
]

[integrations]
discord = true
scheduler = true
channel = true
credentials = false
```

## Runtime Event Flow

When someone runs `cd ~/.pepper && claude`:

1. **Claude Code starts** — reads `.claude/settings.json`, fires `SessionStart` hook
2. **Hook calls `sentient-hook SessionStart`** — the universal hook bridge CLI (entrypoint from `sentient-core`)
3. **`sentient-hook` reads `config.toml`** from the current directory — finds the pipeline for `SessionStart`
4. **Pipeline runs tools in order** — TimeInjector, FileInjector, KnowledgeIndexInjector
5. **Each tool returns `ToolResult(heading, content)`** — pipeline collects and renders to markdown
6. **`sentient-hook` writes JSON to stdout** — Claude Code injects as `additionalContext`
7. **Pepper is awake** — identity loaded, time set, knowledge available

### Lifecycle Events

| Event | What Happens |
|-------|-------------|
| `SessionStart` | Inject identity, time, knowledge index |
| `PreCompact` | Capture current context to daily log before compression |
| `SessionEnd` | Flush conversation to daily log |
| `PreToolUse` | (Future) Guard rails, approval gates |
| `PostToolUse` | (Future) Logging, knowledge capture |

### LLM-Powered Tools at Runtime

When a tool (e.g., `HandoffWriter`, `DailyLogFlusher`) needs to call an LLM:

1. Reads `[llm]` section from `config.toml`
2. Resolves `LLMProvider` implementation via entry points
3. Provider handles auth (reads API key per `[llm.auth]` config)
4. Tool calls `await provider.query(system=..., prompt=...)`
5. Tool doesn't know or care which SDK is underneath

### Full Runtime Chain

```
Claude Code
  -> .claude/settings.json (generated by adapter at scaffold time)
    -> uv run sentient-hook SessionStart
      -> reads config.toml
        -> Pipeline loads tools (from sentient-core, sentient-memory)
          -> Tools needing LLM resolve LLMProvider from config
            -> Provider uses anthropic SDK (or whatever is configured)
        -> Pipeline renders results to markdown
      -> writes JSON to stdout
  -> Claude Code injects additionalContext
```

## CLI Agent Compatibility Research

Research conducted 2026-04-13 across all major CLI coding agents. Key findings:

### Tier 1: Nearly Identical Hook Systems (thin adapter needed)

**Claude Code** — Full plugin system. Hooks in `.claude/settings.json`, skills in `.claude/skills/`, MCP via `.mcp.json`. Plugin marketplace for distribution. JSON stdin/stdout hook protocol.

**Codex CLI (OpenAI)** — Hooks in `hooks.json` (behind `codex_hooks` feature flag). Five events: SessionStart, PreToolUse, PostToolUse, UserPromptSubmit, Stop. Skills via `SKILL.md`. MCP in `~/.codex/config.toml`. Nearly identical hook protocol to Claude Code.

**Cursor CLI** — Full hook system in `.cursor/hooks.json`. Events: sessionStart, sessionEnd, preToolUse, postToolUse, beforeSubmitPrompt, preCompact, stop, plus tool-specific hooks. Skills via `SKILL.md` (also reads `.claude/skills/` for compat). MCP in `.cursor/mcp.json`. Rules in `.cursor/rules/*.md`.

**Kiro CLI (formerly Amazon Q)** — Custom agents via `.kiro/agents/*.json`. Five hook events: agentSpawn, userPromptSubmit, preToolUse, postToolUse, stop. Skills and steering files. MCP in `.kiro/settings/mcp.json`.

**Gemini CLI** — Full extension system. 11 hook events including BeforeAgent, AfterAgent, BeforeTool, AfterTool, BeforeModel, AfterModel, PreCompress. Extensions install via `gemini extensions install <repo>`. MCP in `settings.json`.

### Tier 2: MCP-Only (no lifecycle hooks)

**Goose (Block)** — Everything is an MCP server. No lifecycle hooks. Context via `.goosehints` or MOIM (per-turn file injection). Skills via `SKILL.md`. Recipes for packaging workflows.

### Tier 3: No Plugin System (wrapper/config approach)

**Aider** — No plugins, no hooks, no MCP. Integration via `--read` files for context and `--lint-cmd`/`--test-cmd` for post-edit hooks. Best approach: shell wrapper that manages lifecycle around aider.

### Key Insight

Almost every major CLI agent has converged on three extension primitives:
1. **Lifecycle hooks** — shell commands triggered by events, JSON stdin/stdout
2. **Skills** — `SKILL.md` directories with frontmatter + instructions
3. **MCP servers** — tools exposed via Model Context Protocol

The hook protocols are nearly identical. Differences are mostly field names and which events are supported. This validates the thin-adapter architecture.

## Phasing

### Phase 1 — Build Now

| Package | Notes |
|---------|-------|
| `sentient-core` | Migrate from agent_core. Models, protocols, pipeline, built-in tools, `sentient-hook` entrypoint |
| `sentient-memory` | Migrate from memory-compiler. Refactored to use LLMProvider protocol |
| `sentient-llm-anthropic` | First LLM provider. Wraps anthropic / claude-agent-sdk |
| `sentient-adapter-claude` | First adapter. Generates `.claude/`, `CLAUDE.md`, `.mcp.json` |
| `sentient-cli` | Scaffolder: `sentient new`, `sentient start`, `sentient stop`, `sentient status` |
| `templates/default/` | Memory vault templates extracted from Pepper |
| Workspace root | `pyproject.toml`, shared dev deps, ruff/pytest config |

### Phase 2 — Migrate from Pepper

| Package | Source |
|---------|--------|
| `sentient-discord` | `src/pepper/integrations/discord/` |
| `sentient-scheduler` | `src/pepper/scheduler/` |
| `sentient-channel` | `src/pepper/channel/` |
| `sentient-credentials` | `src/pepper/credentials/` |
| `sentient-mcp` | New — universal MCP server |

### Phase 3 — Additional Adapters & Providers

| Package | Priority |
|---------|----------|
| `sentient-adapter-cursor` | High |
| `sentient-adapter-codex` | High |
| `sentient-adapter-kiro` | Medium |
| `sentient-adapter-gemini` | Medium |
| `sentient-adapter-goose` | Low |
| `sentient-adapter-aider` | Low |
| `sentient-llm-openai` | When Codex/Cursor adapters ship |
| `sentient-llm-google` | When Gemini adapter ships |
| `sentient-llm-litellm` | Universal fallback |
| `sentient-llm-bedrock` | When Kiro adapter ships |

### Phase 4 — Cloud & Revenue

| Item | Notes |
|------|-------|
| `sentient-cloud` | Managed service client. LLMProvider via Sentient API |
| Cloud-synced memory | Vault backup + sync |
| Hosted scheduler | Run jobs without local process |
| Managed integrations | Discord bot hosting, etc. |
| Marketplace | Community templates, integrations, tools |

### Cloud Tier Model (Future)

| | Self-hosted | Cloud Lite | Cloud Pro |
|---|---|---|---|
| LLM calls | Your API keys | Sentient API | Sentient API |
| Memory/vault | Local files | Local files | Cloud-synced |
| Scheduler | Local process | Local process | Cloud-hosted |
| Integrations | Self-managed | Self-managed | Managed |
| Price | Free (OSS) | $/mo | $$/mo |

## Open Questions

1. **Project work** — How does an agent work on external projects? (`--add-dir`, symlinks, project-level configs?) Options: (A) tell the agent in conversation to navigate there, (B) launch with `--add-dir`, (C) defer entirely.
2. **Config sync** — Should adapters auto-regenerate platform configs when `config.toml` changes? File watcher? Manual `sentient sync`?
3. **Integration discovery** — How do integrations find each other at runtime? Channel server URL, ports, service registry?
4. **MCP architecture** — One universal MCP server or per-integration MCP servers?
5. **Package naming** — "sentient" placeholder. Check PyPI availability, trademark concerns.
6. **Agent upgrades** — `sentient upgrade` to update packages + regenerate adapter configs?
7. **Template migration** — What happens when template structure evolves and existing agents need migration?
8. **Multi-agent communication** — Can agents talk to each other?
9. **Cloud API design** — REST? GraphQL? Contract?
10. **Pricing model** — For cloud tier
