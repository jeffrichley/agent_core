# Changelog

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-discord-v0.8.0) (2026-07-21)


### Features

* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **discord:** extract _HandlersMixin into _handlers.py per spec ([#458](https://github.com/jeffrichley/agent_core/issues/458)) ([6d137cf](https://github.com/jeffrichley/agent_core/commit/6d137cf751204600793bf8b8490065cbf9f52934)), closes [#441](https://github.com/jeffrichley/agent_core/issues/441)
* **discord:** extract _OutboundMixin and _ToolsMixin from endpoint.py ([#461](https://github.com/jeffrichley/agent_core/issues/461)) ([3e86492](https://github.com/jeffrichley/agent_core/commit/3e864928e7aa88863202050a2f1eaf8d42b16c8e))
* **discord:** voice memo capture + auto-transcription via faster-whisper ([#252](https://github.com/jeffrichley/agent_core/issues/252)) ([4c44c2f](https://github.com/jeffrichley/agent_core/commit/4c44c2f68adf248aa84a8a209f3bf33df12baa80))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* mypy --strict for agent-core-discord + log CancelledError swallows (closes [#444](https://github.com/jeffrichley/agent_core/issues/444)) ([#470](https://github.com/jeffrichley/agent_core/issues/470)) ([3ca9419](https://github.com/jeffrichley/agent_core/commit/3ca94193c98d547acfe2cd5203b314cb134656d4))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **secrets:** add vault-API accessor and scrub subprocess env ([#399](https://github.com/jeffrichley/agent_core/issues/399)) ([adb404b](https://github.com/jeffrichley/agent_core/commit/adb404b046bd224b56d64188b2cb56eb134d99b7))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))


### Bug Fixes

* **discord,inbound:** nack transient failures instead of acking (issue [#275](https://github.com/jeffrichley/agent_core/issues/275)) ([#281](https://github.com/jeffrichley/agent_core/issues/281)) ([ad52bd6](https://github.com/jeffrichley/agent_core/commit/ad52bd6eb883f1706e76df32260317767f697b2d))
* **discord:** evict missing-timestamp typing orphans regardless of host uptime ([#335](https://github.com/jeffrichley/agent_core/issues/335)) ([0fce89c](https://github.com/jeffrichley/agent_core/commit/0fce89c3c76e796ba1eaaf72a804a2fbc2467268))
* **discord:** harden access-config reload loop against schema-invalid JSON ([#257](https://github.com/jeffrichley/agent_core/issues/257)) ([bd36b88](https://github.com/jeffrichley/agent_core/commit/bd36b8884abb062aadcea28ad4787a2dbcdca8ec))

## agent-core-discord changelog

<!-- towncrier release notes start -->
