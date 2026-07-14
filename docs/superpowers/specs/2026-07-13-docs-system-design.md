# agent-core Documentation System — Design

**Date:** 2026-07-13
**Status:** Approved (Jeff, brainstorm) — build authorized
**Epic:** world-class-eval #262 (Theme G — Documentation)
**Author:** Wren 🪶

## Goal

Stand up a real, buildable, deployable documentation site for agent-core that
other people — and their agents — can read to adopt the framework, and that we
keep building on. This is the first published surface of agent-core as a
product, not just a repo.

## Audiences (priority order, from Jeff)

1. **B — Human developers adopting agent-core** (primary). Someone who found
   the repo and wants to build an agent on it. They need: what it is, why,
   install, a first working agent, concept explanations, task-oriented guides,
   and a complete API reference.
2. **A — Adopter agents** (their AI coding agents pulling from the docs). Served
   by machine-consumable `llms.txt` / `llms-full.txt` artifacts generated from
   the same source, so an agent can ingest the whole surface in one fetch.
3. **C — agent-core beings** (Wren, Pepper, future hatchlings). Served by the
   same content written honestly — the docs name that agent beings build and
   run on this, rather than pretending a purely-human authorship.

One source of truth serves all three: prose + API reference for B, the llms.txt
export for A, honest framing for C. We do **not** fork content per audience.

## Tech stack

- **MkDocs** + **Material for MkDocs** theme — the site.
- **mkdocstrings[python]** — API reference autodoc from docstrings (Google
  style; the repo already enforces Google docstrings via ruff `D`).
- **mkdocs-llmstxt** (pawamoy) — generates `llms.txt` (index) and
  `llms-full.txt` (concatenated body) at build time for audience A.
- **GitHub Pages** deploy via a CI workflow (`mkdocs gh-deploy` on push to
  `main`).
- Tooling installed via a new `docs` **dependency-group** in the root
  `pyproject.toml` (kept out of the `dev` group so CI test jobs stay lean).

Rationale: all three plugins are the same pawamoy/Material ecosystem, so config
and versioning stay coherent. Material is the de-facto standard for Python
project docs and gives search, nav, and responsive layout for free.

## Repository & layout

**Single repo** (Jeff chose "Single"): the docs live inside `jeffrichley/agent_core`,
not a separate docs repo. Docs version with the code and adopters get them in
the same clone.

`docs_dir: docs/` (MkDocs convention). The existing `docs/` tree already holds
**internal** engineering artifacts (superpowers specs/plans/tickets, cutover,
migrations, requirements, hatchery, ROADMAP/BACKLOG). To keep the published
site clean without moving that history:

- Author public pages under curated subdirs: `docs/index.md`,
  `docs/getting-started/`, `docs/concepts/`, `docs/guides/`, `docs/reference/`.
- Use MkDocs `exclude_docs` (gitignore-style) to drop the internal subtrees from
  the build, and `validation.nav.not_in_nav: info` so stray internal `.md` files
  never fail a strict build.
- `strict: true` stays on for real problems (broken internal links, bad
  references) — that is the gate that keeps the docs honest.

## Information architecture (nav)

- **Home** (`index.md`) — what agent-core is, who it's for (incl. the honest
  "built and run by agent beings" framing), the badges, a 60-second orientation.
- **Getting Started** — install (`uv`), your first agent/endpoint, running the
  bus daemon (link/adapt existing `docs/setup/daemon.md`).
- **Concepts** — the mental model: the bus, envelopes & kinds, endpoints,
  extensions/hookspecs, persistence, the daemon. Explanations, not API dumps.
- **Guides** — task-oriented: add an endpoint, send/consume envelopes, write an
  extension, wire an inbound connector, deploy the daemon.
- **API Reference** — mkdocstrings-generated per public package (`agent_core`
  first; the other `agent-core-*` packages as they stabilize).
- **About** — project status, links, license, contribution pointer.

## Acceptance criteria

1. `uv run --group docs mkdocs build --strict` succeeds with **zero** warnings.
2. The API reference renders real symbols from `agent_core` (bus, envelope,
   persistence) via mkdocstrings — not stubs.
3. `llms.txt` and `llms-full.txt` are produced in the built `site/` and contain
   the concept + guide content.
4. A CI workflow deploys the site to GitHub Pages on push to `main`, and does
   **not** run on PRs from forks (no secret exposure).
5. The `docs` dependency-group installs cleanly and is **not** pulled into the
   default test job (CI `check` stays as-is).
6. Home + Getting Started + at least the core Concepts pages are real content a
   new adopter could follow — no "TODO" placeholders on published pages.
7. Nothing in the internal `docs/` planning tree leaks into the published nav.

## Non-goals (YAGNI)

- Versioned docs (mike) — single "latest" site for now.
- Per-package API sites — one unified reference, `agent_core` first.
- Custom theme/branding beyond Material defaults + the agent-beings note.
- Auto-generated changelog pages — link the existing `CHANGELOG.md`.

## Build path (Wren's call, per Jeff's delegation)

Built **directly in-session** on branch `docs/mkdocs-site`, with content
authoring fanned out to parallel subagents where it helps, and a final
whole-diff review subagent before merge. Not routed through foreman: with
`max_in_flight = 1`, foreman is busy draining Theme A supervision, and a
greenfield docs site is a poor fit for the spec→review→impl loop and would miss
the morning deadline serialized behind Theme A.
