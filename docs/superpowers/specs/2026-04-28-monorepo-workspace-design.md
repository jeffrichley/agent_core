# Monorepo Workspace & Extension Strategy — Design

> **Sub-project:** A (foundational) — see `docs/ROADMAP.md`.
> **Status:** Awaiting user review.
> **Date:** 2026-04-28

---

## 1. Motivation

`agent-core` is the foundation for a planned ecosystem of AI agents. Pepper
is the first consumer; future consumers (other agents, third-party
integrations) are expected. The repo's structure today — a single
`pyproject.toml` with everything growing inside `src/agent_core/` — works
for one developer and one agent but does not scale to the company-track
goal of letting external authors ship integrations against stable
protocols.

This design establishes the repo and packaging model that supports that
goal. It is a **structural** design — what packages exist, how they relate,
how they're versioned and released — not a feature design. Subsequent
sub-projects (B–H in the roadmap) build on top of it.

The defining constraint: ecosystem orientation without ecosystem-scale
overhead today. We pay the workspace setup cost once, then spend months
mostly writing Pepper-driven features inside the right package boundaries.
When the first external integration arrives, the structure is already
shaped for it.

---

## 2. Package layout

### 2.1 Tiered package set

**Tier 1 — Core (stable surface, dependency-light):**

- `agent-core` — protocols (`HookTool`, `Endpoint`, `BusHook`), bus core,
  hook pipeline runner, shared Pydantic models, the `agent-core` umbrella
  CLI (a thin entrypoint that other packages extend via entry-points; see
  §5.1). Dependencies: `pydantic`, `typer`, `aiosqlite`, `pyyaml`. Third
  parties code against this; its API is the contract.

**Tier 2a — Official transports:**

- `agent-core-mcp` — MCP-stdio endpoint (Claude Code ↔ bus). Replaces
  Pepper's `channel/server.py` MCP-side gymnastics.
- `agent-core-http` — HTTP/SSE endpoint. Replaces `channel/server.py` HTTP
  side. Pulls in `uvicorn`.

**Tier 2b — Official integrations:**

- `agent-core-discord` — Discord adapter (bot + bus endpoint + 16 MCP
  tools + attachment download). Pulls in `discord.py`.
- `agent-core-scheduler` — APScheduler endpoint + scheduler MCP tools.
- `agent-core-email` — already exists; `agentmail`-based.
- `agent-core-notify` — already exists; desktop-notification MCP server
  (`desktop-notifier`-based).

**Tier 3 — Utilities:**

- `agent-core-credentials` — KeePass-backed credential vault and CLI
  subcommand.

**Out of scope for this repo:**

- `pepper` — stays in its own repo. Imports from `agent-core-*` packages.
  It is the canonical first consumer, not part of the framework.

**Deferred (added when their sub-projects land):**

- `agent-core-dashboard` (sub-project G).
- `agent-core-backup` — TBD whether it ships as its own package or as a
  subsystem in core (sub-project D resolves this).

### 2.2 Dependency rules

1. Core depends on nothing else in the workspace.
2. Tier 2 and tier 3 packages depend only on `agent-core`. They never
   depend on each other.
3. Integrations communicate with each other only via the bus. If
   `agent-core-discord` needs a scheduled job, it publishes an envelope
   `to: scheduler` rather than importing the scheduler package.

This decoupling makes each package independently installable and
independently releasable. It also keeps the dependency graph a tree, not
a mesh.

### 2.3 Enforcement (three layers)

1. **Declared dependencies (pyproject.toml).** Each package's `dependencies`
   list permits only `agent-core` (and external libraries). A small CI
   audit script parses each package's pyproject and rejects any PR that
   adds another workspace member as a dependency outside the allowed set.
2. **Static import checking (`import-linter`).** A `.importlinter` config
   declares contracts of the form "no integration package may import any
   other integration package." Run as a CI gate. `import-linter` is
   mature; preferred over the newer `Tach`.
3. **Runtime smoke (deferred).** Per-package tests that import only the
   public API and assert no other workspace package appears in
   `sys.modules`. Layered in only if static analysis ever proves
   insufficient.

Layers 1 and 2 ship from day one. Layer 3 is reserved.

---

## 3. Workspace mechanics

### 3.1 Directory layout

```
agent-core/                          ← repo root
├── pyproject.toml                   ← workspace root + dev deps + shared ruff/mypy/pytest
├── uv.lock                          ← single lockfile for the whole workspace
├── packages/
│   ├── core/
│   │   ├── pyproject.toml           ← name = "agent-core"
│   │   ├── src/agent_core/...
│   │   ├── tests/
│   │   ├── changelog.d/             ← towncrier fragments
│   │   ├── towncrier.toml
│   │   └── CHANGELOG.md
│   ├── discord/
│   │   ├── pyproject.toml           ← name = "agent-core-discord"
│   │   ├── src/agent_core_discord/...
│   │   ├── tests/
│   │   ├── changelog.d/
│   │   ├── towncrier.toml
│   │   └── CHANGELOG.md
│   └── ... (one directory per package)
├── tests/integration/               ← cross-cutting ecosystem tests
├── docs/
│   ├── ROADMAP.md
│   ├── BACKLOG.md
│   ├── superpowers/specs/
│   └── superpowers/plans/
└── .github/workflows/               ← per-package matrix CI
```

### 3.2 Root `pyproject.toml` (selected fields)

```toml
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
agent-core              = { workspace = true }
agent-core-mcp          = { workspace = true }
agent-core-http         = { workspace = true }
agent-core-discord      = { workspace = true }
agent-core-scheduler    = { workspace = true }
agent-core-email        = { workspace = true }
agent-core-notify       = { workspace = true }
agent-core-credentials  = { workspace = true }

[dependency-groups]
# Dev tools only; real package deps live in member pyprojects.
dev = ["pytest", "ruff", "mypy", "import-linter", "towncrier"]

[tool.ruff]
line-length = 100
# Shared across all packages

[tool.mypy]
strict = true
# Shared across all packages
```

### 3.3 Member `pyproject.toml` (example)

```toml
[project]
name = "agent-core-discord"
version = "0.1.0"
dependencies = [
    "agent-core>=0.1,<1.0",
    "discord.py>=2.7",
]

[project.scripts]
agent-core-discord = "agent_core_discord.main:run"

[project.entry-points."agent_core.endpoints"]
discord = "agent_core_discord.endpoint:DiscordEndpoint"
```

### 3.4 Developer experience

`uv sync` at the repo root creates one `.venv/` with every workspace member
installed editable. Cross-package imports during dev resolve to the local
source. `pytest` from the root runs all tests across all packages. PyPI
publishes ship the released versions independently.

---

## 4. Migration path

We have a merged Channel Bus Phase 1 and working Claude Code hooks on
`main`. Migration must keep CI green at every step. Each step below is a
distinct PR (some may be split further when their plan lands).

```
Step 0  ✅ ROADMAP and this design committed (current state).

Step 1  Restructure to workspace.
        Move src/agent_core/ → packages/core/src/agent_core/.
        Move tests/ → packages/core/tests/.
        Add root pyproject with [tool.uv.workspace].
        Single workspace member today: agent-core. Everything else follows.
        Verify: uv sync, pytest, ruff, mypy, hooks, .mcp.json all green.

Step 2  Carve out agent-core-email and agent-core-notify into their own
        packages/ directories. Both already exist as subpackages today.
        Update entry points in their member pyprojects.

Step 3  Port agent-core-credentials from Pepper.

Step 4  Port agent-core-scheduler from Pepper.

Step 5  Build agent-core-mcp (stdio transport endpoint).
        Replaces Pepper's channel/server.py MCP side.

Step 6  Build agent-core-http (HTTP/SSE transport endpoint).
        Replaces channel/server.py HTTP side.

Step 7  Port agent-core-discord from Pepper.

Step 8  Pepper-side cleanup: replace Pepper's channel/, scheduler/,
        credentials/, discord/, attachments.py with imports from
        agent-core-* packages. Delete the old code.
```

### 4.1 Two design rules across all steps

1. **One package moves per PR.** No big-bang restructures. Each step lands
   behind passing CI.
2. **Pepper keeps working at every step.** Pepper does not migrate to any
   `agent-core-*` package until step 8 (or per-package step 8a, 8b, etc).
   This is the safety guarantee — until then Pepper continues using its
   in-tree implementations.

---

## 5. Plugin discovery & module naming

### 5.1 Entry-points groups

Three groups, one per protocol that core defines:

| Group | What it registers | Used by |
|---|---|---|
| `agent_core.endpoints` | Bus endpoints | `agent_core.yaml` `endpoints:` |
| `agent_core.bus_hooks` | `pre_publish` / `pre_deliver` transformers | `bus_hooks:` |
| `agent_core.hook_tools` | Claude Code lifecycle tools | `pipelines:` |

A third party publishes `agent-core-myintegration`; their pyproject
declares:

```toml
[project.entry-points."agent_core.endpoints"]
myintegration = "agent_core_myintegration.endpoint:MyEndpoint"
```

In `agent_core.yaml`, users reference it by short name (`class: myintegration`)
or by full dotted path (`class: agent_core_myintegration.endpoint.MyEndpoint`).

**Dotted-path always works** — it is the escape hatch for packages that
don't declare an entry point. Entry-points add ergonomics and discovery
(`agent-core list endpoints` enumerates installed integrations). The
existing dotted-path config in `agent_core.yaml` keeps working unchanged;
this addition is purely additive.

### 5.2 Module naming: separate top-level, not namespace package

Each PyPI package gets its own importable top-level module:

| PyPI distribution | Import name |
|---|---|
| `agent-core` | `agent_core` |
| `agent-core-discord` | `agent_core_discord` |
| `agent-core-scheduler` | `agent_core_scheduler` |
| `agent-core-mcp` | `agent_core_mcp` |
| `agent-core-http` | `agent_core_http` |
| `agent-core-email` | `agent_core_email` |
| `agent-core-notify` | `agent_core_notify` |
| `agent-core-credentials` | `agent_core_credentials` |

We deliberately avoid PEP 420 namespace packages
(`agent_core.discord` etc.). Namespace packages have well-documented
build-time pain: mypy stubs collide, packagers disagree about layout,
each tool needs special-casing. LangChain migrated *away* from namespace
packages for these reasons; we follow that lesson.

Trade-off accepted: import paths read
`from agent_core_discord import DiscordEndpoint` rather than the prettier
`from agent_core.discord import DiscordEndpoint`.

---

## 6. Versioning & release

### 6.1 Per-package independent versioning

| Package | Versioning | Stability promise |
|---|---|---|
| `agent-core` | Strict semver. Major = breaking protocol change. | The Endpoint / BusHook / HookTool protocols and envelope wire format are the contract. Within a major, additions only. |
| Tier 2 & 3 official | Independent semver per package. | Each pins `agent-core>=X.Y,<(X+1).0` and evolves under that pin. |
| Third-party `agent-core-*` | Independent semver. | Same compatibility pin; ecosystem decides its own pace. |

This mirrors pytest+plugins, FastAPI+starlette, and langchain-core+integrations.
Lowest-coordination model that still answers "is my install compatible."

### 6.2 Release mechanics

- **Trunk-based on `main`.** Feature branches → PRs → merge → main always
  green.
- **Tags drive publishes.** Tag format: `<package-name>-vX.Y.Z`
  (e.g. `agent-core-discord-v0.3.1`). One tag per release. CI workflow on
  tag push runs build + publishes that one package to PyPI.
- **CHANGELOG.md per package** under `packages/<name>/CHANGELOG.md`.
- **Towncrier from day one.** Each package has its own `towncrier.toml`
  and `changelog.d/`. Every code-changing PR adds a fragment file in
  `changelog.d/` named `<issue-or-pr>.<category>.md` containing one
  sentence. Categories: `added`, `changed`, `fixed`, `removed`,
  `deprecated`, `security`. At release: `towncrier build --version=X.Y.Z`
  for the package being released, generating the CHANGELOG section and
  removing the fragments. Fragments are individual files, so PRs never
  conflict on CHANGELOG.md.
- **Pre-1.0 phase** (current state): everything `0.x.y`. Allowed to break
  things between minors with a clear changelog note. Gives us room to
  find the right protocol shape before declaring it.
- **`agent-core` 1.0** is the marketing event: protocols are stable, build
  against them with confidence. Integrations stay 0.x until they
  individually earn 1.0.

---

## 7. Testing & CI

### 7.1 Test layout

```
packages/<name>/tests/        ← unit + per-package integration; owned by that package
tests/integration/            ← ecosystem cross-cutting tests
                                (e.g., Discord publishes → bus → scheduler picks up)
```

### 7.2 Quality gates

| Gate | Tool | Scope |
|---|---|---|
| Format + lint | `ruff` | Whole workspace, root config |
| Static types | `mypy --strict` | Per-package, root config with package-specific overrides |
| Architecture contracts | `import-linter` | Workspace; rules from §2.3 |
| Unit + integration | `pytest` | Each package + root `tests/integration/` |
| Lockfile sync | `uv lock --check` | Root |

Run via `just gate` locally and as the merge-gate in CI.

### 7.3 CI matrix

- Path-filtered jobs: changes in `packages/discord/**` only run the
  Discord package's gates plus root cross-cutting. Discord PRs do not run
  Scheduler tests.
- One root job always runs: `import-linter` + `uv lock --check` +
  cross-cutting integration tests.
- On tag push (`<package>-vX.Y.Z`): build + publish that one package to
  PyPI.

---

## 8. Open items (deferred decisions)

These do not block the design. Recorded so they aren't lost.

| Item | Status | Resolves in |
|---|---|---|
| **Repo / brand name.** `agent-core` may be too generic for a company-track product. The design does not depend on the name; packages can be renamed via PyPI redirects later. | Pending — owner deciding. | Whenever a name lands. |
| **License.** Private repo today. When public: permissive (MIT/Apache) for max ecosystem adoption vs source-available (BSL/Elastic) vs dual-license (open core + commercial). | Deferred. | Its own brainstorm when relevant. |
| **Lifecycle CLI home.** Lives in `agent-core` core; the umbrella `agent-core` command extends with subcommands from each package via entry-points. | Marker decision. | Sub-project B brainstorm. |
| **Pepper repo location.** Stays its own repo for now. Could fold in as `apps/pepper/` if monorepo unification ever makes sense. | Stays separate. | Re-evaluate when a second consumer agent appears. |

---

## 9. Out of scope for this design

- Feature-level work for any sub-project B–H. They get their own brainstorm
  → spec → plan cycle on top of this structural foundation.
- The actual mechanical move of source files, test files, and CI workflows
  from the current flat layout to `packages/core/`. That belongs to the
  implementation plan, not the design.
- Naming, licensing, and the lifecycle CLI's internal design (see §8).
