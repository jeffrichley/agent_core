# Changelog

## [0.10.0](https://github.com/jeffrichley/agent_core/compare/v0.9.3...agent-core-v0.10.0) (2026-08-05)


### Features

* **#64:** wire discord-pepper file-attachment path through to upload ([9f76846](https://github.com/jeffrichley/agent_core/commit/9f76846c63c492f07ef802d89f91939319a7b668))
* **bus:** add ASGI auth middleware with bus_auth_mode (off/warn/enforce) ([#544](https://github.com/jeffrichley/agent_core/issues/544)) ([d925501](https://github.com/jeffrichley/agent_core/commit/d9255013a0d50a8e694d0ba98e601970827fd3cf))
* **bus:** add backup/restore — VACUUM INTO snapshots, retention, CLI ([#415](https://github.com/jeffrichley/agent_core/issues/415)) ([0a18681](https://github.com/jeffrichley/agent_core/commit/0a18681efa5fe444f716b46300f74c24eee690ba))
* **bus:** add BusHandle.spawn() tracked-task API ([#290](https://github.com/jeffrichley/agent_core/issues/290)) ([#294](https://github.com/jeffrichley/agent_core/issues/294)) ([d0b0791](https://github.com/jeffrichley/agent_core/commit/d0b0791ef7b56783de5c44376eecf0bb739ce202))
* **bus:** add delivery retry backoff with next_attempt_at (T5) ([#285](https://github.com/jeffrichley/agent_core/issues/285)) ([2169071](https://github.com/jeffrichley/agent_core/commit/21690717295e85327e6dff868d34ee8e7515095e))
* **bus:** add EndpointSupervisor + circuit-breaker state machine ([#292](https://github.com/jeffrichley/agent_core/issues/292)) ([d1f3fb1](https://github.com/jeffrichley/agent_core/commit/d1f3fb14478c44d491117912f56c7f1e82cda3a7))
* **bus:** add peek(envelope_id) MCP tool ([#70](https://github.com/jeffrichley/agent_core/issues/70)) ([71e5b51](https://github.com/jeffrichley/agent_core/commit/71e5b516dc2763cecdf4d954b43e06a280ad6415))
* **bus:** add portable liveness watchdog (heartbeat + self-terminate) ([#343](https://github.com/jeffrichley/agent_core/issues/343)) ([628ffdb](https://github.com/jeffrichley/agent_core/commit/628ffdb418d126c95305dd54eb5cb0d062b74f38))
* **bus:** add PubkeyRegistry loader for being → Ed25519 pubkey (Dβ-2a) ([#533](https://github.com/jeffrichley/agent_core/issues/533)) ([20e2f91](https://github.com/jeffrichley/agent_core/commit/20e2f91ae3eb766065964d260dc0e8eb372f4685))
* **bus:** add pydantic daemon-config schema and real validate_config ([#422](https://github.com/jeffrichley/agent_core/issues/422)) ([e3e1a8d](https://github.com/jeffrichley/agent_core/commit/e3e1a8d6c3555d2bcfc2394f59d91508d997633e))
* **bus:** add SupervisorConfig block to BusConfig with boot logging ([#279](https://github.com/jeffrichley/agent_core/issues/279)) ([97a8522](https://github.com/jeffrichley/agent_core/commit/97a8522627205efcad41c19c7657f5b8ca48848d))
* **bus:** degraded boot, wire supervisor, state-change events, bus status ([#313](https://github.com/jeffrichley/agent_core/issues/313)) ([24a8eb4](https://github.com/jeffrichley/agent_core/commit/24a8eb452bef85f1955f0f3a6df2483e395bee53))
* **bus:** offload VoiceEndpoint construction to start() and add slow-deliver watchdog ([#331](https://github.com/jeffrichley/agent_core/issues/331)) ([57d98f5](https://github.com/jeffrichley/agent_core/commit/57d98f546d34afa250b69cc57256e2220a263228))
* **bus:** per-being config-fragment isolation + degraded load + migrate Pepper ([#381](https://github.com/jeffrichley/agent_core/issues/381)) ([808a271](https://github.com/jeffrichley/agent_core/commit/808a2713c74d0216f523fa4fe79b6d4555de8d83))
* canonical skills library + scheduler as first entry ([#134](https://github.com/jeffrichley/agent_core/issues/134)) ([6859010](https://github.com/jeffrichley/agent_core/commit/6859010359db3379550373eaea1dcb0d6a00b879))
* **core:** endpoints.d + jobs.d conf.d-style merging ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([e385077](https://github.com/jeffrichley/agent_core/commit/e385077bfa448639680719dfa0a818f0cba048c9))
* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **core:** jobs.d conf.d-style merging in scheduler ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([7221566](https://github.com/jeffrichley/agent_core/commit/7221566f65b8a1ce31653d78fee55e04205eef03))
* **core:** replace hardcoded __version__ with importlib.metadata lookup ([#342](https://github.com/jeffrichley/agent_core/issues/342)) ([c0e5238](https://github.com/jeffrichley/agent_core/commit/c0e52386ca3d575845310f2a9a1e3ef6439b53cc))
* **core:** wake-stats analyzer + CLI subcommand ([#70](https://github.com/jeffrichley/agent_core/issues/70)) ([8bf61c5](https://github.com/jeffrichley/agent_core/commit/8bf61c584b24135e7b3724096cc931ee3f41a7d2))
* **daemon:** _daemon_python() helper with sys.executable fallback ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([cf4d334](https://github.com/jeffrichley/agent_core/commit/cf4d3348ca665e97e2700bfbb7aa4de2ee153e54))
* **daemon:** add config hygiene / drift detection to daemon doctor (Cα-3) ([#379](https://github.com/jeffrichley/agent_core/issues/379)) ([38ae9c9](https://github.com/jeffrichley/agent_core/commit/38ae9c98ce3c617be8609cc9ca3973ac1a39821c))
* **daemon:** add cross-platform autostart framework for Linux and macOS ([#340](https://github.com/jeffrichley/agent_core/issues/340)) ([b1d93d8](https://github.com/jeffrichley/agent_core/commit/b1d93d880848332f411e6190863cb2755505af95))
* **daemon:** add venv GC report engine and detectors to daemon doctor ([#540](https://github.com/jeffrichley/agent_core/issues/540)) ([0c30c78](https://github.com/jeffrichley/agent_core/commit/0c30c787b56e0f262f2464a7e93550869904f12b))
* **daemon:** add Windows Service headless autostart with unbounded restart ([#337](https://github.com/jeffrichley/agent_core/issues/337)) ([5b1b8df](https://github.com/jeffrichley/agent_core/commit/5b1b8dff47a69cf7aa8d160a9dd0b36a7fb73316))
* **daemon:** build_uv_sync_command pure builder ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([5d6531c](https://github.com/jeffrichley/agent_core/commit/5d6531c5bc7ba36338d6e8fac0c12b930aef19a6))
* **daemon:** daemon install CLI command ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([255b1d5](https://github.com/jeffrichley/agent_core/commit/255b1d554520fa57a922bdcd7a606efb6184022e))
* **daemon:** daemon refresh CLI command ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([d7512d4](https://github.com/jeffrichley/agent_core/commit/d7512d4cfbada458254fbce0b7a207e84446ef8a))
* **daemon:** dev/prod daemon instance-parameterization (Phase 3) ([#108](https://github.com/jeffrichley/agent_core/issues/108)) ([16c4cc6](https://github.com/jeffrichley/agent_core/commit/16c4cc69d719d0fb69c4d1ad080c47c10a2082eb))
* **daemon:** install stamp file read/write ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([26662aa](https://github.com/jeffrichley/agent_core/commit/26662aaedefb4e34c81009b6f79826ad03d66e5d))
* **daemon:** run_install orchestrator (uv venv + uv sync + stamp) ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([1713c33](https://github.com/jeffrichley/agent_core/commit/1713c337c0acba86f35c2e3d2ffb0dca563de76f))
* **daemon:** status diagnostics — fallback warning, stamp display, lock drift ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([28113a0](https://github.com/jeffrichley/agent_core/commit/28113a01df1a4fcbf4004b325c655d233adcafc1))
* **daemon:** status surfaces installed version next to installed sha ([e5ac29d](https://github.com/jeffrichley/agent_core/commit/e5ac29d1b4270ba9cd505525c05cdab3c41cdddd))
* **daemon:** three-instance model — prod / source / test (Phase 3.5) ([#120](https://github.com/jeffrichley/agent_core/issues/120)) ([5301505](https://github.com/jeffrichley/agent_core/commit/5301505e312fc905637932574ffc89b908336222))
* **daemon:** venv isolation from workspace uv sync ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([834a7c9](https://github.com/jeffrichley/agent_core/commit/834a7c90a8806c1a7540278e7df37af08ab01ae2))
* **daemon:** windows daemon auto-start (Phase 4) ([#110](https://github.com/jeffrichley/agent_core/issues/110)) ([c819b5d](https://github.com/jeffrichley/agent_core/commit/c819b5de19ff1a385cbb50b0f3a334effa814460))
* **daemon:** wire --fix to remove dead central corpse venvs (C2-3c) ([#550](https://github.com/jeffrichley/agent_core/issues/550)) ([3ee03b7](https://github.com/jeffrichley/agent_core/commit/3ee03b7b601acce0eea0c31013edc4d0c0d6cd16))
* **daemon:** workspace root discovery for install command ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([01e26bc](https://github.com/jeffrichley/agent_core/commit/01e26bc2b7f26ac5adc10f446c2c6f18cbd36a6f))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* envelope extension hookspec — content-agnostic plugin seam for new kinds + renderers ([#124](https://github.com/jeffrichley/agent_core/issues/124)) ([ad4e166](https://github.com/jeffrichley/agent_core/commit/ad4e16686448f34f95e0203c8abccc79819163c8))
* **envelope:** FileAttachment model with required path ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([0c3b1fc](https://github.com/jeffrichley/agent_core/commit/0c3b1fc8ebf03f1c1529f7ac6129459314bb9711))
* **envelope:** tighten TextMessagePayload.attachments to list[FileAttachment] ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([c1688d9](https://github.com/jeffrichley/agent_core/commit/c1688d9d37c0ae3ffa554d595b3730803d21e6b0))
* **githooks:** install_git_hooks pure function + unit tests ([597d436](https://github.com/jeffrichley/agent_core/commit/597d436f809eb359ca87d7b75186b77fe0be352c))
* **githooks:** local guard test catches missing core.hooksPath per worktree ([f12496d](https://github.com/jeffrichley/agent_core/commit/f12496d08133d9d65c9dce6de8cec17502c182fd))
* **githooks:** version-controlled pre-push hook runs just check ([099c03c](https://github.com/jeffrichley/agent_core/commit/099c03cd6c573bab1c9646b5e64b02829ced8074))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **inbound:** inbound notifications v1.a — GitHub → Wren via Tailscale Funnel ([#196](https://github.com/jeffrichley/agent_core/issues/196)) ([d180b73](https://github.com/jeffrichley/agent_core/commit/d180b73fe8981bc8cc7add7342800a3a8291d23d))
* inline-content wake via relay-side prefetch ([#70](https://github.com/jeffrichley/agent_core/issues/70)) ([eb347b0](https://github.com/jeffrichley/agent_core/commit/eb347b090a4c95e380a06ba2c6bc03dcb95b5c56))
* **logging:** add structured JSON logging and correlation-id contextvar ([#462](https://github.com/jeffrichley/agent_core/issues/462)) ([b3397a2](https://github.com/jeffrichley/agent_core/commit/b3397a2b08aa2b083d26ba9d230bacfa07a23641))
* mypy --strict for agent-core-discord + log CancelledError swallows (closes [#444](https://github.com/jeffrichley/agent_core/issues/444)) ([#470](https://github.com/jeffrichley/agent_core/issues/470)) ([3ca9419](https://github.com/jeffrichley/agent_core/commit/3ca94193c98d547acfe2cd5203b314cb134656d4))
* **qa:** session-scoped auto-start daemon fixture, replace skip-unless-live autouse ([#534](https://github.com/jeffrichley/agent_core/issues/534)) ([407c387](https://github.com/jeffrichley/agent_core/commit/407c3874a0636641b0b546a4c262408c839973d3))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **release:** Phase 2.5 — release-artifact deploy + bug cleanup ([#102](https://github.com/jeffrichley/agent_core/issues/102)) ([079e493](https://github.com/jeffrichley/agent_core/commit/079e4930dc1bcab620dcadf226273dd1bc1be0f7))
* **release:** phase 2.6 — end-to-end install validation (closes 3 release-pipeline bugs) ([#121](https://github.com/jeffrichley/agent_core/issues/121)) ([ce8ee59](https://github.com/jeffrichley/agent_core/commit/ce8ee5971acb46d64944f3df3149249214dabcf8))
* **releases:** root towncrier config + aggregated changelog.d/&lt;pkg&gt;/ layout ([5910685](https://github.com/jeffrichley/agent_core/commit/591068555671b68258df224de7a186702968dfeb))
* **secrets:** add vault-API accessor and scrub subprocess env ([#399](https://github.com/jeffrichley/agent_core/issues/399)) ([adb404b](https://github.com/jeffrichley/agent_core/commit/adb404b046bd224b56d64188b2cb56eb134d99b7))
* **venv:** add per-being pinned venv builder + absolute uv resolution ([#365](https://github.com/jeffrichley/agent_core/issues/365)) ([07ece11](https://github.com/jeffrichley/agent_core/commit/07ece113ddea8b111dece79588a74fc840864aea)), closes [#315](https://github.com/jeffrichley/agent_core/issues/315)
* **venv:** canonical .mcp.json generator (C2-2, [#316](https://github.com/jeffrichley/agent_core/issues/316)) ([#482](https://github.com/jeffrichley/agent_core/issues/482)) ([e376ab2](https://github.com/jeffrichley/agent_core/commit/e376ab2bf4e8130ca8654800a9b3c0034bf68dac))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **bus:** install signal handlers before announcing readiness (deflake test_cli_run) ([#283](https://github.com/jeffrichley/agent_core/issues/283)) ([08fd821](https://github.com/jeffrichley/agent_core/commit/08fd8214a7958a921d0a6216ced2a1e0183d40ca))
* **ci:** set TERMINAL_WIDTH for check job; drop ineffective per-test COLUMNS ([a9a11ca](https://github.com/jeffrichley/agent_core/commit/a9a11ca2b4b3cfcb6e331ded9dbaac47212b9ae2))
* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **core:** resolve two pre-existing mypy errors ([f066784](https://github.com/jeffrichley/agent_core/commit/f06678442f3f9e64ef43af1a614063bcbedd017d))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **daemon:** datetime.UTC alias; test uv venv non-zero exit (T5 review) ([b235d9d](https://github.com/jeffrichley/agent_core/commit/b235d9dca61231697d956abebd7e77fbf53fa0c6))
* **daemon:** handle CalledProcessError; use cwd for workspace discovery (T6 review) ([e8ec21e](https://github.com/jeffrichley/agent_core/commit/e8ec21ef12d26d9ea65bbdd692ae628bcec07937))
* **daemon:** test TOMLDecodeError branch; align error class with Exception (T2 review) ([0f9e0b7](https://github.com/jeffrichley/agent_core/commit/0f9e0b77308901a0dd6802dec379fa5df90c7af0))
* **daemon:** uv venv --clear so install works on uv&gt;=0.10 (CI + future prod) ([7ca37e3](https://github.com/jeffrichley/agent_core/commit/7ca37e341fa3858cfe2a3c0d80c9e68e0e271ef7))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* log or justify bare except-pass swallows + test get_client factory (closes [#408](https://github.com/jeffrichley/agent_core/issues/408)) ([#471](https://github.com/jeffrichley/agent_core/issues/471)) ([e67783c](https://github.com/jeffrichley/agent_core/commit/e67783ccb4dfab6f1783d6a67223f475f81526ea))
* **packaging:** force-include agent_core.venv in all build targets ([#569](https://github.com/jeffrichley/agent_core/issues/569)) ([345cad0](https://github.com/jeffrichley/agent_core/commit/345cad077ac1ea3d1ff540046e46c37c4e1d5007)), closes [#566](https://github.com/jeffrichley/agent_core/issues/566)
* **packaging:** force-include agent_core.venv in the wheel ([#567](https://github.com/jeffrichley/agent_core/issues/567)) ([89aabf1](https://github.com/jeffrichley/agent_core/commit/89aabf11a91fea93391941a09ed7190ccf925f91)), closes [#566](https://github.com/jeffrichley/agent_core/issues/566)
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* **reply:** strip inbound-only Discord metadata keys in reply() and escalate Unrecognized-shape ack urgency ([#224](https://github.com/jeffrichley/agent_core/issues/224)) ([ca550c5](https://github.com/jeffrichley/agent_core/commit/ca550c5691b4a3228d0b57f46bff474cd543b143))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))
* **scheduler:** dispose aiosqlite engine on stop + guard against connection leaks ([#468](https://github.com/jeffrichley/agent_core/issues/468)) ([d14421f](https://github.com/jeffrichley/agent_core/commit/d14421f769b62566b8759a541e401ea050c81c44))
* **test:** update bus-tail summary fixture for tightened FileAttachment schema ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([581c8ec](https://github.com/jeffrichley/agent_core/commit/581c8eca8a883c8d15a099db07b851cde66fb13d))

## [0.9.3](https://github.com/jeffrichley/agent_core/compare/v0.9.2...agent-core-v0.9.3) (2026-08-04)


### Bug Fixes

* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))

## [0.9.2](https://github.com/jeffrichley/agent_core/compare/v0.9.1...agent-core-v0.9.2) (2026-08-04)


### Bug Fixes

* **packaging:** force-include agent_core.venv in all build targets ([#569](https://github.com/jeffrichley/agent_core/issues/569)) ([345cad0](https://github.com/jeffrichley/agent_core/commit/345cad077ac1ea3d1ff540046e46c37c4e1d5007)), closes [#566](https://github.com/jeffrichley/agent_core/issues/566)

## [0.9.1](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-v0.9.1) (2026-08-04)


### Bug Fixes

* **packaging:** force-include agent_core.venv in the wheel ([#567](https://github.com/jeffrichley/agent_core/issues/567)) ([89aabf1](https://github.com/jeffrichley/agent_core/commit/89aabf11a91fea93391941a09ed7190ccf925f91)), closes [#566](https://github.com/jeffrichley/agent_core/issues/566)

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/v0.8.2...agent-core-v0.9.0) (2026-08-03)


### Features

* **bus:** add ASGI auth middleware with bus_auth_mode (off/warn/enforce) ([#544](https://github.com/jeffrichley/agent_core/issues/544)) ([d925501](https://github.com/jeffrichley/agent_core/commit/d9255013a0d50a8e694d0ba98e601970827fd3cf))
* **bus:** add PubkeyRegistry loader for being → Ed25519 pubkey (Dβ-2a) ([#533](https://github.com/jeffrichley/agent_core/issues/533)) ([20e2f91](https://github.com/jeffrichley/agent_core/commit/20e2f91ae3eb766065964d260dc0e8eb372f4685))
* **daemon:** add venv GC report engine and detectors to daemon doctor ([#540](https://github.com/jeffrichley/agent_core/issues/540)) ([0c30c78](https://github.com/jeffrichley/agent_core/commit/0c30c787b56e0f262f2464a7e93550869904f12b))
* **daemon:** wire --fix to remove dead central corpse venvs (C2-3c) ([#550](https://github.com/jeffrichley/agent_core/issues/550)) ([3ee03b7](https://github.com/jeffrichley/agent_core/commit/3ee03b7b601acce0eea0c31013edc4d0c0d6cd16))
* **qa:** session-scoped auto-start daemon fixture, replace skip-unless-live autouse ([#534](https://github.com/jeffrichley/agent_core/issues/534)) ([407c387](https://github.com/jeffrichley/agent_core/commit/407c3874a0636641b0b546a4c262408c839973d3))
* **venv:** canonical .mcp.json generator (C2-2, [#316](https://github.com/jeffrichley/agent_core/issues/316)) ([#482](https://github.com/jeffrichley/agent_core/issues/482)) ([e376ab2](https://github.com/jeffrichley/agent_core/commit/e376ab2bf4e8130ca8654800a9b3c0034bf68dac))

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
