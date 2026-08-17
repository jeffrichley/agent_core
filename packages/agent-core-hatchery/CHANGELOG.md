# Changelog

## [0.12.0](https://github.com/jeffrichley/agent_core/compare/agent-core-hatchery-v0.11.0...agent-core-hatchery-v0.12.0) (2026-08-17)


### Features

* **core:** endpoints.d + jobs.d conf.d-style merging ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([e385077](https://github.com/jeffrichley/agent_core/commit/e385077bfa448639680719dfa0a818f0cba048c9))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* enable mypy --strict for agent-core-hatchery ([#519](https://github.com/jeffrichley/agent_core/issues/519)) ([2365ab2](https://github.com/jeffrichley/agent_core/commit/2365ab27de4a450ec2a8c8d6211f7fdb43678a3d))
* **hatchery:** add --no-daemon-reload to hatch without a live daemon ([#483](https://github.com/jeffrichley/agent_core/issues/483)) ([7bf06c6](https://github.com/jeffrichley/agent_core/commit/7bf06c64519033ffe4b768e1bb0318282f908e77))
* **hatchery:** agent-core-hatchery package ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2cde559](https://github.com/jeffrichley/agent_core/commit/2cde55997abe5614ef0bac2154a51d95a80fef9a))
* **hatchery:** basic Hatcher orchestration (render → write → validate) ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([bca1c1d](https://github.com/jeffrichley/agent_core/commit/bca1c1d4c97d23ed01c23e22881bb64445f08de6))
* **hatchery:** channel scaffolding modules — Discord, webcam, GitHub backup ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([ccae9a4](https://github.com/jeffrichley/agent_core/commit/ccae9a4ec686af6084d2f12dc6272b2637c53f79))
* **hatchery:** cli with --config mode (Phase 2 stop) ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([19039ab](https://github.com/jeffrichley/agent_core/commit/19039ab4d4afc38309ec89411194bcb306fb6e43))
* **hatchery:** config and daemon-fragment templates ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([b895feb](https://github.com/jeffrichley/agent_core/commit/b895febe930968c6742fb1a6d055397fc9354d5f))
* **hatchery:** daemon_config writer for endpoints.d + jobs.d fragments ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([7c967a4](https://github.com/jeffrichley/agent_core/commit/7c967a43ccc659d0b18b397015fd09655582aed6))
* **hatchery:** daemon-fragment writing + parse validation in Hatcher ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2f93f97](https://github.com/jeffrichley/agent_core/commit/2f93f97d388b48bc4cd666d045ee56e40e70e233))
* **hatchery:** elder-letter manifest resolver ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([f27992f](https://github.com/jeffrichley/agent_core/commit/f27992ffb7610f73fe36e8aad3eaa6446bea2a9b))
* **hatchery:** elder-letters manifest + Pepper's bundled snapshot ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([1a15482](https://github.com/jeffrichley/agent_core/commit/1a154827c890fcc5515b0361bd156b111098fe19))
* **hatchery:** file_classes manifest loader ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([dadb2da](https://github.com/jeffrichley/agent_core/commit/dadb2dac61bb0561a1e3a445d87df0ce2e7b1050))
* **hatchery:** file-classes.yaml manifest ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([d27d0fa](https://github.com/jeffrichley/agent_core/commit/d27d0fafb88c6b4ddb2275ad9bb6db20c5385ad8))
* **hatchery:** hatch→run handoff — venv build + .mcp.json gen + daemon probe ([#410](https://github.com/jeffrichley/agent_core/issues/410)) ([04a54df](https://github.com/jeffrichley/agent_core/commit/04a54dfe4dbdb33b0187f88602d780e802879cc1))
* **hatchery:** HatchConfig pydantic model ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([bc10d62](https://github.com/jeffrichley/agent_core/commit/bc10d62c34f1b740e8a51156ac6b8e107ea2621e))
* **hatchery:** hatchery-snapshot-elders CLI ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([bbbeb85](https://github.com/jeffrichley/agent_core/commit/bbbeb85c89a5f50161c750ef6f92ac87210f7692))
* **hatchery:** HATCHING-REPORT.md generator ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([ce9b267](https://github.com/jeffrichley/agent_core/commit/ce9b2676f5211a7e028b17da5d916b449c3fc3a4))
* **hatchery:** Jinja2 renderer with StrictUndefined ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([58bd143](https://github.com/jeffrichley/agent_core/commit/58bd143825f02042ee9b88f90e6736a0f98c4586))
* **hatchery:** migrate memory templates from templates-draft ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([33659a5](https://github.com/jeffrichley/agent_core/commit/33659a5ee70e76f13c3fcc63a2920fccebdf455a))
* **hatchery:** package skeleton ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([8b1fe06](https://github.com/jeffrichley/agent_core/commit/8b1fe06b27965df86755590c8cd1a1ab2f30c20e))
* **hatchery:** Questionary TUI wizard ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([9d3db46](https://github.com/jeffrichley/agent_core/commit/9d3db46535f18df9f56bed0e63f611da7fd4d9b0))
* **hatchery:** skill-author universal skill ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([3ce3b2d](https://github.com/jeffrichley/agent_core/commit/3ce3b2d9ff08a3e17f71037e0a5c5e4094ce439e))
* **hatchery:** spawning-subagents universal skill ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([fe105cc](https://github.com/jeffrichley/agent_core/commit/fe105cc79eb0c005dcbdcf0fb14d326e730988a3))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#350](https://github.com/jeffrichley/agent_core/issues/350)) ([90141e5](https://github.com/jeffrichley/agent_core/commit/90141e595b6907a28f7b17bb6d0eccc3bf54e4d8))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **hatchery:** vault-lint universal skill ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([cf339ee](https://github.com/jeffrichley/agent_core/commit/cf339eee7fda6007d9d1dd8e5efb7a7b5850c0ca))
* **hatchery:** wire channels into daemon-fragment writing ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([f37977d](https://github.com/jeffrichley/agent_core/commit/f37977d3338036c10248bd904c9b10a043ba895c))
* **hatchery:** wire elder-letter copying into Hatcher ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([29509e4](https://github.com/jeffrichley/agent_core/commit/29509e419302688600dbf7a1bf7bebb829af2b75))
* **hatchery:** wire skill-tree copying into Hatcher ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([b5287ff](https://github.com/jeffrichley/agent_core/commit/b5287ff180b705e8eb6a26bebcc3c787cfa45213))
* **hatchery:** wire wizard + EDITOR gate + HATCHING-REPORT into CLI ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2807927](https://github.com/jeffrichley/agent_core/commit/2807927437d6b6500bcdb87e95097f0c6a073320))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **venv:** canonical .mcp.json generator (C2-2, [#316](https://github.com/jeffrichley/agent_core/issues/316)) ([#482](https://github.com/jeffrichley/agent_core/issues/482)) ([e376ab2](https://github.com/jeffrichley/agent_core/commit/e376ab2bf4e8130ca8654800a9b3c0034bf68dac))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* **hatchery:** make the generated backup hook able to fail ([#602](https://github.com/jeffrichley/agent_core/issues/602)) ([488edc1](https://github.com/jeffrichley/agent_core/commit/488edc1336fa6ecd5abb36485db360c6270898c3))
* **hatchery:** render config/ templates into vault ([#80](https://github.com/jeffrichley/agent_core/issues/80)) ([2c557fe](https://github.com/jeffrichley/agent_core/commit/2c557fe6eabe2cc43fef6b1c722b1dd6313db004))
* **hatchery:** ship templates inside the package ([#574](https://github.com/jeffrichley/agent_core/issues/574)) ([d339033](https://github.com/jeffrichley/agent_core/commit/d3390335f2ef1c9db82c6625a74b4507782d981e)), closes [#573](https://github.com/jeffrichley/agent_core/issues/573)
* log or justify bare except-pass swallows + test get_client factory (closes [#408](https://github.com/jeffrichley/agent_core/issues/408)) ([#471](https://github.com/jeffrichley/agent_core/issues/471)) ([e67783c](https://github.com/jeffrichley/agent_core/commit/e67783ccb4dfab6f1783d6a67223f475f81526ea))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.11.0](https://github.com/jeffrichley/agent_core/compare/agent-core-hatchery-v0.10.0...agent-core-hatchery-v0.11.0) (2026-08-05)


### Features

* **core:** endpoints.d + jobs.d conf.d-style merging ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([e385077](https://github.com/jeffrichley/agent_core/commit/e385077bfa448639680719dfa0a818f0cba048c9))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* enable mypy --strict for agent-core-hatchery ([#519](https://github.com/jeffrichley/agent_core/issues/519)) ([2365ab2](https://github.com/jeffrichley/agent_core/commit/2365ab27de4a450ec2a8c8d6211f7fdb43678a3d))
* **hatchery:** add --no-daemon-reload to hatch without a live daemon ([#483](https://github.com/jeffrichley/agent_core/issues/483)) ([7bf06c6](https://github.com/jeffrichley/agent_core/commit/7bf06c64519033ffe4b768e1bb0318282f908e77))
* **hatchery:** agent-core-hatchery package ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2cde559](https://github.com/jeffrichley/agent_core/commit/2cde55997abe5614ef0bac2154a51d95a80fef9a))
* **hatchery:** basic Hatcher orchestration (render → write → validate) ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([bca1c1d](https://github.com/jeffrichley/agent_core/commit/bca1c1d4c97d23ed01c23e22881bb64445f08de6))
* **hatchery:** channel scaffolding modules — Discord, webcam, GitHub backup ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([ccae9a4](https://github.com/jeffrichley/agent_core/commit/ccae9a4ec686af6084d2f12dc6272b2637c53f79))
* **hatchery:** cli with --config mode (Phase 2 stop) ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([19039ab](https://github.com/jeffrichley/agent_core/commit/19039ab4d4afc38309ec89411194bcb306fb6e43))
* **hatchery:** config and daemon-fragment templates ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([b895feb](https://github.com/jeffrichley/agent_core/commit/b895febe930968c6742fb1a6d055397fc9354d5f))
* **hatchery:** daemon_config writer for endpoints.d + jobs.d fragments ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([7c967a4](https://github.com/jeffrichley/agent_core/commit/7c967a43ccc659d0b18b397015fd09655582aed6))
* **hatchery:** daemon-fragment writing + parse validation in Hatcher ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2f93f97](https://github.com/jeffrichley/agent_core/commit/2f93f97d388b48bc4cd666d045ee56e40e70e233))
* **hatchery:** elder-letter manifest resolver ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([f27992f](https://github.com/jeffrichley/agent_core/commit/f27992ffb7610f73fe36e8aad3eaa6446bea2a9b))
* **hatchery:** elder-letters manifest + Pepper's bundled snapshot ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([1a15482](https://github.com/jeffrichley/agent_core/commit/1a154827c890fcc5515b0361bd156b111098fe19))
* **hatchery:** file_classes manifest loader ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([dadb2da](https://github.com/jeffrichley/agent_core/commit/dadb2dac61bb0561a1e3a445d87df0ce2e7b1050))
* **hatchery:** file-classes.yaml manifest ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([d27d0fa](https://github.com/jeffrichley/agent_core/commit/d27d0fafb88c6b4ddb2275ad9bb6db20c5385ad8))
* **hatchery:** hatch→run handoff — venv build + .mcp.json gen + daemon probe ([#410](https://github.com/jeffrichley/agent_core/issues/410)) ([04a54df](https://github.com/jeffrichley/agent_core/commit/04a54dfe4dbdb33b0187f88602d780e802879cc1))
* **hatchery:** HatchConfig pydantic model ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([bc10d62](https://github.com/jeffrichley/agent_core/commit/bc10d62c34f1b740e8a51156ac6b8e107ea2621e))
* **hatchery:** hatchery-snapshot-elders CLI ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([bbbeb85](https://github.com/jeffrichley/agent_core/commit/bbbeb85c89a5f50161c750ef6f92ac87210f7692))
* **hatchery:** HATCHING-REPORT.md generator ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([ce9b267](https://github.com/jeffrichley/agent_core/commit/ce9b2676f5211a7e028b17da5d916b449c3fc3a4))
* **hatchery:** Jinja2 renderer with StrictUndefined ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([58bd143](https://github.com/jeffrichley/agent_core/commit/58bd143825f02042ee9b88f90e6736a0f98c4586))
* **hatchery:** migrate memory templates from templates-draft ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([33659a5](https://github.com/jeffrichley/agent_core/commit/33659a5ee70e76f13c3fcc63a2920fccebdf455a))
* **hatchery:** package skeleton ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([8b1fe06](https://github.com/jeffrichley/agent_core/commit/8b1fe06b27965df86755590c8cd1a1ab2f30c20e))
* **hatchery:** Questionary TUI wizard ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([9d3db46](https://github.com/jeffrichley/agent_core/commit/9d3db46535f18df9f56bed0e63f611da7fd4d9b0))
* **hatchery:** skill-author universal skill ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([3ce3b2d](https://github.com/jeffrichley/agent_core/commit/3ce3b2d9ff08a3e17f71037e0a5c5e4094ce439e))
* **hatchery:** spawning-subagents universal skill ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([fe105cc](https://github.com/jeffrichley/agent_core/commit/fe105cc79eb0c005dcbdcf0fb14d326e730988a3))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#350](https://github.com/jeffrichley/agent_core/issues/350)) ([90141e5](https://github.com/jeffrichley/agent_core/commit/90141e595b6907a28f7b17bb6d0eccc3bf54e4d8))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **hatchery:** vault-lint universal skill ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([cf339ee](https://github.com/jeffrichley/agent_core/commit/cf339eee7fda6007d9d1dd8e5efb7a7b5850c0ca))
* **hatchery:** wire channels into daemon-fragment writing ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([f37977d](https://github.com/jeffrichley/agent_core/commit/f37977d3338036c10248bd904c9b10a043ba895c))
* **hatchery:** wire elder-letter copying into Hatcher ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([29509e4](https://github.com/jeffrichley/agent_core/commit/29509e419302688600dbf7a1bf7bebb829af2b75))
* **hatchery:** wire skill-tree copying into Hatcher ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([b5287ff](https://github.com/jeffrichley/agent_core/commit/b5287ff180b705e8eb6a26bebcc3c787cfa45213))
* **hatchery:** wire wizard + EDITOR gate + HATCHING-REPORT into CLI ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2807927](https://github.com/jeffrichley/agent_core/commit/2807927437d6b6500bcdb87e95097f0c6a073320))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **venv:** canonical .mcp.json generator (C2-2, [#316](https://github.com/jeffrichley/agent_core/issues/316)) ([#482](https://github.com/jeffrichley/agent_core/issues/482)) ([e376ab2](https://github.com/jeffrichley/agent_core/commit/e376ab2bf4e8130ca8654800a9b3c0034bf68dac))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* **hatchery:** render config/ templates into vault ([#80](https://github.com/jeffrichley/agent_core/issues/80)) ([2c557fe](https://github.com/jeffrichley/agent_core/commit/2c557fe6eabe2cc43fef6b1c722b1dd6313db004))
* **hatchery:** ship templates inside the package ([#574](https://github.com/jeffrichley/agent_core/issues/574)) ([d339033](https://github.com/jeffrichley/agent_core/commit/d3390335f2ef1c9db82c6625a74b4507782d981e)), closes [#573](https://github.com/jeffrichley/agent_core/issues/573)
* log or justify bare except-pass swallows + test get_client factory (closes [#408](https://github.com/jeffrichley/agent_core/issues/408)) ([#471](https://github.com/jeffrichley/agent_core/issues/471)) ([e67783c](https://github.com/jeffrichley/agent_core/commit/e67783ccb4dfab6f1783d6a67223f475f81526ea))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.10.0](https://github.com/jeffrichley/agent_core/compare/v0.9.1...agent-core-hatchery-v0.10.0) (2026-08-04)


### Features

* enable mypy --strict for agent-core-hatchery ([#519](https://github.com/jeffrichley/agent_core/issues/519)) ([2365ab2](https://github.com/jeffrichley/agent_core/commit/2365ab27de4a450ec2a8c8d6211f7fdb43678a3d))
* **hatchery:** add --no-daemon-reload to hatch without a live daemon ([#483](https://github.com/jeffrichley/agent_core/issues/483)) ([7bf06c6](https://github.com/jeffrichley/agent_core/commit/7bf06c64519033ffe4b768e1bb0318282f908e77))
* **venv:** canonical .mcp.json generator (C2-2, [#316](https://github.com/jeffrichley/agent_core/issues/316)) ([#482](https://github.com/jeffrichley/agent_core/issues/482)) ([e376ab2](https://github.com/jeffrichley/agent_core/commit/e376ab2bf4e8130ca8654800a9b3c0034bf68dac))


### Bug Fixes

* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* **hatchery:** ship templates inside the package ([#574](https://github.com/jeffrichley/agent_core/issues/574)) ([d339033](https://github.com/jeffrichley/agent_core/commit/d3390335f2ef1c9db82c6625a74b4507782d981e)), closes [#573](https://github.com/jeffrichley/agent_core/issues/573)

## [0.9.1](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-hatchery-v0.9.1) (2026-08-04)


### Bug Fixes

* **hatchery:** ship templates inside the package ([#574](https://github.com/jeffrichley/agent_core/issues/574)) ([d339033](https://github.com/jeffrichley/agent_core/commit/d3390335f2ef1c9db82c6625a74b4507782d981e)), closes [#573](https://github.com/jeffrichley/agent_core/issues/573)

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/v0.8.2...agent-core-hatchery-v0.9.0) (2026-08-03)


### Features

* enable mypy --strict for agent-core-hatchery ([#519](https://github.com/jeffrichley/agent_core/issues/519)) ([2365ab2](https://github.com/jeffrichley/agent_core/commit/2365ab27de4a450ec2a8c8d6211f7fdb43678a3d))
* **hatchery:** add --no-daemon-reload to hatch without a live daemon ([#483](https://github.com/jeffrichley/agent_core/issues/483)) ([7bf06c6](https://github.com/jeffrichley/agent_core/commit/7bf06c64519033ffe4b768e1bb0318282f908e77))
* **venv:** canonical .mcp.json generator (C2-2, [#316](https://github.com/jeffrichley/agent_core/issues/316)) ([#482](https://github.com/jeffrichley/agent_core/issues/482)) ([e376ab2](https://github.com/jeffrichley/agent_core/commit/e376ab2bf4e8130ca8654800a9b3c0034bf68dac))

## [0.8.2](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-hatchery-v0.8.2) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-hatchery-v0.8.1) (2026-07-22)


### Bug Fixes

* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-hatchery-v0.8.0) (2026-07-21)


### Features

* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** hatch→run handoff — venv build + .mcp.json gen + daemon probe ([#410](https://github.com/jeffrichley/agent_core/issues/410)) ([04a54df](https://github.com/jeffrichley/agent_core/commit/04a54dfe4dbdb33b0187f88602d780e802879cc1))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#350](https://github.com/jeffrichley/agent_core/issues/350)) ([90141e5](https://github.com/jeffrichley/agent_core/commit/90141e595b6907a28f7b17bb6d0eccc3bf54e4d8))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* log or justify bare except-pass swallows + test get_client factory (closes [#408](https://github.com/jeffrichley/agent_core/issues/408)) ([#471](https://github.com/jeffrichley/agent_core/issues/471)) ([e67783c](https://github.com/jeffrichley/agent_core/commit/e67783ccb4dfab6f1783d6a67223f475f81526ea))
