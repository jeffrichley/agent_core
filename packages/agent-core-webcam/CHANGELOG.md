# Changelog

## [0.11.0](https://github.com/jeffrichley/agent_core/compare/agent-core-webcam-v0.10.0...agent-core-webcam-v0.11.0) (2026-08-17)


### Features

* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **presence:** camera-derived presence awareness with multi-class identification ([#557](https://github.com/jeffrichley/agent_core/issues/557)) ([00e79c6](https://github.com/jeffrichley/agent_core/commit/00e79c636b7aab4e102d45a70cc2164c8eed483e))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))
* **voice:** agent-core-voice — per-agent Qwen3-TTS over the bus ([42713d7](https://github.com/jeffrichley/agent_core/commit/42713d78cc6bcecb9d88c114d1a180e07216968a))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **audit:** atomic append + disk-failure swallow test (voice & webcam) ([3ad7430](https://github.com/jeffrichley/agent_core/commit/3ad7430b837e779668b38a5f933a8746c702e371))
* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* **presence:** a dead sensor must not read as a quiet one ([#609](https://github.com/jeffrichley/agent_core/issues/609)) ([0414d19](https://github.com/jeffrichley/agent_core/commit/0414d19c1153f02cc05aa252b99cf8befc6ca1df))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.10.0](https://github.com/jeffrichley/agent_core/compare/v0.9.1...agent-core-webcam-v0.10.0) (2026-08-05)


### Features

* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **presence:** camera-derived presence awareness with multi-class identification ([#557](https://github.com/jeffrichley/agent_core/issues/557)) ([00e79c6](https://github.com/jeffrichley/agent_core/commit/00e79c636b7aab4e102d45a70cc2164c8eed483e))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))
* **voice:** agent-core-voice — per-agent Qwen3-TTS over the bus ([42713d7](https://github.com/jeffrichley/agent_core/commit/42713d78cc6bcecb9d88c114d1a180e07216968a))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **audit:** atomic append + disk-failure swallow test (voice & webcam) ([3ad7430](https://github.com/jeffrichley/agent_core/commit/3ad7430b837e779668b38a5f933a8746c702e371))
* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.9.1](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-webcam-v0.9.1) (2026-08-04)


### Bug Fixes

* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/v0.8.2...agent-core-webcam-v0.9.0) (2026-08-03)


### Features

* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **presence:** camera-derived presence awareness with multi-class identification ([#557](https://github.com/jeffrichley/agent_core/issues/557)) ([00e79c6](https://github.com/jeffrichley/agent_core/commit/00e79c636b7aab4e102d45a70cc2164c8eed483e))

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-webcam-v0.8.2) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-webcam-v0.8.1) (2026-07-22)


### Bug Fixes

* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-webcam-v0.8.0) (2026-07-21)


### Features

* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
