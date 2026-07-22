# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). Versions are VCS-derived (`uv-dynamic-versioning`); releases are cut with `just release <X.Y.Z>` (see `docs/setup/releases.md`).

This project uses [*towncrier*](https://towncrier.readthedocs.io/); unreleased changes live in per-package `changelog.d/<package>/` fragments.

<!-- towncrier release notes start -->

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...v0.8.2) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...v0.8.1) (2026-07-22)


### Bug Fixes

* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...v0.8.0) (2026-07-21)


### Features

* **bus:** add backup/restore — VACUUM INTO snapshots, retention, CLI ([#415](https://github.com/jeffrichley/agent_core/issues/415)) ([0a18681](https://github.com/jeffrichley/agent_core/commit/0a18681efa5fe444f716b46300f74c24eee690ba))
* **bus:** add BusHandle.spawn() tracked-task API ([#290](https://github.com/jeffrichley/agent_core/issues/290)) ([#294](https://github.com/jeffrichley/agent_core/issues/294)) ([d0b0791](https://github.com/jeffrichley/agent_core/commit/d0b0791ef7b56783de5c44376eecf0bb739ce202))
* **bus:** add delivery retry backoff with next_attempt_at (T5) ([#285](https://github.com/jeffrichley/agent_core/issues/285)) ([2169071](https://github.com/jeffrichley/agent_core/commit/21690717295e85327e6dff868d34ee8e7515095e))
* **bus:** add EndpointSupervisor + circuit-breaker state machine ([#292](https://github.com/jeffrichley/agent_core/issues/292)) ([d1f3fb1](https://github.com/jeffrichley/agent_core/commit/d1f3fb14478c44d491117912f56c7f1e82cda3a7))
* **bus:** add portable liveness watchdog (heartbeat + self-terminate) ([#343](https://github.com/jeffrichley/agent_core/issues/343)) ([628ffdb](https://github.com/jeffrichley/agent_core/commit/628ffdb418d126c95305dd54eb5cb0d062b74f38))
* **bus:** add pydantic daemon-config schema and real validate_config ([#422](https://github.com/jeffrichley/agent_core/issues/422)) ([e3e1a8d](https://github.com/jeffrichley/agent_core/commit/e3e1a8d6c3555d2bcfc2394f59d91508d997633e))
* **bus:** add SupervisorConfig block to BusConfig with boot logging ([#279](https://github.com/jeffrichley/agent_core/issues/279)) ([97a8522](https://github.com/jeffrichley/agent_core/commit/97a8522627205efcad41c19c7657f5b8ca48848d))
* **bus:** degraded boot, wire supervisor, state-change events, bus status ([#313](https://github.com/jeffrichley/agent_core/issues/313)) ([24a8eb4](https://github.com/jeffrichley/agent_core/commit/24a8eb452bef85f1955f0f3a6df2483e395bee53))
* **bus:** offload VoiceEndpoint construction to start() and add slow-deliver watchdog ([#331](https://github.com/jeffrichley/agent_core/issues/331)) ([57d98f5](https://github.com/jeffrichley/agent_core/commit/57d98f546d34afa250b69cc57256e2220a263228))
* **bus:** per-being config-fragment isolation + degraded load + migrate Pepper ([#381](https://github.com/jeffrichley/agent_core/issues/381)) ([808a271](https://github.com/jeffrichley/agent_core/commit/808a2713c74d0216f523fa4fe79b6d4555de8d83))
* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **core:** replace hardcoded __version__ with importlib.metadata lookup ([#342](https://github.com/jeffrichley/agent_core/issues/342)) ([c0e5238](https://github.com/jeffrichley/agent_core/commit/c0e52386ca3d575845310f2a9a1e3ef6439b53cc))
* **credentials:** add keyring master-password store with encrypted-file fallback ([#413](https://github.com/jeffrichley/agent_core/issues/413)) ([b5b8dd6](https://github.com/jeffrichley/agent_core/commit/b5b8dd6c671b9015ba5935540a6ded33dba13f91))
* **credentials:** make creds get metadata-only, remove stdout secret emission ([#411](https://github.com/jeffrichley/agent_core/issues/411)) ([8aae1e5](https://github.com/jeffrichley/agent_core/commit/8aae1e575f3791998b95a7dbe1e787353d7c0788))
* **daemon:** add config hygiene / drift detection to daemon doctor (Cα-3) ([#379](https://github.com/jeffrichley/agent_core/issues/379)) ([38ae9c9](https://github.com/jeffrichley/agent_core/commit/38ae9c98ce3c617be8609cc9ca3973ac1a39821c))
* **daemon:** add cross-platform autostart framework for Linux and macOS ([#340](https://github.com/jeffrichley/agent_core/issues/340)) ([b1d93d8](https://github.com/jeffrichley/agent_core/commit/b1d93d880848332f411e6190863cb2755505af95))
* **daemon:** add Windows Service headless autostart with unbounded restart ([#337](https://github.com/jeffrichley/agent_core/issues/337)) ([5b1b8df](https://github.com/jeffrichley/agent_core/commit/5b1b8dff47a69cf7aa8d160a9dd0b36a7fb73316))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **discord:** extract _HandlersMixin into _handlers.py per spec ([#458](https://github.com/jeffrichley/agent_core/issues/458)) ([6d137cf](https://github.com/jeffrichley/agent_core/commit/6d137cf751204600793bf8b8490065cbf9f52934)), closes [#441](https://github.com/jeffrichley/agent_core/issues/441)
* **discord:** extract _OutboundMixin and _ToolsMixin from endpoint.py ([#461](https://github.com/jeffrichley/agent_core/issues/461)) ([3e86492](https://github.com/jeffrichley/agent_core/commit/3e864928e7aa88863202050a2f1eaf8d42b16c8e))
* **discord:** voice memo capture + auto-transcription via faster-whisper ([#252](https://github.com/jeffrichley/agent_core/issues/252)) ([4c44c2f](https://github.com/jeffrichley/agent_core/commit/4c44c2f68adf248aa84a8a209f3bf33df12baa80))
* **hatchery:** hatch→run handoff — venv build + .mcp.json gen + daemon probe ([#410](https://github.com/jeffrichley/agent_core/issues/410)) ([04a54df](https://github.com/jeffrichley/agent_core/commit/04a54dfe4dbdb33b0187f88602d780e802879cc1))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#350](https://github.com/jeffrichley/agent_core/issues/350)) ([90141e5](https://github.com/jeffrichley/agent_core/commit/90141e595b6907a28f7b17bb6d0eccc3bf54e4d8))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **logging:** add structured JSON logging and correlation-id contextvar ([#462](https://github.com/jeffrichley/agent_core/issues/462)) ([b3397a2](https://github.com/jeffrichley/agent_core/commit/b3397a2b08aa2b083d26ba9d230bacfa07a23641))
* mypy --strict for agent-core-discord + log CancelledError swallows (closes [#444](https://github.com/jeffrichley/agent_core/issues/444)) ([#470](https://github.com/jeffrichley/agent_core/issues/470)) ([3ca9419](https://github.com/jeffrichley/agent_core/commit/3ca94193c98d547acfe2cd5203b314cb134656d4))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **secrets:** add vault-API accessor and scrub subprocess env ([#399](https://github.com/jeffrichley/agent_core/issues/399)) ([adb404b](https://github.com/jeffrichley/agent_core/commit/adb404b046bd224b56d64188b2cb56eb134d99b7))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))
* **venv:** add per-being pinned venv builder + absolute uv resolution ([#365](https://github.com/jeffrichley/agent_core/issues/365)) ([07ece11](https://github.com/jeffrichley/agent_core/commit/07ece113ddea8b111dece79588a74fc840864aea)), closes [#315](https://github.com/jeffrichley/agent_core/issues/315)
* **voice:** add format selection (mp3, ogg) to synthesize_speech ([#258](https://github.com/jeffrichley/agent_core/issues/258)) ([2414f9a](https://github.com/jeffrichley/agent_core/commit/2414f9acedec83b16801f8f2a7c64ecefa9502d0))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **bus:** install signal handlers before announcing readiness (deflake test_cli_run) ([#283](https://github.com/jeffrichley/agent_core/issues/283)) ([08fd821](https://github.com/jeffrichley/agent_core/commit/08fd8214a7958a921d0a6216ced2a1e0183d40ca))
* **discord,inbound:** nack transient failures instead of acking (issue [#275](https://github.com/jeffrichley/agent_core/issues/275)) ([#281](https://github.com/jeffrichley/agent_core/issues/281)) ([ad52bd6](https://github.com/jeffrichley/agent_core/commit/ad52bd6eb883f1706e76df32260317767f697b2d))
* **discord:** evict missing-timestamp typing orphans regardless of host uptime ([#335](https://github.com/jeffrichley/agent_core/issues/335)) ([0fce89c](https://github.com/jeffrichley/agent_core/commit/0fce89c3c76e796ba1eaaf72a804a2fbc2467268))
* **discord:** harden access-config reload loop against schema-invalid JSON ([#257](https://github.com/jeffrichley/agent_core/issues/257)) ([bd36b88](https://github.com/jeffrichley/agent_core/commit/bd36b8884abb062aadcea28ad4787a2dbcdca8ec))
* log or justify bare except-pass swallows + test get_client factory (closes [#408](https://github.com/jeffrichley/agent_core/issues/408)) ([#471](https://github.com/jeffrichley/agent_core/issues/471)) ([e67783c](https://github.com/jeffrichley/agent_core/commit/e67783ccb4dfab6f1783d6a67223f475f81526ea))
* **scheduler:** dispose aiosqlite engine on stop + guard against connection leaks ([#468](https://github.com/jeffrichley/agent_core/issues/468)) ([d14421f](https://github.com/jeffrichley/agent_core/commit/d14421f769b62566b8759a541e401ea050c81c44))

## [0.7.0](https://github.com/jeffrichley/agent_core/compare/v0.6.1...v0.7.0) (2026-07-02)


### Features

* **discord:** hot-reload access_config_path on mtime change ([#217](https://github.com/jeffrichley/agent_core/issues/217)) ([cc55ab8](https://github.com/jeffrichley/agent_core/commit/cc55ab8790720d6af68a0805484e6c4d0b28c516))
* **discord:** scrub lone UTF-16 surrogates at inbound boundary ([#215](https://github.com/jeffrichley/agent_core/issues/215)) ([80229ec](https://github.com/jeffrichley/agent_core/commit/80229ec57835e3c770b11b7ab2de749a9b36bab5))
* **inbound:** inbound notifications v1.a — GitHub → Wren via Tailscale Funnel ([#196](https://github.com/jeffrichley/agent_core/issues/196)) ([d180b73](https://github.com/jeffrichley/agent_core/commit/d180b73fe8981bc8cc7add7342800a3a8291d23d))
* **inbound:** v2 — schema-flexible GitHub event matching ([#199](https://github.com/jeffrichley/agent_core/issues/199)) ([a178628](https://github.com/jeffrichley/agent_core/commit/a1786282dde9cdfd493312468f9a533b9568945d))
* **inbound:** v2.1 connector-default body projection for Notification envelopes ([#204](https://github.com/jeffrichley/agent_core/issues/204)) ([d29f830](https://github.com/jeffrichley/agent_core/commit/d29f8309b728ae459b5f982e95e7a5b98fa17ea6))


### Bug Fixes

* **discord-access:** allowlisted bots must still pass channel filter ([#167](https://github.com/jeffrichley/agent_core/issues/167)) ([4acf307](https://github.com/jeffrichley/agent_core/commit/4acf30763357025e7f8e985eab820b5a7d9a5680)), closes [#165](https://github.com/jeffrichley/agent_core/issues/165)
* **discord-endpoint:** gate reaction/edit/delete/poll-vote by channel allowlist ([#209](https://github.com/jeffrichley/agent_core/issues/209)) ([a79bd68](https://github.com/jeffrichley/agent_core/commit/a79bd68749b09f8278cf68daee3a4d1ca94440c6))
* **discord:** gate meta-event handlers on channel allowlist ([#210](https://github.com/jeffrichley/agent_core/issues/210)) ([2ec23c4](https://github.com/jeffrichley/agent_core/commit/2ec23c4a9e32cd214a7f977a23f5ab2c1dc3c89c))
* **reply:** strip inbound-only Discord metadata keys in reply() and escalate Unrecognized-shape ack urgency ([#224](https://github.com/jeffrichley/agent_core/issues/224)) ([ca550c5](https://github.com/jeffrichley/agent_core/commit/ca550c5691b4a3228d0b57f46bff474cd543b143))

## [0.6.1](https://github.com/jeffrichley/agent_core/compare/v0.6.0...v0.6.1) (2026-06-07)


### Bug Fixes

* **discord:** on_message hands bot authors to the access gate ([#159](https://github.com/jeffrichley/agent_core/issues/159)) ([b36e44a](https://github.com/jeffrichley/agent_core/commit/b36e44a0a97ef1c3954df745caa144fb6acad997))

## [0.6.0](https://github.com/jeffrichley/agent_core/compare/v0.5.0...v0.6.0) (2026-06-07)


### Features

* canonical skills library + scheduler as first entry ([#134](https://github.com/jeffrichley/agent_core/issues/134)) ([6859010](https://github.com/jeffrichley/agent_core/commit/6859010359db3379550373eaea1dcb0d6a00b879))
* **daemon:** windows daemon auto-start (Phase 4) ([#110](https://github.com/jeffrichley/agent_core/issues/110)) ([c819b5d](https://github.com/jeffrichley/agent_core/commit/c819b5de19ff1a385cbb50b0f3a334effa814460))
* **discord-access:** add allowedBotIds opt-in allowlist for other-bot authors ([#158](https://github.com/jeffrichley/agent_core/issues/158)) ([aa0a835](https://github.com/jeffrichley/agent_core/commit/aa0a835c07fc7d6a8198847be2665fbe0b4ccd3e)), closes [#143](https://github.com/jeffrichley/agent_core/issues/143)

## [0.5.0](https://github.com/jeffrichley/agent_core/compare/v0.4.1...v0.5.0) (2026-05-26)


### ⚠ BREAKING CHANGES

* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130))

### Features

* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130)) ([574044c](https://github.com/jeffrichley/agent_core/commit/574044c4560f504a02c49b736fcc80cbe170672a))

## [0.4.1](https://github.com/jeffrichley/agent_core/compare/v0.4.0...v0.4.1) (2026-05-25)


### Bug Fixes

* **channel:** wire set_plugin_renderers at __main__ bootstrap ([#128](https://github.com/jeffrichley/agent_core/issues/128)) ([c1579fc](https://github.com/jeffrichley/agent_core/commit/c1579fc42dc3169a42049f372293b32e353fbc57))

## [0.4.0](https://github.com/jeffrichley/agent_core/compare/v0.3.0...v0.4.0) (2026-05-25)


### Features

* envelope extension hookspec — content-agnostic plugin seam for new kinds + renderers ([#124](https://github.com/jeffrichley/agent_core/issues/124)) ([ad4e166](https://github.com/jeffrichley/agent_core/commit/ad4e16686448f34f95e0203c8abccc79819163c8))

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
