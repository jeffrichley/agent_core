# Changelog

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-v0.8.2) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-v0.8.1) (2026-07-22)


### Bug Fixes

* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-v0.8.0) (2026-07-21)


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
* **daemon:** add config hygiene / drift detection to daemon doctor (Cα-3) ([#379](https://github.com/jeffrichley/agent_core/issues/379)) ([38ae9c9](https://github.com/jeffrichley/agent_core/commit/38ae9c98ce3c617be8609cc9ca3973ac1a39821c))
* **daemon:** add cross-platform autostart framework for Linux and macOS ([#340](https://github.com/jeffrichley/agent_core/issues/340)) ([b1d93d8](https://github.com/jeffrichley/agent_core/commit/b1d93d880848332f411e6190863cb2755505af95))
* **daemon:** add Windows Service headless autostart with unbounded restart ([#337](https://github.com/jeffrichley/agent_core/issues/337)) ([5b1b8df](https://github.com/jeffrichley/agent_core/commit/5b1b8dff47a69cf7aa8d160a9dd0b36a7fb73316))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **logging:** add structured JSON logging and correlation-id contextvar ([#462](https://github.com/jeffrichley/agent_core/issues/462)) ([b3397a2](https://github.com/jeffrichley/agent_core/commit/b3397a2b08aa2b083d26ba9d230bacfa07a23641))
* mypy --strict for agent-core-discord + log CancelledError swallows (closes [#444](https://github.com/jeffrichley/agent_core/issues/444)) ([#470](https://github.com/jeffrichley/agent_core/issues/470)) ([3ca9419](https://github.com/jeffrichley/agent_core/commit/3ca94193c98d547acfe2cd5203b314cb134656d4))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **secrets:** add vault-API accessor and scrub subprocess env ([#399](https://github.com/jeffrichley/agent_core/issues/399)) ([adb404b](https://github.com/jeffrichley/agent_core/commit/adb404b046bd224b56d64188b2cb56eb134d99b7))
* **venv:** add per-being pinned venv builder + absolute uv resolution ([#365](https://github.com/jeffrichley/agent_core/issues/365)) ([07ece11](https://github.com/jeffrichley/agent_core/commit/07ece113ddea8b111dece79588a74fc840864aea)), closes [#315](https://github.com/jeffrichley/agent_core/issues/315)


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **bus:** install signal handlers before announcing readiness (deflake test_cli_run) ([#283](https://github.com/jeffrichley/agent_core/issues/283)) ([08fd821](https://github.com/jeffrichley/agent_core/commit/08fd8214a7958a921d0a6216ced2a1e0183d40ca))
* log or justify bare except-pass swallows + test get_client factory (closes [#408](https://github.com/jeffrichley/agent_core/issues/408)) ([#471](https://github.com/jeffrichley/agent_core/issues/471)) ([e67783c](https://github.com/jeffrichley/agent_core/commit/e67783ccb4dfab6f1783d6a67223f475f81526ea))
* **scheduler:** dispose aiosqlite engine on stop + guard against connection leaks ([#468](https://github.com/jeffrichley/agent_core/issues/468)) ([d14421f](https://github.com/jeffrichley/agent_core/commit/d14421f769b62566b8759a541e401ea050c81c44))

## Changelog

All notable changes to `agent-core` are documented in this file. The format
is generated by towncrier from fragments in `changelog.d/` at release time.

<!-- towncrier release notes start -->
