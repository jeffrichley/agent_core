# Changelog

## [0.11.0](https://github.com/jeffrichley/agent_core/compare/agent-core-voice-v0.10.0...agent-core-voice-v0.11.0) (2026-08-17)


### ⚠ BREAKING CHANGES

* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130))

### Features

* **bus:** offload VoiceEndpoint construction to start() and add slow-deliver watchdog ([#331](https://github.com/jeffrichley/agent_core/issues/331)) ([57d98f5](https://github.com/jeffrichley/agent_core/commit/57d98f546d34afa250b69cc57256e2220a263228))
* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **release:** phase 2.6 — end-to-end install validation (closes 3 release-pipeline bugs) ([#121](https://github.com/jeffrichley/agent_core/issues/121)) ([ce8ee59](https://github.com/jeffrichley/agent_core/commit/ce8ee5971acb46d64944f3df3149249214dabcf8))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))
* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130)) ([574044c](https://github.com/jeffrichley/agent_core/commit/574044c4560f504a02c49b736fcc80cbe170672a))
* **voice:** add format selection (mp3, ogg) to synthesize_speech ([#258](https://github.com/jeffrichley/agent_core/issues/258)) ([2414f9a](https://github.com/jeffrichley/agent_core/commit/2414f9acedec83b16801f8f2a7c64ecefa9502d0))
* **voice:** agent-core-voice — per-agent Qwen3-TTS over the bus ([42713d7](https://github.com/jeffrichley/agent_core/commit/42713d78cc6bcecb9d88c114d1a180e07216968a))
* **voice:** bootstrap agent-core-voice package skeleton ([5b09b5b](https://github.com/jeffrichley/agent_core/commit/5b09b5b4829b59112ca9ca6075317cab7ea5787a))
* **voice:** caller-side max_text_len on VoiceEndpoint (default 2000) ([3e437fc](https://github.com/jeffrichley/agent_core/commit/3e437fcab1d12e756c0a82e36df8995268f3133c))
* **voice:** error mapping + audit-on-failure for synthesize_safe ([4e909db](https://github.com/jeffrichley/agent_core/commit/4e909db4a5d8eba5689a55a6c97eacea9105af9b))
* **voice:** fail-fast CUDA availability check in QwenTTSBackend ([170f18c](https://github.com/jeffrichley/agent_core/commit/170f18c5fad63a32a840d03474526abf97c704b4))
* **voice:** FakeTTSBackend with deterministic distinct outputs ([e8bd8e8](https://github.com/jeffrichley/agent_core/commit/e8bd8e87173fa6cf60fd5a9c424ca1722c3012d1))
* **voice:** jsonl audit log with success/error schema ([6fd19e6](https://github.com/jeffrichley/agent_core/commit/6fd19e624e321ba58cdb9e48e2f78012b95ccc5c))
* **voice:** MCP synthesize_speech + voice_info with closure-bound voice_id ([45a6dbd](https://github.com/jeffrichley/agent_core/commit/45a6dbd7c95d1632b7634faf63e8cf83c0eab124))
* **voice:** plugin register_endpoint_types + reserved_endpoint_params ([a5753c9](https://github.com/jeffrichley/agent_core/commit/a5753c9ac498b9d82540e33e302f840aba7f013a))
* **voice:** plugin wire_endpoints_after_registration with isolation ([bf52f79](https://github.com/jeffrichley/agent_core/commit/bf52f798f3abc8279202699c3beb9b24d2161ea3))
* **voice:** QwenTTSBackend skeleton + endpoint production path ([df19eb5](https://github.com/jeffrichley/agent_core/commit/df19eb506ac2012c42f149fc12407bf44c931707))
* **voice:** synthesize_safe happy path with service-owned output paths ([b758fac](https://github.com/jeffrichley/agent_core/commit/b758face741a9aaf19e553fde761c24f59c43fa9))
* **voice:** TTSBackend protocol + error taxonomy ([3dd314d](https://github.com/jeffrichley/agent_core/commit/3dd314d5e5a3ed1888df2a0ccc71ba55f3744ad7))
* **voice:** VoiceEndpoint construction + voice-registry prep ([376bdd7](https://github.com/jeffrichley/agent_core/commit/376bdd731e9010269d6f40d72213460f1a1f866e))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **audit:** atomic append + disk-failure swallow test (voice & webcam) ([3ad7430](https://github.com/jeffrichley/agent_core/commit/3ad7430b837e779668b38a5f933a8746c702e371))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))
* **voice:** align VoiceEndpoint deliver/start to Endpoint protocol ([f0f6f36](https://github.com/jeffrichley/agent_core/commit/f0f6f365ba184e72da1ad8a5af255e51ce1e3897))
* **voice:** expanduser() on output_dir / audit_path / ref_wav ([9a4ab03](https://github.com/jeffrichley/agent_core/commit/9a4ab033fc3d7651adc3ccf9421ef2e308bcd0d4))
* **voice:** microsecond timestamp + single now() in synthesize_safe ([7e1d34b](https://github.com/jeffrichley/agent_core/commit/7e1d34bf36f89185a3bb5e4ce6bf09919b8272dc))
* **voice:** single 'synthesis failed:' prefix + ensure_ascii=False in MCP ([0fce53d](https://github.com/jeffrichley/agent_core/commit/0fce53daa6011610614df746f10e48c571ef92d2))
* **voice:** widen synthesize_safe to swallow wav decode errors ([d591cdd](https://github.com/jeffrichley/agent_core/commit/d591cdd7aa3a964545c02f35bb4590aae89373ed))

## [0.10.0](https://github.com/jeffrichley/agent_core/compare/v0.9.1...agent-core-voice-v0.10.0) (2026-08-05)


### ⚠ BREAKING CHANGES

* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130))

### Features

* **bus:** offload VoiceEndpoint construction to start() and add slow-deliver watchdog ([#331](https://github.com/jeffrichley/agent_core/issues/331)) ([57d98f5](https://github.com/jeffrichley/agent_core/commit/57d98f546d34afa250b69cc57256e2220a263228))
* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **release:** phase 2.6 — end-to-end install validation (closes 3 release-pipeline bugs) ([#121](https://github.com/jeffrichley/agent_core/issues/121)) ([ce8ee59](https://github.com/jeffrichley/agent_core/commit/ce8ee5971acb46d64944f3df3149249214dabcf8))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))
* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130)) ([574044c](https://github.com/jeffrichley/agent_core/commit/574044c4560f504a02c49b736fcc80cbe170672a))
* **voice:** add format selection (mp3, ogg) to synthesize_speech ([#258](https://github.com/jeffrichley/agent_core/issues/258)) ([2414f9a](https://github.com/jeffrichley/agent_core/commit/2414f9acedec83b16801f8f2a7c64ecefa9502d0))
* **voice:** agent-core-voice — per-agent Qwen3-TTS over the bus ([42713d7](https://github.com/jeffrichley/agent_core/commit/42713d78cc6bcecb9d88c114d1a180e07216968a))
* **voice:** bootstrap agent-core-voice package skeleton ([5b09b5b](https://github.com/jeffrichley/agent_core/commit/5b09b5b4829b59112ca9ca6075317cab7ea5787a))
* **voice:** caller-side max_text_len on VoiceEndpoint (default 2000) ([3e437fc](https://github.com/jeffrichley/agent_core/commit/3e437fcab1d12e756c0a82e36df8995268f3133c))
* **voice:** error mapping + audit-on-failure for synthesize_safe ([4e909db](https://github.com/jeffrichley/agent_core/commit/4e909db4a5d8eba5689a55a6c97eacea9105af9b))
* **voice:** fail-fast CUDA availability check in QwenTTSBackend ([170f18c](https://github.com/jeffrichley/agent_core/commit/170f18c5fad63a32a840d03474526abf97c704b4))
* **voice:** FakeTTSBackend with deterministic distinct outputs ([e8bd8e8](https://github.com/jeffrichley/agent_core/commit/e8bd8e87173fa6cf60fd5a9c424ca1722c3012d1))
* **voice:** jsonl audit log with success/error schema ([6fd19e6](https://github.com/jeffrichley/agent_core/commit/6fd19e624e321ba58cdb9e48e2f78012b95ccc5c))
* **voice:** MCP synthesize_speech + voice_info with closure-bound voice_id ([45a6dbd](https://github.com/jeffrichley/agent_core/commit/45a6dbd7c95d1632b7634faf63e8cf83c0eab124))
* **voice:** plugin register_endpoint_types + reserved_endpoint_params ([a5753c9](https://github.com/jeffrichley/agent_core/commit/a5753c9ac498b9d82540e33e302f840aba7f013a))
* **voice:** plugin wire_endpoints_after_registration with isolation ([bf52f79](https://github.com/jeffrichley/agent_core/commit/bf52f798f3abc8279202699c3beb9b24d2161ea3))
* **voice:** QwenTTSBackend skeleton + endpoint production path ([df19eb5](https://github.com/jeffrichley/agent_core/commit/df19eb506ac2012c42f149fc12407bf44c931707))
* **voice:** synthesize_safe happy path with service-owned output paths ([b758fac](https://github.com/jeffrichley/agent_core/commit/b758face741a9aaf19e553fde761c24f59c43fa9))
* **voice:** TTSBackend protocol + error taxonomy ([3dd314d](https://github.com/jeffrichley/agent_core/commit/3dd314d5e5a3ed1888df2a0ccc71ba55f3744ad7))
* **voice:** VoiceEndpoint construction + voice-registry prep ([376bdd7](https://github.com/jeffrichley/agent_core/commit/376bdd731e9010269d6f40d72213460f1a1f866e))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **audit:** atomic append + disk-failure swallow test (voice & webcam) ([3ad7430](https://github.com/jeffrichley/agent_core/commit/3ad7430b837e779668b38a5f933a8746c702e371))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))
* **voice:** align VoiceEndpoint deliver/start to Endpoint protocol ([f0f6f36](https://github.com/jeffrichley/agent_core/commit/f0f6f365ba184e72da1ad8a5af255e51ce1e3897))
* **voice:** expanduser() on output_dir / audit_path / ref_wav ([9a4ab03](https://github.com/jeffrichley/agent_core/commit/9a4ab033fc3d7651adc3ccf9421ef2e308bcd0d4))
* **voice:** microsecond timestamp + single now() in synthesize_safe ([7e1d34b](https://github.com/jeffrichley/agent_core/commit/7e1d34bf36f89185a3bb5e4ce6bf09919b8272dc))
* **voice:** single 'synthesis failed:' prefix + ensure_ascii=False in MCP ([0fce53d](https://github.com/jeffrichley/agent_core/commit/0fce53daa6011610614df746f10e48c571ef92d2))
* **voice:** widen synthesize_safe to swallow wav decode errors ([d591cdd](https://github.com/jeffrichley/agent_core/commit/d591cdd7aa3a964545c02f35bb4590aae89373ed))

## [0.9.1](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-voice-v0.9.1) (2026-08-04)


### Bug Fixes

* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/v0.8.2...agent-core-voice-v0.9.0) (2026-08-03)


### Features

* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-voice-v0.8.2) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

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
