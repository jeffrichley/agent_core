# Spec: architecture overview — one doc + diagram (issue #395)

## Goal

Create `docs/concepts/architecture.md`, a single self-contained page that explains the
bus + daemon + sidecar + pluggable-endpoint runtime model and defines the six core
nouns (bus, daemon, endpoint, being, envelope, sidecar) so a reader new to agent-core
can explain the runtime model after reading it. Wire the page into the MkDocs nav and
update the concepts index to reference it. See issue #395; part of Theme F Track A
(#262, #269), spec PR #389.

---

## Acceptance criteria

- `docs/concepts/architecture.md` exists and the `mkdocs build --strict` build in
  `uv run --group docs mkdocs build --strict` passes with zero warnings after it is
  added.
- The document defines all six core nouns in explicit prose: **bus**, **daemon**,
  **endpoint**, **being**, **envelope**, **sidecar**. Each definition must stand on its
  own — no forward references to undefined terms.
- The document contains at least one ASCII-art diagram showing the runtime topology:
  daemon containing the bus and endpoints, beings connecting via sidecars, the HTTP
  boundary between sidecars and the daemon.
- The document has a "delivery path" section that traces an envelope's journey from
  `publish()` through the SQLite mailbox to `endpoint.deliver()` and `ack()`.
- `mkdocs.yml` nav is updated to include `Architecture: concepts/architecture.md` under
  the `Concepts:` section (first entry after `concepts/index.md`).
- `docs/concepts/index.md` is updated to include "Architecture" in its table and to
  open with a cross-reference to the new architecture page.
- No Mermaid or other diagram dependency is added — ASCII art only (consistent with
  `docs/index.md` and `docs/concepts/bus.md`).
- No production code, tests, or non-doc files are modified.
- `just check` continues to pass (docs changes do not affect Python coverage).

---

## Approach

No GoF pattern applies. This is straightforward documentation authoring: one new
markdown file with an ASCII diagram, and two small edits to wire it into the nav. The
approach follows SRP — the new page has one responsibility: explain the runtime model at
the system level, leaving per-component depth to the existing concept pages it links to.

**What the document must cover that no existing page covers.** The four existing concept
pages (`bus.md`, `envelopes.md`, `endpoints.md`, `extensions.md`) each explain one
component in isolation. None of them explain:

- The **daemon** as a distinct runtime concept (it appears only in the getting-started
  section as operational procedure).
- The **sidecar** connection model — how beings reach the bus via two stdio MCP proxy
  processes (`agent-core-busproxy` and `agent-core-channel`) rather than connecting
  directly.
- The **being** noun — an AI agent identity with its own home directory, vault, and
  bus presence. It is used throughout the codebase and docs (`world-class-eval-2026-07-13.md`,
  `docs/setup/daemon.md`, hatchery README) but is never defined in the adopter docs.
- How all the pieces **compose** at runtime: which process runs what, what the HTTP
  boundary is between beings and the daemon, how an envelope crosses that boundary.

The architecture page fills exactly this gap. It is the "zoom out" view that makes
the individual concept pages readable as deeper dives rather than first introductions.

**Style and tone.** Follow the prose style of `docs/concepts/bus.md` exactly: short
declarative sentences, no marketing phrasing, concrete file and module references where
they anchor a claim, admonitions for important invariants, a final "Read more" table
linking to related pages. The ASCII diagram style follows `docs/index.md`'s 60-second
picture (monospace box-drawing with `▶` arrows).

**Nav placement.** `concepts/architecture.md` is listed first under `Concepts:` in
`mkdocs.yml`, after `concepts/index.md`. The `concepts/index.md` ToC table gains an
"Architecture" row pointing to the new page, and its opening paragraph is updated to
direct readers there first.

---

## Sub-requests (topologically sorted)

1. **Create `docs/concepts/architecture.md`.**

   The file must contain the following sections (exact section titles required for MkDocs
   anchor stability; Worker may adjust wording inside sections):

   **`# Architecture`** — one-sentence intro: "This page explains how agent-core's
   pieces fit together at runtime; the individual concept pages go deeper on each."

   **`## The six core nouns`** — a definition list or compact table defining: **being**,
   **daemon**, **bus**, **endpoint**, **sidecar**, **envelope**. Each definition is one
   to two sentences, self-contained. Specifically:
   - **Being** — an AI agent identity (a Claude Code session, or any process that talks
     to the bus). Each being has a home directory (`~/.<being>/`), a credentials vault,
     and one or more registered endpoints on the bus.
   - **Daemon** — the single long-running host process started with
     `agent-core daemon start`. It owns the bus, starts and supervises all configured
     endpoints, and exposes an HTTP API the sidecars connect to.
   - **Bus** — the in-process async message router inside the daemon. It reads
     `agent_core.yaml`, routes envelopes between registered endpoints, persists every
     envelope to a SQLite mailbox before delivery, and retries or dead-letters on
     failure.
   - **Endpoint** — an addressable participant registered on the bus. An endpoint
     implements three async methods (`start`, `deliver`, `stop`). The daemon hosts many:
     one `ClaudeCodeMCPEndpoint` per being, a `DiscordEndpoint`, a `SchedulerEndpoint`,
     etc. Third-party packages contribute additional endpoint types via Python entry
     points.
   - **Sidecar** — a lightweight stdio MCP server process each being runs alongside its
     AI session. Two sidecars per being: `agent-core-busproxy` (exposes bus tools —
     publish, inbox, ack) and `agent-core-channel` (inline-wake relay — wakes the agent
     when high-urgency mail arrives). Each sidecar proxies calls to the daemon over HTTP;
     each tool call opens a fresh connection so daemon restarts do not strand the agent.
   - **Envelope** — the universal wire format for all bus messages. Every message
     carries: `id`, `from_` (stamped by the bus), `to`, `kind`, `payload`, `urgency`
     (`green`/`yellow`/`red`), optional `expires_at`, and `correlation_id`.

   **`## Runtime topology`** — ASCII diagram followed by a brief narrative.

   The ASCII diagram must show:
   - A box labelled "daemon (one process)" containing the bus (labelled "Bus — SQLite
     mailbox") and at least two example endpoints (e.g. `endpoint: wren (ClaudeCodeMCP)`
     and `endpoint: discord (DiscordEndpoint)`).
   - Outside the daemon box: two beings (e.g. "Being: wren" and "Being: discord service").
   - Each being shows its two sidecars (`busproxy` and `channel`).
   - Arrows from each being's sidecars → daemon over HTTP (`http://127.0.0.1:8789`).
   - An arrow from the Discord endpoint → the Discord API (showing an endpoint can also
     reach external services).

   An acceptable diagram shape (Worker may refine layout):

   ```
                   ┌─────────────────────────────────────────────────┐
                   │              daemon  (one process)               │
                   │                                                  │
                   │   ┌──────────────────────────────────────────┐   │
                   │   │      Bus  (SQLite mailbox)               │   │
                   │   │                                          │   │
                   │   │  ┌───────────────┐  ┌────────────────┐  │   │
                   │   │  │ endpoint:wren  │  │ endpoint:      │  │   │
                   │   │  │ ClaudeCodeMCP  │  │ discord        │  │   │
                   │   │  └───────┬───────┘  └──────┬─────────┘  │   │
                   │   └──────────│──────────────────│────────────┘   │
                   │              │HTTP              │ Discord API     │
                   └──────────────│──────────────────│────────────────┘
                                  │                  └──────────▶ Discord
                                  │
                ┌─────────────────┘
                ▼
          Being: wren
      ┌─────────────────────┐
      │ busproxy  (stdio MCP)│──▶ http://127.0.0.1:8789
      │ channel   (stdio MCP)│──▶ http://127.0.0.1:8789
      └──────────┬──────────┘
                 │ MCP tools
                 ▼
         Claude Code session
   ```

   The narrative (after the diagram, ~3–4 sentences) explains: the daemon is the single
   process; beings do not embed the bus; sidecars are the indirection layer that lets
   the daemon restart without stranding agent sessions; each tool call opens a fresh
   HTTP connection.

   **`## The delivery path`** — a short section (can use the bus.md delivery-lifecycle
   ASCII or a prose-only variant) showing the path an envelope takes from
   `handle.publish()` → `pre_publish hooks` → `SQLite insert` → `endpoint.deliver()` →
   `bus.ack()`. Three to five sentences maximum. Cross-reference `concepts/bus.md` for
   the full details (redelivery, TTL, dead-letter).

   **`## Pluggable endpoints`** — two to three sentences explaining that endpoint types
   are not hardcoded: third-party packages register new types via Python entry points
   (`[project.entry-points."agent_core"]`), and the daemon discovers them at startup.
   Cross-reference `concepts/extensions.md`.

   **`## Read more`** — a compact table:

   | Topic | Page |
   |---|---|
   | Bus delivery lifecycle, hooks, config | [The bus](bus.md) |
   | Envelope fields and built-in kinds | [Envelopes](envelopes.md) |
   | Endpoint protocol and supervision | [Endpoints](endpoints.md) |
   | Plugin hooks and extension types | [Extensions](extensions.md) |
   | Running the daemon | [Running the daemon](../getting-started/daemon.md) |

2. **Update `mkdocs.yml` nav.**

   In the `Concepts:` block, insert `Architecture: concepts/architecture.md` as the
   first entry after `concepts/index.md`:

   ```yaml
   - Concepts:
       - concepts/index.md
       - Architecture: concepts/architecture.md
       - The bus: concepts/bus.md
       - Envelopes: concepts/envelopes.md
       - Endpoints: concepts/endpoints.md
       - Extensions: concepts/extensions.md
   ```

3. **Update `docs/concepts/index.md`.**

   Add "Architecture" as the first row in the table (before Bus):

   ```markdown
   | [Architecture](architecture.md) | How the daemon, bus, endpoints, beings, and sidecars compose |
   ```

   Add one sentence at the top of the "four concepts build on each other" paragraph
   directing readers to the architecture page first: "New to agent-core? Read the
   [Architecture](architecture.md) page first for a full system diagram and noun
   definitions."

---

## File-level changes

| File | Change |
|---|---|
| `docs/concepts/architecture.md` | **Create** — architecture overview with ASCII diagram, noun definitions, delivery path, pluggable-endpoint section, read-more table |
| `mkdocs.yml` | **Modify** — add `Architecture: concepts/architecture.md` to Concepts nav block, first after `concepts/index.md` |
| `docs/concepts/index.md` | **Modify** — add Architecture row to table; add cross-reference sentence at the top of the body paragraph |

---

## Alternatives considered

1. **Add the architecture content to `docs/index.md` (home page) by expanding the
   "60-second picture".** The home page already has a brief ASCII art block. Ruled out:
   the home page's job is orientation + navigation, not depth. A full noun-glossary +
   sidecar explanation would overwhelm the landing page. The content belongs in Concepts
   where a reader who wants the mental model goes deliberately.

2. **Add `docs/architecture.md` as a new top-level nav section (not under Concepts).**
   Would create a parallel "Architecture" entry at the top level alongside Getting
   Started, Concepts, Guides. Ruled out: the MkDocs system design (`2026-07-13-docs-system-design.md`,
   approved by Jeff) defined Concepts as the mental-model section. Adding a parallel
   top-level section fragments the nav. The page belongs inside Concepts.

3. **Expand `docs/concepts/index.md` in place instead of creating a new page.** `concepts/index.md`
   is a four-line intro + a four-row table (one row per concept page). It serves as a
   section ToC. Bloating it with a full diagram, noun glossary, and delivery-path
   narrative would violate its ToC role. A dedicated page is the right shape.

4. **Use a Mermaid diagram instead of ASCII art.** `pymdownx.superfences` is already
   present in `mkdocs.yml`, but Mermaid requires a `custom_fences` config block plus a
   JavaScript include in the Material theme's `extra_javascript`. Both are absent and
   would require new dependencies outside this ticket's scope. ASCII art is consistent
   with `docs/index.md` and `docs/concepts/bus.md` and requires zero new config. Ruled
   out.

---

## Open questions

None. The issue is unambiguous ("one doc + diagram", "bus + daemon + sidecar +
pluggable-endpoint model", "core nouns: bus, daemon, endpoint, being, envelope"), the
repo conventions are clear (ASCII art, admonition blocks, table of links at the end),
and all referenced file paths have been verified in the worktree.

---

## Out of scope

- Defining "being" anywhere except the new architecture page — other pages use the term
  without defining it and may be updated in later tickets (A2.3 de-Wren-ify, A2.4
  hatch-your-own-being).
- Adding a Mermaid diagram capability to MkDocs (`extra_javascript`, `custom_fences`).
- Creating a standalone "hatchery" concept page explaining how beings are bootstrapped
  (that is A2.4 scope).
- Any changes to production code, tests, or CI configuration.
- Adding per-package READMEs or a CONTRIBUTING guide (A2.5 scope).
- Getting-started or install docs (A2.1 scope, depends on A1.3 PyPI publish).
