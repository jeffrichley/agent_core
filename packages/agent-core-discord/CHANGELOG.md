# Changelog

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/agent-core-discord-v0.8.3...agent-core-discord-v0.9.0) (2026-08-05)


### Features

* **#114:** unified discord_send envelope shape + strict-mode validator ([#119](https://github.com/jeffrichley/agent_core/issues/119)) ([a6d17fc](https://github.com/jeffrichley/agent_core/commit/a6d17fcd16b7831d6a4c637785b9f78dd15070df))
* **#64:** wire discord-pepper file-attachment path through to upload ([9f76846](https://github.com/jeffrichley/agent_core/commit/9f76846c63c492f07ef802d89f91939319a7b668))
* **#83:** inline channel_id preview + auto-echo on discord-pepper ([0965320](https://github.com/jeffrichley/agent_core/commit/096532070c1b0501d4969966e8a7ed163145a74a))
* **#84:** typing-cleanup linkage via bus in_reply_to + 90s TTL safety net ([53a004b](https://github.com/jeffrichley/agent_core/commit/53a004b593d19f92f2fcf1ba844437e885390ca8))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **discord:** _recent_inbounds cache field and recorder ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([c30ec80](https://github.com/jeffrichley/agent_core/commit/c30ec8079829f9819c7c398810783fe3cc9f2667))
* **discord:** _recent_inbounds LRU eviction at max ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([452f341](https://github.com/jeffrichley/agent_core/commit/452f3416c3d2c7787847e6e28e35867916668ba9))
* **discord:** _recent_inbounds TTL sweep ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([94f4453](https://github.com/jeffrichley/agent_core/commit/94f44535c253718281550b6096d26b9a4ec1c700))
* **discord:** _resolve_channel_id chain with sub-cause logging ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([74dabe4](https://github.com/jeffrichley/agent_core/commit/74dabe45cd9fbcd3d32e5c5244aa9c475fed6b6e))
* **discord-access:** add allowedBotIds opt-in allowlist for other-bot authors ([#158](https://github.com/jeffrichley/agent_core/issues/158)) ([aa0a835](https://github.com/jeffrichley/agent_core/commit/aa0a835c07fc7d6a8198847be2665fbe0b4ccd3e)), closes [#143](https://github.com/jeffrichley/agent_core/issues/143)
* **discord:** 90s TTL safety net + self-heal in _typing_while_pending ([#84](https://github.com/jeffrichley/agent_core/issues/84)) ([e81fde8](https://github.com/jeffrichley/agent_core/commit/e81fde86c8000fcb85c6886723341f5712bb493c))
* **discord:** add _SendArgs.cleanup_inbound_message_id optional field ([#84](https://github.com/jeffrichley/agent_core/issues/84)) ([f4bb4e0](https://github.com/jeffrichley/agent_core/commit/f4bb4e081d44ad9badc5183d92dce2ac5305140c))
* **discord:** auto-download attachments at inbound, enrich metadata ([#76](https://github.com/jeffrichley/agent_core/issues/76)) ([fcb791a](https://github.com/jeffrichley/agent_core/commit/fcb791a4e19ec91983bd31931c2aaf099d6b1105))
* **discord:** daemon-side attachment retention sweep ([#76](https://github.com/jeffrichley/agent_core/issues/76)) ([3948da7](https://github.com/jeffrichley/agent_core/commit/3948da721a55bc4a169b0661f23dc42798736afe))
* **discord:** extract _HandlersMixin into _handlers.py per spec ([#458](https://github.com/jeffrichley/agent_core/issues/458)) ([6d137cf](https://github.com/jeffrichley/agent_core/commit/6d137cf751204600793bf8b8490065cbf9f52934)), closes [#441](https://github.com/jeffrichley/agent_core/issues/441)
* **discord:** extract _OutboundMixin and _ToolsMixin from endpoint.py ([#461](https://github.com/jeffrichley/agent_core/issues/461)) ([3e86492](https://github.com/jeffrichley/agent_core/commit/3e864928e7aa88863202050a2f1eaf8d42b16c8e))
* **discord:** hot-reload access_config_path on mtime change ([#217](https://github.com/jeffrichley/agent_core/issues/217)) ([cc55ab8](https://github.com/jeffrichley/agent_core/commit/cc55ab8790720d6af68a0805484e6c4d0b28c516))
* **discord:** pair-manage _awaiting_reply_ids with timestamps dict ([#84](https://github.com/jeffrichley/agent_core/issues/84)) ([c974d34](https://github.com/jeffrichley/agent_core/commit/c974d34c22557844c0dac4e1a4a6eae5ee6f2321))
* **discord:** record inbounds in _recent_inbounds on publish ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([898de1b](https://github.com/jeffrichley/agent_core/commit/898de1b150d2192d1e9c810aa56705d9036e258a))
* **discord:** scrub lone UTF-16 surrogates at inbound boundary ([#215](https://github.com/jeffrichley/agent_core/issues/215)) ([80229ec](https://github.com/jeffrichley/agent_core/commit/80229ec57835e3c770b11b7ab2de749a9b36bab5))
* **discord:** voice memo capture + auto-transcription via faster-whisper ([#252](https://github.com/jeffrichley/agent_core/issues/252)) ([4c44c2f](https://github.com/jeffrichley/agent_core/commit/4c44c2f68adf248aa84a8a209f3bf33df12baa80))
* **discord:** wire payload.attachments through _deliver_text_message ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([8dac265](https://github.com/jeffrichley/agent_core/commit/8dac26583256a9799b18ab3889cac061de22b770))
* **discord:** wire TextMessage typing-cleanup via bus in_reply_to ([#84](https://github.com/jeffrichley/agent_core/issues/84)) ([ddd6b3a](https://github.com/jeffrichley/agent_core/commit/ddd6b3ad135495592e03c60d13b76b9eaf65db98))
* **discord:** wire ToolInvocation typing-cleanup via bus in_reply_to ([#84](https://github.com/jeffrichley/agent_core/issues/84)) ([9e5d82d](https://github.com/jeffrichley/agent_core/commit/9e5d82dbe52751836613f69c0b8ce4ac70f567f9))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* mypy --strict for agent-core-discord + log CancelledError swallows (closes [#444](https://github.com/jeffrichley/agent_core/issues/444)) ([#470](https://github.com/jeffrichley/agent_core/issues/470)) ([3ca9419](https://github.com/jeffrichley/agent_core/commit/3ca94193c98d547acfe2cd5203b314cb134656d4))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **secrets:** add vault-API accessor and scrub subprocess env ([#399](https://github.com/jeffrichley/agent_core/issues/399)) ([adb404b](https://github.com/jeffrichley/agent_core/commit/adb404b046bd224b56d64188b2cb56eb134d99b7))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))
* **testing:** model attachment roundtrip in FakeMessage/FakeChannel ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([41d93d8](https://github.com/jeffrichley/agent_core/commit/41d93d8667e08c1bce1c305efe34f0f2c05558ab))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))


### Bug Fixes

* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* **discord-access:** allowlisted bots must still pass channel filter ([#167](https://github.com/jeffrichley/agent_core/issues/167)) ([4acf307](https://github.com/jeffrichley/agent_core/commit/4acf30763357025e7f8e985eab820b5a7d9a5680)), closes [#165](https://github.com/jeffrichley/agent_core/issues/165)
* **discord,inbound:** nack transient failures instead of acking (issue [#275](https://github.com/jeffrichley/agent_core/issues/275)) ([#281](https://github.com/jeffrichley/agent_core/issues/281)) ([ad52bd6](https://github.com/jeffrichley/agent_core/commit/ad52bd6eb883f1706e76df32260317767f697b2d))
* **discord:** cancel both sweep tasks before awaiting — attachment sweep no longer starves no-sleep tests ([#76](https://github.com/jeffrichley/agent_core/issues/76) Task 5) ([5eebdfd](https://github.com/jeffrichley/agent_core/commit/5eebdfdf9033ccf9bb935b504c550465ecde6625))
* **discord:** evict missing-timestamp typing orphans regardless of host uptime ([#335](https://github.com/jeffrichley/agent_core/issues/335)) ([0fce89c](https://github.com/jeffrichley/agent_core/commit/0fce89c3c76e796ba1eaaf72a804a2fbc2467268))
* **discord:** gate meta-event handlers on channel allowlist ([#210](https://github.com/jeffrichley/agent_core/issues/210)) ([2ec23c4](https://github.com/jeffrichley/agent_core/commit/2ec23c4a9e32cd214a7f977a23f5ab2c1dc3c89c))
* **discord:** harden access-config reload loop against schema-invalid JSON ([#257](https://github.com/jeffrichley/agent_core/issues/257)) ([bd36b88](https://github.com/jeffrichley/agent_core/commit/bd36b8884abb062aadcea28ad4787a2dbcdca8ec))
* **discord:** on_message hands bot authors to the access gate ([#159](https://github.com/jeffrichley/agent_core/issues/159)) ([b36e44a](https://github.com/jeffrichley/agent_core/commit/b36e44a0a97ef1c3954df745caa144fb6acad997))
* **discord:** redact signed CDN urls from download_error + logs ([#76](https://github.com/jeffrichley/agent_core/issues/76) Task 3) ([79c9401](https://github.com/jeffrichley/agent_core/commit/79c94018b1aa8da4874f345e7ea1afc5e13b3905))
* **discord:** same cancel-all-before-await fix in start-rollback path ([#76](https://github.com/jeffrichley/agent_core/issues/76) Task 5) ([79bb4a7](https://github.com/jeffrichley/agent_core/commit/79bb4a7e57a1bc1a9b92c984b48c26c89d596098))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* **reply:** strip inbound-only Discord metadata keys in reply() and escalate Unrecognized-shape ack urgency ([#224](https://github.com/jeffrichley/agent_core/issues/224)) ([ca550c5](https://github.com/jeffrichley/agent_core/commit/ca550c5691b4a3228d0b57f46bff474cd543b143))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.3](https://github.com/jeffrichley/agent_core/compare/v0.8.2...agent-core-discord-v0.8.3) (2026-08-04)


### Bug Fixes

* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-discord-v0.8.2) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-discord-v0.8.1) (2026-07-22)


### Bug Fixes

* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))

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
