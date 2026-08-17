# Changelog

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/agent-core-channel-v0.8.3...agent-core-channel-v0.9.0) (2026-08-17)


### Features

* **#83:** inline channel_id preview + auto-echo on discord-pepper ([0965320](https://github.com/jeffrichley/agent_core/commit/096532070c1b0501d4969966e8a7ed163145a74a))
* **channel:** _inbox_attrs helper with framework attrs ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([aafc84e](https://github.com/jeffrichley/agent_core/commit/aafc84e15cf9c586cb7b5639ce06702ff302aa6d))
* **channel:** layered RelayConfig resolver ([#70](https://github.com/jeffrichley/agent_core/issues/70)) ([fba3bb0](https://github.com/jeffrichley/agent_core/commit/fba3bb0a5dcdf6a54954973437398b2d22a1401d))
* **channel:** surface attachment block in inbox render ([#76](https://github.com/jeffrichley/agent_core/issues/76)) ([e49a59f](https://github.com/jeffrichley/agent_core/commit/e49a59f84762c81d2be1391bb804cf2f690f3951))
* **channel:** surface discord channel_id/channel_name on &lt;inbox&gt; preview ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([61082d4](https://github.com/jeffrichley/agent_core/commit/61082d42dfe43b179e0a2cb780180974b3b82372))
* **channel:** wire _inbox_attrs into _render_preview and _render_with_truncation ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([bac7f75](https://github.com/jeffrichley/agent_core/commit/bac7f753eb72544020a680a2538b1bdda8e495b0))
* **channel:** wire _inbox_attrs into render_envelope ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([43ab903](https://github.com/jeffrichley/agent_core/commit/43ab903576ec890abb81779816e779b7a7a4a4f7))
* **channel:** wire CLI flags + env vars to relay startup ([#70](https://github.com/jeffrichley/agent_core/issues/70)) ([0728705](https://github.com/jeffrichley/agent_core/commit/0728705b8fe746967999ce2ed692c84d1120f034))
* envelope extension hookspec — content-agnostic plugin seam for new kinds + renderers ([#124](https://github.com/jeffrichley/agent_core/issues/124)) ([ad4e166](https://github.com/jeffrichley/agent_core/commit/ad4e16686448f34f95e0203c8abccc79819163c8))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* inline-content wake via relay-side prefetch ([#70](https://github.com/jeffrichley/agent_core/issues/70)) ([eb347b0](https://github.com/jeffrichley/agent_core/commit/eb347b090a4c95e380a06ba2c6bc03dcb95b5c56))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))


### Bug Fixes

* **channel:** declare agent-core-bus and fastmcp, which the code imports ([#583](https://github.com/jeffrichley/agent_core/issues/583)) ([4b971a8](https://github.com/jeffrichley/agent_core/commit/4b971a811614ac97ca38e5dad32f65ae855f9f4b)), closes [#566](https://github.com/jeffrichley/agent_core/issues/566)
* **channel:** move _inbox_attrs test import to top of file ([#83](https://github.com/jeffrichley/agent_core/issues/83)) ([fc52364](https://github.com/jeffrichley/agent_core/commit/fc52364e7d2fbdffd2021ef6859a67ba5896bc7c))
* **channel:** wire set_plugin_renderers at __main__ bootstrap ([#128](https://github.com/jeffrichley/agent_core/issues/128)) ([c1579fc](https://github.com/jeffrichley/agent_core/commit/c1579fc42dc3169a42049f372293b32e353fbc57))
* **ci:** set TERMINAL_WIDTH for check job; drop ineffective per-test COLUMNS ([a9a11ca](https://github.com/jeffrichley/agent_core/commit/a9a11ca2b4b3cfcb6e331ded9dbaac47212b9ae2))
* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.3](https://github.com/jeffrichley/agent_core/compare/v0.8.2...agent-core-channel-v0.8.3) (2026-08-05)


### Bug Fixes

* **channel:** declare agent-core-bus and fastmcp, which the code imports ([#583](https://github.com/jeffrichley/agent_core/issues/583)) ([4b971a8](https://github.com/jeffrichley/agent_core/commit/4b971a811614ac97ca38e5dad32f65ae855f9f4b)), closes [#566](https://github.com/jeffrichley/agent_core/issues/566)

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-channel-v0.8.2) (2026-08-03)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-channel-v0.8.1) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-channel-v0.8.0) (2026-07-21)


### Features

* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
