# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). Versions are VCS-derived (`uv-dynamic-versioning`); releases are cut with `just release <X.Y.Z>` (see `docs/setup/releases.md`).

This project uses [*towncrier*](https://towncrier.readthedocs.io/); unreleased changes live in per-package `changelog.d/<package>/` fragments.

<!-- towncrier release notes start -->

## [0.3.0](https://github.com/jeffrichley/agent_core/compare/v0.2.0...v0.3.0) (2026-05-24)


### Features

* **#114:** unified discord_send envelope shape + strict-mode validator ([#119](https://github.com/jeffrichley/agent_core/issues/119)) ([a6d17fc](https://github.com/jeffrichley/agent_core/commit/a6d17fcd16b7831d6a4c637785b9f78dd15070df))
* **daemon:** dev/prod daemon instance-parameterization (Phase 3) ([#108](https://github.com/jeffrichley/agent_core/issues/108)) ([16c4cc6](https://github.com/jeffrichley/agent_core/commit/16c4cc69d719d0fb69c4d1ad080c47c10a2082eb))
* **daemon:** three-instance model — prod / source / test (Phase 3.5) ([#120](https://github.com/jeffrichley/agent_core/issues/120)) ([5301505](https://github.com/jeffrichley/agent_core/commit/5301505e312fc905637932574ffc89b908336222))
* **qa:** agent-core-qa — release-validation scenario runner ([#122](https://github.com/jeffrichley/agent_core/issues/122)) ([cc1d099](https://github.com/jeffrichley/agent_core/commit/cc1d09923c32ad6f46105c0ca27df64b413ac620))
* **release:** phase 2.6 — end-to-end install validation (closes 3 release-pipeline bugs) ([#121](https://github.com/jeffrichley/agent_core/issues/121)) ([ce8ee59](https://github.com/jeffrichley/agent_core/commit/ce8ee5971acb46d64944f3df3149249214dabcf8))

## [0.2.0](https://github.com/jeffrichley/agent_core/compare/v0.1.0...v0.2.0) (2026-05-20)


### Features

* **release:** Phase 2.5 — release-artifact deploy + bug cleanup ([#102](https://github.com/jeffrichley/agent_core/issues/102)) ([079e493](https://github.com/jeffrichley/agent_core/commit/079e4930dc1bcab620dcadf226273dd1bc1be0f7))

## [0.1.0](https://github.com/jeffrichley/agent_core/tree/0.1.0) - 2026-05-20

No significant changes.


### core

#### Added

- - `ClaudeCodeMCPEndpoint` adapter so Claude Code instances can connect
    to the bus over Streamable HTTP. Path-based identity at `/mcp/<name>`.
  - Shared HTTP host (Starlette + Uvicorn) wired into the bus runner;
    mounts every registered `MCPHostable` endpoint automatically.
  - `agent-core daemon start/stop/status` — PID-managed lifecycle for the
    long-running bus daemon. Spawns `agent-core bus run` detached.

  ([#6](https://github.com/jeffrichley/agent_core/issues/6))
- - `SchedulerEndpoint` adapter — fires scheduled prompts as bus envelopes.
    Static `jobs.yaml` seeds at boot plus dynamic management via
    `ToolInvocation` envelopes addressed to `to=scheduler`. Six tools:
    `create_job`, `update_job`, `delete_job`, `list_jobs`, `pause_job`,
    `resume_job`. Replies via `Acknowledgment` envelopes back to caller. ([#7](https://github.com/jeffrichley/agent_core/issues/7))
- `ClaudeCodeMCPEndpoint.start()` now emits MCP
  `notifications/tools/list_changed` once after all deferred tool mounters
  drain (issue #37). Connected MCP clients that respect the protocol-level
  notification — including Claude Code, per Pepper's confirmation on
  2026-05-09 — re-run `tools/list` on receipt and pick up the new tool
  surface without an `/exit + relaunch`. Static registries (no deferred
  mounters) skip the notification. Sessions that raise during the push
  are unregistered, mirroring the existing channel-push pattern. ([#37](https://github.com/jeffrichley/agent_core/issues/37))
- Two new MCP tools on `ClaudeCodeMCPEndpoint` cut the per-round-trip floor
  to 2 calls (was 3 after #54, was 5 before #54). Both are additive —
  existing `list_pending`, `handle`, and `send` remain available unchanged.

  - `consume(batch_window_seconds=30, auto_ack=True, max_items=None)` —
    same return shape as `list_pending`; with `auto_ack=True` (default)
    every envelope id in the returned items is ack'd before the call
    returns.
  - `reply(in_reply_to, payload, urgency='green', metadata=None)` —
    publishes a `TextMessage` outbound and acks the inbound atomically.
    Routing (`to`, `correlation_id`) inherits from the inbound; metadata
    is shallow-merged with the override winning per top-level key.
    Urgency defaults to `'green'`; pass `'auto'` to inherit from the
    inbound. Looks up the inbound in the pickup queue first, then in a
    new recent-inbounds cache so it works after `consume(auto_ack=True)`.

  ([#67](https://github.com/jeffrichley/agent_core/issues/67))
- Inline-content wake notifications via relay-side prefetch (issue #70).
  The `agent-core-channel` relay now calls `consume(auto_ack=False)` on
  every wake, applies per-kind rendering with HTML-safe encoding, and
  emits a richer `notifications/claude/channel` notification carrying
  the inbound envelope content directly. Drops the per-Discord-round-
  trip floor from 2 tool calls to 1.

  New bus tool: `peek(envelope_id)` returns one specific envelope from
  the pickup queue without acking — used to hydrate truncated previews
  into full payload, and useful for power-use manual triage.

  New CLI command: `agent-core wake-stats <agent>` joins the relay's
  wake-audit JSONL with the bus's mcp-audit JSONL to compute per-wake
  outcomes (replied / handled / engaged-with-fetch / side-action /
  ignored). The 30% no-engagement rate is the rollout-gate threshold —
  above it, switch the relay to `--inline-mode=preview` instead of
  `full` (Alt A from the design discussion).

  Configuration via layered precedence (CLI > env > YAML > defaults).
  YAML schema: `endpoints[name=<agent>].params.channel_relay` with
  keys `inline_mode`, `max_envelopes`, `max_bytes`, `per_envelope_bytes`.
  Env vars: `AGENT_CORE_CHANNEL_*`. Defaults: full mode, 5 envelopes,
  8KB total, 4KB per-envelope.

  Backward compatible: existing endpoints without a `channel_relay`
  YAML block use defaults. Bus protocol unchanged — all new behavior
  lives in the relay (Alt B / harness-side prefetch). ([#70](https://github.com/jeffrichley/agent_core/issues/70))

#### Changed

- Restructured the repo into a `uv` workspace. `agent-core` is now a member at `packages/core/`; subsequent integrations will land as sibling packages. ([#3](https://github.com/jeffrichley/agent_core/issues/3))
- Notify subsystem (desktop notifications via `desktop-notifier`) extracted to the new `agent-core-notify` package. The `agent-core-notify` script and `agent_core.notify` module are no longer part of `agent-core`. Install `agent-core-notify` directly to use desktop notifications. ([#4](https://github.com/jeffrichley/agent_core/issues/4))

#### Fixed

- Routine green Acknowledgments now auto-clear by **shape**, not by registry
  lookup. The earlier gate required `_recent_outbound_ids[in_reply_to]` to be
  present, which left a startup gap: any ack arriving for a send issued
  before the daemon's most recent restart fell through to the pickup queue
  and woke the agent. The auto-clear path now relies on
  kind/urgency/note + the `in_reply_to == payload.of` integrity guard, so it
  survives daemon restarts. Yellow/red acks and notes prefixed `error:`
  still wake. ([#54](https://github.com/jeffrichley/agent_core/issues/54))
- Closed the publish/register ordering race in `send()` and `reply()` that
  left routine green Acknowledgments triggering a missing-ack-timer wake
  ~30s after delivery (issue #69, partial implementation of #54's
  "never wake you" contract). The bus is single-loop and dispatches
  in-process, so the recipient adapter's routine ack reaches the agent's
  `deliver()` while the original `await handle.publish(env)` is still
  in flight. The auto-clear path saw an empty registry and an unscheduled
  timer, then `_register_outbound_sent` ran *afterward* and scheduled a
  phantom missing-ack timer for an envelope that had already been acked.
  Fix: register the outbound and schedule the timer **before** awaiting
  publish; on publish failure, clean up the registry entry and cancel
  the timer. ([#69](https://github.com/jeffrichley/agent_core/issues/69))


### credentials

#### Added

- Initial release. Port of Pepper's PyKeePass-backed credential vault into agent-core. Provides `agent_core_credentials` library API and `agent-core-creds` CLI. AES-256 encrypted vault at `~/.agent-core/credentials.kdbx` (override via `AGENT_CORE_VAULT_PATH`); master password from `AGENT_CORE_VAULT_PASSWORD` env var. ([#5](https://github.com/jeffrichley/agent_core/issues/5))


### notify

#### Added

- Initial release as a standalone package. Carved out from `agent-core` 0.1.0; module renamed from `agent_core.notify` to `agent_core_notify`. No behavior change. ([#4](https://github.com/jeffrichley/agent_core/issues/4))


### agent-core-briefs

No significant changes.


### agent-core-busproxy

No significant changes.


### agent-core-channel

No significant changes.


### agent-core-discord

#### Added

- - `DiscordEndpoint` adapter — bridges one Discord bot to one named bus
    agent (1:1). Inbound messages and user reactions become `TextMessage` and
    `Event` envelopes; outbound `ToolInvocation` envelopes dispatch to 7
    Discord tools (`send`, `edit`, `react`, `fetch`, `download_attachments`,
    `list_channels`, `get_channel_info`). Replies via `Acknowledgment`
    envelopes. Access control via JSON config (DM policy + channel allowlist
    + ack emoji) ports verbatim from Pepper. ([#8](https://github.com/jeffrichley/agent_core/issues/8))
- - Outbound `send` now retries `channel.send` on Discord rate limits (HTTP 429) and
    transient 5xx/408 errors with exponential backoff, capped attempts, and
    `retry_after` when the API provides it. Multi-chunk sends use the same policy
    per chunk. On partial delivery (some chunks only), inbound 👀 ack is no
    longer cleared so the thread still signals an incomplete reply; the tool
    result remains `status=partial` with `message_ids` and `urgency=yellow`. ([#22](https://github.com/jeffrichley/agent_core/issues/22))


### agent-core-hatchery

No significant changes.


### agent-core-voice

No significant changes.


### agent-core-webcam

No significant changes.
