# Changelog

## [0.10.0](https://github.com/jeffrichley/agent_core/compare/v0.9.1...agent-core-inbound-v0.10.0) (2026-08-05)


### Features

* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **inbound:** inbound notifications v1.a — GitHub → Wren via Tailscale Funnel ([#196](https://github.com/jeffrichley/agent_core/issues/196)) ([d180b73](https://github.com/jeffrichley/agent_core/commit/d180b73fe8981bc8cc7add7342800a3a8291d23d))
* **inbound:** v2 — schema-flexible GitHub event matching ([#199](https://github.com/jeffrichley/agent_core/issues/199)) ([a178628](https://github.com/jeffrichley/agent_core/commit/a1786282dde9cdfd493312468f9a533b9568945d))
* **inbound:** v2.1 connector-default body projection for Notification envelopes ([#204](https://github.com/jeffrichley/agent_core/issues/204)) ([d29f830](https://github.com/jeffrichley/agent_core/commit/d29f8309b728ae459b5f982e95e7a5b98fa17ea6))
* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **secrets:** add vault-API accessor and scrub subprocess env ([#399](https://github.com/jeffrichley/agent_core/issues/399)) ([adb404b](https://github.com/jeffrichley/agent_core/commit/adb404b046bd224b56d64188b2cb56eb134d99b7))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))


### Bug Fixes

* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* **discord,inbound:** nack transient failures instead of acking (issue [#275](https://github.com/jeffrichley/agent_core/issues/275)) ([#281](https://github.com/jeffrichley/agent_core/issues/281)) ([ad52bd6](https://github.com/jeffrichley/agent_core/commit/ad52bd6eb883f1706e76df32260317767f697b2d))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.9.1](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-inbound-v0.9.1) (2026-08-04)


### Bug Fixes

* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/v0.8.2...agent-core-inbound-v0.9.0) (2026-08-03)


### Features

* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-inbound-v0.8.2) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-inbound-v0.8.1) (2026-07-22)


### Bug Fixes

* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-inbound-v0.8.0) (2026-07-21)


### Features

* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **secrets:** add vault-API accessor and scrub subprocess env ([#399](https://github.com/jeffrichley/agent_core/issues/399)) ([adb404b](https://github.com/jeffrichley/agent_core/commit/adb404b046bd224b56d64188b2cb56eb134d99b7))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))


### Bug Fixes

* **discord,inbound:** nack transient failures instead of acking (issue [#275](https://github.com/jeffrichley/agent_core/issues/275)) ([#281](https://github.com/jeffrichley/agent_core/issues/281)) ([ad52bd6](https://github.com/jeffrichley/agent_core/commit/ad52bd6eb883f1706e76df32260317767f697b2d))
