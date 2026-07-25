# Spec: per-package READMEs for all packages lacking one (issue #506)

## Goal

Write a README (purpose, install, minimal usage) for each of the eight `packages/` directories that currently lack one — `core`, `agent-core-busproxy`, `agent-core-briefs`, `agent-core-discord`, `agent-core-voice`, `agent-core-webcam`, `credentials`, and `notify` — and add a CI guard that prevents future packages from shipping without one. See issue #506 and parent spec `docs/superpowers/specs/2026-07-16-theme-f-track-a-pypi-launch-design.md` §5 (A2-5).

## Acceptance criteria

- `packages/core/README.md` exists with purpose, install, and minimal usage.
- `packages/agent-core-busproxy/README.md` exists with purpose, install, and minimal usage.
- `packages/agent-core-briefs/README.md` exists with purpose, install, and minimal usage.
- `packages/agent-core-discord/README.md` exists with purpose, install, and minimal usage.
- `packages/agent-core-voice/README.md` exists with purpose, install, and minimal usage.
- `packages/agent-core-webcam/README.md` exists with purpose, install, and minimal usage.
- `packages/credentials/README.md` exists with purpose, install, and minimal usage.
- `packages/notify/README.md` exists with purpose, install, and minimal usage.
- `just readme-check` exits 0 (all 12 packages have READMEs) and exits non-zero if any are missing.
- CI (`ci.yml`) runs `just readme-check` on every PR and push, failing the build if any package lacks a README.
- The four packages that already have READMEs (`agent-core-channel`, `agent-core-hatchery`, `agent-core-inbound`, `agent-core-qa`) are not modified.

## Approach

No GoF pattern fits here — this is straightforward documentation authoring plus one CI guard.

**Style model:** Follow the pattern of `packages/agent-core-hatchery/README.md` (the most concise existing example): an H1 with the package name, a 2–3 sentence purpose paragraph, an `## Install` section, and a `## Usage` section with the minimal shell commands or config snippet needed to get started. The inbound README's full operator runbook style is too heavy for this ticket — the issue asks for "purpose, install, minimal usage," not exhaustive runbooks.

**Install wording:** The A1 PyPI publish has not landed yet. Use the install wording `uv add <dist-name>` (matching the planned PyPI name from the parent spec) with a note that, until PyPI publish lands, `uv sync` at the repo root installs the package automatically. This avoids workspace-only wording that will need rewriting post-A1.

**CI guard:** Add a `readme-check` recipe to `justfile` that runs a Python one-liner globs `packages/*/` and fails if any package directory lacks a `README.md`. Wire it as a new step in the existing `check` job in `.github/workflows/ci.yml` (Linux OS only — the check is OS-agnostic and one job is enough). This matches the shape of the existing `contracts` step (fast, no full install required, single-purpose gate).

**Package details grounded from source:**

- `packages/core/` — dist name `agent-core-bus`. The main framework: message bus daemon (`agent-core-daemon`), bus routing, the `agent-core` CLI (hooks, bus, bus-log, daemon, vault, venv subcommands), pluggable hook tool pipeline (`agent_core.yaml`), and shared models.
- `packages/agent-core-busproxy/` — dist name `agent-core-busproxy`. A stdio MCP proxy that exposes the daemon's per-agent HTTP MCP endpoint to Claude Code. Opens a fresh backend session per tool call so daemon bounces never strand the session. Configured as a Claude Code stdio MCP server.
- `packages/agent-core-briefs/` — dist name `agent-core-briefs`. Structured-composition framework for assembling briefs by gathering context from pluggable fetchers (CLI, filesystem, time), rendering through Markdown playbooks, and submitting to the bus. CLI subcommands: `agent-core briefs compose`, `agent-core briefs fetchers list/test`.
- `packages/agent-core-discord/` — dist name `agent-core-discord`. Discord bot adapter; one bot per being (1:1). Bridges Discord messages to bus envelopes; supports text, attachments, urgency sigils. Optional `[voice]` extra adds Whisper transcription.
- `packages/agent-core-voice/` — dist name `agent-core-voice`. Voice synthesis endpoint using Qwen3-TTS with ICL voice cloning. Requires `[cpu]` or `[cu130]` extra (mutually exclusive); CUDA variant needs the PyTorch GPU index.
- `packages/agent-core-webcam/` — dist name `agent-core-webcam`. Webcam capture endpoint using OpenCV; exposes on-demand frame capture as an MCP tool to agents.
- `packages/credentials/` — dist name `agent-core-credentials`. KeePass-backed credential vault at `~/.agent-core/credentials.kdbx`. CLI: `agent-core-creds`. Python API: `get_credential`, `set_credential`, `list_credentials`, `delete_credential`.
- `packages/notify/` — dist name `agent-core-notify`. Desktop notification MCP server; exposes `send_notification`, `ask_user`, `notify_with_buttons`, `get_reply`, `clear_notifications` tools. Registered in `.mcp.json`; entrypoint is `agent-core-notify`.

## Sub-requests (topologically sorted)

1. Create `packages/core/README.md` — purpose (bus daemon + CLI framework), install (`uv add agent-core-bus`), usage (`agent-core daemon run`, `agent-core hooks run SessionStart`).
2. Create `packages/agent-core-busproxy/README.md` — purpose (stdio MCP proxy bridging Claude Code to daemon), install (`uv add agent-core-busproxy`), usage (`.mcp.json` snippet + CLI invocation).
3. Create `packages/agent-core-briefs/README.md` — purpose (brief composition framework), install (`uv add agent-core-briefs`), usage (`agent-core briefs compose` and `fetchers list/test`).
4. Create `packages/agent-core-discord/README.md` — purpose (Discord bot adapter), install (`uv add agent-core-discord`; voice extra), usage (`agent_core.yaml` endpoint config snippet).
5. Create `packages/agent-core-voice/README.md` — purpose (Qwen3-TTS voice synthesis endpoint), install (cpu/cu130 extras, GPU index note), usage (`agent_core.yaml` endpoint config snippet).
6. Create `packages/agent-core-webcam/README.md` — purpose (webcam capture endpoint), install (`uv add agent-core-webcam`), usage (`agent_core.yaml` endpoint config snippet).
7. Create `packages/credentials/README.md` — purpose (KeePass credential vault), install (`uv add agent-core-credentials`), usage (CLI commands + Python API snippet).
8. Create `packages/notify/README.md` — purpose (desktop notification MCP server), install (`uv add agent-core-notify`), usage (`.mcp.json` registration snippet).
9. Add `readme-check` recipe to `justfile` — Python one-liner that iterates `packages/*/`, collects dirs missing `README.md`, prints them and exits 1 if any found.
10. Add a `readme-check` step to the Linux matrix job in `.github/workflows/ci.yml` that runs `just readme-check` (after the `uv sync` step so `uv` is available for `uv run`).

## File-level changes

| File | Action | What changes |
|---|---|---|
| `packages/core/README.md` | **Create** | Purpose, install (`agent-core-bus`), CLI usage |
| `packages/agent-core-busproxy/README.md` | **Create** | Purpose, install, `.mcp.json` config + CLI invocation |
| `packages/agent-core-briefs/README.md` | **Create** | Purpose, install, `briefs compose` / `fetchers` usage |
| `packages/agent-core-discord/README.md` | **Create** | Purpose, install (including `[voice]` extra), `agent_core.yaml` config |
| `packages/agent-core-voice/README.md` | **Create** | Purpose, install (cpu/cu130 extras + GPU index), `agent_core.yaml` config |
| `packages/agent-core-webcam/README.md` | **Create** | Purpose, install, `agent_core.yaml` config |
| `packages/credentials/README.md` | **Create** | Purpose, install, CLI + Python API usage |
| `packages/notify/README.md` | **Create** | Purpose, install, `.mcp.json` registration |
| `justfile` | **Modify** | Add `readme-check` recipe |
| `.github/workflows/ci.yml` | **Modify** | Add `readme-check` step to the Linux `check` job |

## Alternatives considered

1. **No CI guard, just write the READMEs** — the issue's "done when" criterion explicitly requires "a check confirms none is missing." Writing the READMEs without the guard satisfies only half the acceptance criteria and leaves the completeness unenforced on future additions.

2. **Express the check as a pytest test** — a pytest test checking file existence would be novel (pytest is for code behavior in this repo) and would drag the README check into the coverage accounting. The `justfile` + CI step pattern matches how the existing `contracts` gate works and imposes no test-framework overhead.

3. **Wire `readme-check` into the existing `just check` command (making it mandatory for every local `just check` run)** — would block `just check` on a branch that's still mid-stream adding READMEs. Keeping it as a separate step in CI only (not added to the default `check` recipe) is less disruptive to the local dev loop.

## Open questions

None. The eight packages that need READMEs are unambiguous (verified by globbing `packages/*/README.md`), the content for each is grounded in their `pyproject.toml` and source `__init__.py` / CLI modules, and the CI guard pattern matches the existing `contracts` step.

## Out of scope

- Writing the "add an endpoint" reference or documenting bus config keys outside the sample file (those are the "b" sub-tickets under A2-5 and explicitly not this ticket).
- Writing `CONTRIBUTING.md` or the "hatch your own being" walkthrough (A2-4).
- Modifying any of the four existing READMEs (`agent-core-channel`, `agent-core-hatchery`, `agent-core-inbound`, `agent-core-qa`).
- Any A1 packaging work (workspace→PyPI dependency pinning, release CI, version train).
- Renaming the `agent-core-bus` dist name to `agent-core` (called out in the parent spec as future A1 work; the README should use the current pyproject name).
