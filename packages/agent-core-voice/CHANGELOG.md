# Changelog

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-voice-v0.8.1) (2026-07-22)


### Bug Fixes

* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-voice-v0.8.0) (2026-07-21)


### Features

* **bus:** offload VoiceEndpoint construction to start() and add slow-deliver watchdog ([#331](https://github.com/jeffrichley/agent_core/issues/331)) ([57d98f5](https://github.com/jeffrichley/agent_core/commit/57d98f546d34afa250b69cc57256e2220a263228))
* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))
* **voice:** add format selection (mp3, ogg) to synthesize_speech ([#258](https://github.com/jeffrichley/agent_core/issues/258)) ([2414f9a](https://github.com/jeffrichley/agent_core/commit/2414f9acedec83b16801f8f2a7c64ecefa9502d0))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
