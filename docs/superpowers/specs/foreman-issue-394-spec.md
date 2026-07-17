# Spec: getting-started guide — `uv add` → hatch → daemon → connect (issue #394)

## Goal

Create a complete end-to-end getting-started guide for new adopters of agent-core, replacing the existing source-dev-oriented `docs/getting-started/index.md` with a guide that installs from PyPI, hatches a being, starts the daemon, connects the being's Claude Code session, and verifies a round-trip message. Also trim `README.md` to eliminate the `uv sync` source-dev setup section and point readers to the docs site. See issue #394; part of Theme F Track A (#262, #269), design doc at `docs/superpowers/specs/2026-07-16-theme-f-track-a-pypi-launch-design.md`.

---

## Acceptance criteria

- `docs/getting-started/index.md` is rewritten and contains five distinct, numbered stages in order: Install, Hatch a being, Start the daemon, Connect your Claude Code session, Verify the round trip.
- Every command in the guide uses PyPI-installable packages (`agent-core`, `agent-core-hatchery`); no command requires cloning the repository or running `uv sync`.
- The `.mcp.json` block shows both Windows (`Scripts\python.exe`) and POSIX (`bin/python`) paths using `pymdownx.tabbed` tabs (the extension is already present in `mkdocs.yml` as `pymdownx.tabbed: alternate_style: true`).
- All being-identity strings use placeholders (`<your-being>`, `<you>`) with no hardcoded Wren, Pepper, Jeff, or other personal names.
- `uv run --group docs mkdocs build --strict` passes with zero warnings after the change.
- `README.md` no longer contains the `## Setup` block with `uv sync`; it instead contains a `## Documentation` section linking to `https://jeffrichley.github.io/agent_core/getting-started/`.
- `README.md`'s badge block, one-line description, `## Memory Compiler`, and `## Plugins & extensions` sections are preserved unchanged.
- No production code, test files, or `mkdocs.yml` are modified. `just check` continues to pass.

---

## Approach

No GoF pattern applies. This is SRP in documentation form: the rewritten `index.md` has one responsibility — carry a reader from nothing to a verified round-trip using only PyPI packages. The existing deeper pages (`first-agent.md` for protocol internals, `daemon.md` for daemon ops) remain intact as a "Next steps" table.

**Why rewrite `index.md` rather than add a new page.** The `getting-started/index.md` is the section landing. A reader clicking "Getting Started" in the nav must land on the actionable guide, not a meta-page. Adding a separate `quickstart.md` would also require a `mkdocs.yml` nav edit. Rewriting `index.md` is strictly simpler and the right shape.

**Install stage.** The adopter installs two packages from PyPI: `agent-core` (provides the `agent-core` daemon CLI) and `agent-core-hatchery` (provides the `hatch-being` CLI). No other package is required for the quickstart. Additional endpoint packages (`agent-core-discord`, `agent-core-inbound`, etc.) are mentioned in a short table as optional extras with one-line descriptions. The install works in any uv-managed project (`uv init my-agent && cd my-agent && uv add agent-core agent-core-hatchery`).

**Hatch a being.** `hatch-being` with no flags launches the interactive TUI wizard. The guide describes what the wizard collects (being name, vault root, endpoint name, channel selections) without over-explaining. After hatching: the vault exists at `~/.<being>/Memory/`; daemon config fragments are written to `~/.agent-core/endpoints.d/<being>.yaml`. The daemon does not need to be running at hatch time — the hatchery probe simply reports "unreachable" and the user proceeds to start the daemon.

**Daemon stage.** Three commands in sequence: `agent-core daemon init` (scaffolds `~/.agent-core/agent_core.yaml`), `agent-core daemon install` (installs the daemon runtime into `~/.agent-core/.venv/`), `agent-core daemon start`. Verify with `agent-core daemon status`. The guide omits the mechanism behind `daemon install` (GitHub releases vs. PyPI) — this is a CLI implementation detail that A1-3 owns; the adopter just runs the command.

**Connect stage.** The being connects via two stdio MCP processes: `agent-core-busproxy` (bus tools: send, list pending, ack) and `agent-core-channel` (inline-wake relay). Both ship inside the daemon's isolated venv at `~/.agent-core/.venv/`. The guide shows the `.mcp.json` shape in two tabs (Windows / POSIX) using `pymdownx.tabbed`, with `<you>` and `<your-being>` placeholders. The `.mcp.json` lives at `~/.<being>/.mcp.json`. The Claude Code launch command includes `--dangerously-load-development-channels server:agent-core-channel` (required for the channel relay).

**Verify stage.** Once Claude Code connects to the MCP servers, the reader verifies the round trip in four steps: confirm bus tools appear in Claude Code's tool panel, send a `TextMessage` to `<your-being>` via `bus_send`, list pending with `list_pending`, ack with `handle`. This exercises the full path: publish → SQLite persist → deliver → ack.

**README.md.** The `## Setup` section (lines 14–21) is replaced with a `## Documentation` section: a single line pointing to the getting-started page. The source-dev `uv sync` instruction is not appropriate for adopters; contributors who need it will find it in CONTRIBUTING (A2-4 scope). All other README content is preserved.

---

## Sub-requests (topologically sorted)

1. **Rewrite `docs/getting-started/index.md`** with the five-stage quickstart guide.

   Required sections in order, with mandatory content:

   **Opening paragraph** (no heading): 2–3 sentences. "agent-core is a durable message-bus runtime for AI agent beings. This guide takes you from a fresh machine to a running being with a verified round-trip message — no repository clone needed." Mention that the guide uses the PyPI packages, and that all five stages should take roughly 10 minutes.

   **Prerequisites**: Python 3.12 or later; [uv](https://docs.astral.sh/uv/) installed.

   **Step 1 — Install**: One command block: `uv add agent-core agent-core-hatchery`. Below the block, a short paragraph noting that `agent-core` provides the daemon CLI and `agent-core-hatchery` provides `hatch-being`. Then a compact table of optional endpoint packages (not required for the quickstart):

   | Package | What it adds |
   |---|---|
   | `agent-core-discord` | Discord bot adapter |
   | `agent-core-inbound` | GitHub/webhook inbound events |
   | `agent-core-briefs` | Scheduled morning briefs |
   | `agent-core-voice` | Voice synthesis endpoint (GPU; see package README for extra `--index` flags) |

   **Step 2 — Hatch a being**: Command block: `hatch-being`. Prose description: "The interactive wizard collects a being name (lowercase, ASCII, no spaces — e.g. `myagent`), a vault root (default: your home directory), and optional channel selections (Discord, webcam, etc.). After completing the wizard, your being's vault is at `~/.<your-being>/Memory/` and the daemon has received a config fragment at `~/.agent-core/endpoints.d/<your-being>.yaml`." Include a `!!! note` admonition: "The daemon does not need to be running during hatching. The wizard will report the daemon as unreachable — that is expected here; you will start it in Step 3."

   **Step 3 — Start the daemon**: Three command blocks with prose between them:

   ```bash
   agent-core daemon init
   ```
   "Scaffold `~/.agent-core/agent_core.yaml` (run once per machine)."

   ```bash
   agent-core daemon install
   ```
   "Install the daemon runtime into `~/.agent-core/.venv/`."

   ```bash
   agent-core daemon start
   ```
   "Start the daemon."

   Then: `agent-core daemon status` verification with the expected output block:

   ```
   prod daemon is running (PID: NNNNN)
   running from: ~/.agent-core/.venv/bin/python
   installed at: <ISO timestamp>
   installed sha: <git sha>
   installed version: <X.Y.Z>
   ...
   --- last 20 lines of daemon.log ---
   <log output>
   ```

   *(The `installed at:`, `installed sha:`, and `installed version:` fields appear for a release-installed daemon, followed by the last 20 lines of `daemon.log`. The exact log content varies; its presence confirms the daemon started successfully.)*

   **Step 4 — Connect your Claude Code session**: Intro sentence: "Your being connects to the bus through two stdio MCP servers bundled in the daemon venv." Then: place `.mcp.json` at `~/.<your-being>/.mcp.json` (or at the Claude Code project root if your being is tied to a project).

   Use `=== "Windows"` / `=== "POSIX (macOS / Linux)"` tabbed blocks:

   === "Windows"
   ```json
   {
     "mcpServers": {
       "agent-core": {
         "type": "stdio",
         "command": "C:\\Users\\<you>\\.agent-core\\.venv\\Scripts\\python.exe",
         "args": ["-m", "agent_core_busproxy", "--agent", "<your-being>",
                  "--daemon-url", "http://127.0.0.1:8789"]
       },
       "agent-core-channel": {
         "type": "stdio",
         "command": "C:\\Users\\<you>\\.agent-core\\.venv\\Scripts\\python.exe",
         "args": ["-m", "agent_core_channel", "--agent", "<your-being>",
                  "--daemon-url", "http://127.0.0.1:8789"]
       }
     }
   }
   ```

   === "POSIX (macOS / Linux)"
   ```json
   {
     "mcpServers": {
       "agent-core": {
         "type": "stdio",
         "command": "/home/<you>/.agent-core/.venv/bin/python",
         "args": ["-m", "agent_core_busproxy", "--agent", "<your-being>",
                  "--daemon-url", "http://127.0.0.1:8789"]
       },
       "agent-core-channel": {
         "type": "stdio",
         "command": "/home/<you>/.agent-core/.venv/bin/python",
         "args": ["-m", "agent_core_channel", "--agent", "<your-being>",
                  "--daemon-url", "http://127.0.0.1:8789"]
       }
     }
   }
   ```

   > **macOS users:** replace `/home/<you>` with `/Users/<you>` in both command paths above.

   After the tabs, include a `!!! warning` admonition: "Do not use `\"type\": \"http\"` — that form strands live Claude Code sessions on daemon restart. Always use the stdio form above."

   Then: Claude Code launch command:
   ```bash
   claude --dangerously-load-development-channels server:agent-core-channel
   ```
   With a note: "The `--dangerously-load-development-channels` flag is required for the `agent-core-channel` wake relay. Start Claude Code with this flag each session."

   **Step 5 — Verify the round trip**: "Once Claude Code connects, confirm the following in sequence:"

   Numbered list:
   1. Open Claude Code's MCP tool panel — confirm `agent-core` tools appear (`bus_send`, `list_pending`, `handle`, `list_endpoints`, …).
   2. Send a message to yourself: use `bus_send` with `to="<your-being>"`, `kind="TextMessage"`, `payload={"text": "hello"}`.
   3. List pending messages: use `list_pending` — your message should appear.
   4. Acknowledge the message: use `handle` with the envelope ID from the previous step.

   Close with: "A successful round trip confirms that publish → SQLite persist → deliver → ack is working end-to-end."

   **Next steps**: compact table:

   | Page | What it covers |
   |---|---|
   | [Your first endpoint](first-agent.md) | The `Endpoint` protocol; writing and running a custom endpoint |
   | [Running the daemon](daemon.md) | Prod/source/test instances, upgrades, troubleshooting |
   | [Concepts](../concepts/index.md) | Bus, envelopes, endpoints, the full runtime model |

2. **Update `README.md`**: replace the `## Setup` section (lines 14–21 of the current file) with a `## Documentation` section.

   Old content (lines 14–21):
   ```markdown
   ## Setup

   ```bash
   uv sync
   ```

   - **Running the bus daemon:** see [docs/setup/daemon.md](docs/setup/daemon.md) for the one-time setup and the `daemon refresh` daily flow.
   ```

   Replacement:
   ```markdown
   ## Documentation

   → [Getting started](https://jeffrichley.github.io/agent_core/getting-started/) — install from PyPI, hatch a being, run the daemon, connect your agent.
   ```

   All other content (badges, one-line description, `## Memory Compiler`, `## Plugins & extensions`) is preserved verbatim.

---

## File-level changes

| File | Change |
|---|---|
| `docs/getting-started/index.md` | **Rewrite** — complete end-to-end PyPI-first getting-started guide with five numbered stages |
| `README.md` | **Modify** — replace `## Setup` / `uv sync` block with `## Documentation` link to docs site |

---

## Alternatives considered

1. **Add a new page `docs/getting-started/quickstart.md`** and leave `index.md` as a meta-landing. Ruled out: the section landing IS the right place for the primary guide; a separate quickstart page would require a `mkdocs.yml` nav change and a redirection from `index.md`. Rewriting `index.md` is strictly simpler.

2. **Keep the source-dev flow in `index.md` and add a separate "Adopter install" section** within the same page. Ruled out: mixing `uv sync` (source-dev) and `uv add` (PyPI adopter) on one page confuses the two audiences. The source-dev flow belongs in CONTRIBUTING (A2-4 scope); the adopter entry is clean `uv add`.

3. **Use Discord as the "connect an endpoint" example** instead of Claude Code MCP. Ruled out: Discord requires a bot token (external credential), making the quickstart non-self-contained. Claude Code is the primary surface agent-core was built for; the MCP sidecar path is the zero-external-credential path for an existing Claude Code user.

4. **Expand `README.md` to contain the full quickstart** rather than linking out. Ruled out: the README would duplicate docs-site content, diverge over time, and become the longer-form source — the opposite of what the issue requests ("replaces the 19-line README as the primary entry" means the README shrinks, not grows).

---

## Open questions

1. After A1-3 lands, does `agent-core daemon install` pull from PyPI instead of GitHub Release artifacts, or does it continue to use GitHub releases with PyPI available in parallel? The spec conservatively omits the install mechanism from the guide ("installs the daemon runtime into `~/.agent-core/.venv/`") — this is safe either way.

2. Does `hatch-being` create `~/.agent-core/` if it doesn't exist, or does `daemon init` need to run first? The spec orders `daemon init` before `hatch-being` because `daemon init` scaffolds `agent_core.yaml` and the ordering is recommended in existing docs. If the hatchery can be run first without error, the ordering still works (daemon init is idempotent).

---

## Out of scope

- Documenting Discord, inbound, voice, or webcam endpoints in detail (listed as optional extras only).
- Source-dev / contributor workflow (`uv sync`, `just test-fast`, etc.) — belongs in CONTRIBUTING (A2-4).
- Daemon operator reference (troubleshooting, refresh, rollback, Windows service) — stays in `docs/getting-started/daemon.md` and `docs/setup/daemon.md`.
- A2-2 (architecture overview), A2-3 (de-Wren-ify), A2-4 (CONTRIBUTING), A2-5 (per-package READMEs) — separate tickets.
- Changes to `mkdocs.yml`, `docs/getting-started/first-agent.md`, `docs/getting-started/daemon.md`, or any Python source or test files.
