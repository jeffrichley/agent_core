:robot: I have created a release *beep* *boop*
---


<details><summary>0.10.0</summary>

## [0.10.0](https://github.com/jeffrichley/agent_core/compare/v0.9.5...v0.10.0) (2026-08-17)


### Features

* **bus:** complete non-loopback bind gate wiring for issue [#505](https://github.com/jeffrichley/agent_core/issues/505) ([#588](https://github.com/jeffrichley/agent_core/issues/588)) ([37d74c8](https://github.com/jeffrichley/agent_core/commit/37d74c8c4b4c26edc992e4bd13b62a1d3cbccdb8))
* **daemon:** add --fix pruning for superseded venvs, broken links, partial builds, drifted .mcp.json ([#554](https://github.com/jeffrichley/agent_core/issues/554)) ([c7e87ba](https://github.com/jeffrichley/agent_core/commit/c7e87ba879f57c6630fc4c816e1d559383effb94))


### Bug Fixes

* **hatchery:** make the generated backup hook able to fail ([#602](https://github.com/jeffrichley/agent_core/issues/602)) ([488edc1](https://github.com/jeffrichley/agent_core/commit/488edc1336fa6ecd5abb36485db360c6270898c3))
* **presence:** a dead sensor must not read as a quiet one ([#609](https://github.com/jeffrichley/agent_core/issues/609)) ([0414d19](https://github.com/jeffrichley/agent_core/commit/0414d19c1153f02cc05aa252b99cf8befc6ca1df))
* **qa:** gate daemon readiness on a tool that needs the started handle ([#590](https://github.com/jeffrichley/agent_core/issues/590)) ([b601f65](https://github.com/jeffrichley/agent_core/commit/b601f6568f26638413bdf1c8a69d2b5b9c453c24))
* **scheduler:** open the SQLite store in WAL mode with a busy timeout ([#587](https://github.com/jeffrichley/agent_core/issues/587)) ([d3a3fec](https://github.com/jeffrichley/agent_core/commit/d3a3fec002e98aae1131371f994f57662bf8870b)), closes [#585](https://github.com/jeffrichley/agent_core/issues/585) [#586](https://github.com/jeffrichley/agent_core/issues/586)
* **scheduler:** report a dead scheduler to the bus instead of failing silently ([#589](https://github.com/jeffrichley/agent_core/issues/589)) ([8d1e77c](https://github.com/jeffrichley/agent_core/commit/8d1e77ce00102d5aa94de3169dbda9418cfd965d)), closes [#586](https://github.com/jeffrichley/agent_core/issues/586)
</details>

<details><summary>agent-core: 0.11.0</summary>

## [0.11.0](https://github.com/jeffrichley/agent_core/compare/agent-core-v0.10.0...agent-core-v0.11.0) (2026-08-17)


### Features

* **#64:** wire discord-pepper file-attachment path through to upload ([9f76846](https://github.com/jeffrichley/agent_core/commit/9f76846c63c492f07ef802d89f91939319a7b668))
* **bus:** add ASGI auth middleware with bus_auth_mode (off/warn/enforce) ([#544](https://github.com/jeffrichley/agent_core/issues/544)) ([d925501](https://github.com/jeffrichley/agent_core/commit/d9255013a0d50a8e694d0ba98e601970827fd3cf))
* **bus:** add backup/restore  VACUUM INTO snapshots, retention, CLI ([#415](https://github.com/jeffrichley/agent_core/issues/415)) ([0a18681](https://github.com/jeffrichley/agent_core/commit/0a18681efa5fe444f716b46300f74c24eee690ba))
* **bus:** add BusHandle.spawn() tracked-task API ([#290](https://github.com/jeffrichley/agent_core/issues/290)) ([#294](https://github.com/jeffrichley/agent_core/issues/294)) ([d0b0791](https://github.com/jeffrichley/agent_core/commit/d0b0791ef7b56783de5c44376eecf0bb739ce202))
* **bus:** add delivery retry backoff with next_attempt_at (T5) ([#285](https://github.com/jeffrichley/agent_core/issues/285)) ([2169071](https://github.com/jeffrichley/agent_core/commit/21690717295e85327e6dff868d34ee8e7515095e))
* **bus:** add EndpointSupervisor + circuit-breaker state machine ([#292](https://github.com/jeffrichley/agent_core/issues/292)) ([d1f3fb1](https://github.com/jeffrichley/agent_core/commit/d1f3fb14478c44d491117912f56c7f1e82cda3a7))
* **bus:** add portable liveness watchdog (heartbeat + self-terminate) ([#343](https://github.com/jeffrichley/agent_core/issues/343)) ([628ffdb](https://github.com/jeffrichley/agent_core/commit/628ffdb418d126c95305dd54eb5cb0d062b74f38))
* **bus:** add PubkeyRegistry loader for being ’ Ed25519 pubkey (D²-2a) ([#533](https://github.com/jeffrichley/agent_core/issues/533)) ([20e2f91](https://github.com/jeffrichley/agent_core/commit/20e2f91ae3eb766065964d260dc0e8eb372f4685))
* **bus:** add pydantic daemon-config schema and real validate_config ([#422](https://github.com/jeffrichley/agent_core/issues/422)) ([e3e1a8d](https://github.com/jeffrichley/agent_core/commit/e3e1a8d6c3555d2bcfc2394f59d91508d997633e))
* **bus:** add SupervisorConfig block to BusConfig with boot logging ([#279](https://github.com/jeffrichley/agent_core/issues/279)) ([97a8522](https://github.com/jeffrichley/agent_core/commit/97a8522627205efcad41c19c7657f5b8ca48848d))
* **bus:** complete non-loopback bind gate wiring for issue [#505](https://github.com/jeffrichley/agent_core/issues/505) ([#588](https://github.com/jeffrichley/agent_core/issues/588)) ([37d74c8](https://github.com/jeffrichley/agent_core/commit/37d74c8c4b4c26edc992e4bd13b62a1d3cbccdb8))
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
* **daemon:** add --fix pruning for superseded venvs, broken links, partial builds, drifted .mcp.json ([#554](https://github.com/jeffrichley/agent_core/issues/554)) ([c7e87ba](https://github.com/jeffrichley/agent_core/commit/c7e87ba879f57c6630fc4c816e1d559383effb94))
* **daemon:** add config hygiene / drift detection to daemon doctor (C±-3) ([#379](https://github.com/jeffrichley/agent_core/issues/379)) ([38ae9c9](https://github.com/jeffrichley/agent_core/commit/38ae9c98ce3c617be8609cc9ca3973ac1a39821c))
* **daemon:** add cross-platform autostart framework for Linux and macOS ([#340](https://github.com/jeffrichley/agent_core/issues/340)) ([b1d93d8](https://github.com/jeffrichley/agent_core/commit/b1d93d880848332f411e6190863cb2755505af95))
* **daemon:** add venv GC report engine and detectors to daemon doctor ([#540](https://github.com/jeffrichley/agent_core/issues/540)) ([0c30c78](https://github.com/jeffrichley/agent_core/commit/0c30c787b56e0f262f2464a7e93550869904f12b))
* **daemon:** add Windows Service headless autostart with unbounded restart ([#337](https://github.com/jeffrichley/agent_core/issues/337)) ([5b1b8df](https://github.com/jeffrichley/agent_core/commit/5b1b8dff47a69cf7aa8d160a9dd0b36a7fb73316))
* **daemon:** build_uv_sync_command pure builder ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([5d6531c](https://github.com/jeffrichley/agent_core/commit/5d6531c5bc7ba36338d6e8fac0c12b930aef19a6))
* **daemon:** daemon install CLI command ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([255b1d5](https://github.com/jeffrichley/agent_core/commit/255b1d554520fa57a922bdcd7a606efb6184022e))
* **daemon:** daemon refresh CLI command ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([d7512d4](https://github.com/jeffrichley/agent_core/commit/d7512d4cfbada458254fbce0b7a207e84446ef8a))
* **daemon:** dev/prod daemon instance-parameterization (Phase 3) ([#108](https://github.com/jeffrichley/agent_core/issues/108)) ([16c4cc6](https://github.com/jeffrichley/agent_core/commit/16c4cc69d719d0fb69c4d1ad080c47c10a2082eb))
* **daemon:** install stamp file read/write ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([26662aa](https://github.com/jeffrichley/agent_core/commit/26662aaedefb4e34c81009b6f79826ad03d66e5d))
* **daemon:** run_install orchestrator (uv venv + uv sync + stamp) ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([1713c33](https://github.com/jeffrichley/agent_core/commit/1713c337c0acba86f35c2e3d2ffb0dca563de76f))
* **daemon:** status diagnostics  fallback warning, stamp display, lock drift ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([28113a0](https://github.com/jeffrichley/agent_core/commit/28113a01df1a4fcbf4004b325c655d233adcafc1))
* **daemon:** status surfaces installed version next to installed sha ([e5ac29d](https://github.com/jeffrichley/agent_core/commit/e5ac29d1b4270ba9cd505525c05cdab3c41cdddd))
* **daemon:** three-instance model  prod / source / test (Phase 3.5) ([#120](https://github.com/jeffrichley/agent_core/issues/120)) ([5301505](https://github.com/jeffrichley/agent_core/commit/5301505e312fc905637932574ffc89b908336222))
* **daemon:** venv isolation from workspace uv sync ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([834a7c9](https://github.com/jeffrichley/agent_core/commit/834a7c90a8806c1a7540278e7df37af08ab01ae2))
* **daemon:** windows daemon auto-start (Phase 4) ([#110](https://github.com/jeffrichley/agent_core/issues/110)) ([c819b5d](https://github.com/jeffrichley/agent_core/commit/c819b5de19ff1a385cbb50b0f3a334effa814460))
* **daemon:** wire --fix to remove dead central corpse venvs (C2-3c) ([#550](https://github.com/jeffrichley/agent_core/issues/550)) ([3ee03b7](https://github.com/jeffrichley/agent_core/commit/3ee03b7b601acce0eea0c31013edc4d0c0d6cd16))
* **daemon:** workspace root discovery for install command ([#79](https://github.com/jeffrichley/agent_core/issues/79)) ([01e26bc](https://github.com/jeffrichley/agent_core/commit/01e26bc2b7f26ac5adc10f446c2c6f18cbd36a6f))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* envelope extension hookspec  content-agnostic plugin seam for new kinds + renderers ([#124](https://github.com/jeffrichley/agent_core/issues/124)) ([ad4e166](https://github.com/jeffrichley/agent_core/commit/ad4e16686448f34f95e0203c8abccc79819163c8))
* **envelope:** FileAttachment model with required path ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([0c3b1fc](https://github.com/jeffrichley/agent_core/commit/0c3b1fc8ebf03f1c1529f7ac6129459314bb9711))
* **envelope:** tighten TextMessagePayload.attachments to list[FileAttachment] ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([c1688d9](https://github.com/jeffrichley/agent_core/commit/c1688d9d37c0ae3ffa554d595b3730803d21e6b0))
* **githooks:** install_git_hooks pure function + unit tests ([597d436](https://github.com/jeffrichley/agent_core/commit/597d436f809eb359ca87d7b75186b77fe0be352c))
* **githooks:** local guard test catches missing core.hooksPath per worktree ([f12496d](https://github.com/jeffrichley/agent_core/commit/f12496d08133d9d65c9dce6de8cec17502c182fd))
* **githooks:** version-controlled pre-push hook runs just check ([099c03c](https://github.com/jeffrichley/agent_core/commit/099c03cd6c573bab1c9646b5e64b02829ced8074))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **inbound:** inbound notifications v1.a  GitHub ’ Wren via Tailscale Funnel ([#196](https://github.com/jeffrichley/agent_core/issues/196)) ([d180b73](https://github.com/jeffrichley/agent_core/commit/d180b73fe8981bc8cc7add7342800a3a8291d23d))
* inline-content wake via relay-side prefetch ([#70](https://github.com/jeffrichley/agent_core/issues/70)) ([eb347b0](https://github.com/jeffrichley/agent_core/commit/eb347b090a4c95e380a06ba2c6bc03dcb95b5c56))
* **logging:** add structured JSON logging and correlation-id contextvar ([#462](https://github.com/jeffrichley/agent_core/issues/462)) ([b3397a2](https://github.com/jeffrichley/agent_core/commit/b3397a2b08aa2b083d26ba9d230bacfa07a23641))
* mypy --strict for agent-core-discord + log CancelledError swallows (closes [#444](https://github.com/jeffrichley/agent_core/issues/444)) ([#470](https://github.com/jeffrichley/agent_core/issues/470)) ([3ca9419](https://github.com/jeffrichley/agent_core/commit/3ca94193c98d547acfe2cd5203b314cb134656d4))
* **qa:** session-scoped auto-start daemon fixture, replace skip-unless-live autouse ([#534](https://github.com/jeffrichley/agent_core/issues/534)) ([407c387](https://github.com/jeffrichley/agent_core/commit/407c3874a0636641b0b546a4c262408c839973d3))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **release:** Phase 2.5  release-artifact deploy + bug cleanup ([#102](https://github.com/jeffrichley/agent_core/issues/102)) ([079e493](https://github.com/jeffrichley/agent_core/commit/079e4930dc1bcab620dcadf226273dd1bc1be0f7))
* **release:** phase 2.6  end-to-end install validation (closes 3 release-pipeline bugs) ([#121](https://github.com/jeffrichley/agent_core/issues/121)) ([ce8ee59](https://github.com/jeffrichley/agent_core/commit/ce8ee5971acb46d64944f3df3149249214dabcf8))
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
* **qa:** gate daemon readiness on a tool that needs the started handle ([#590](https://github.com/jeffrichley/agent_core/issues/590)) ([b601f65](https://github.com/jeffrichley/agent_core/commit/b601f6568f26638413bdf1c8a69d2b5b9c453c24))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* **reply:** strip inbound-only Discord metadata keys in reply() and escalate Unrecognized-shape ack urgency ([#224](https://github.com/jeffrichley/agent_core/issues/224)) ([ca550c5](https://github.com/jeffrichley/agent_core/commit/ca550c5691b4a3228d0b57f46bff474cd543b143))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))
* **scheduler:** dispose aiosqlite engine on stop + guard against connection leaks ([#468](https://github.com/jeffrichley/agent_core/issues/468)) ([d14421f](https://github.com/jeffrichley/agent_core/commit/d14421f769b62566b8759a541e401ea050c81c44))
* **scheduler:** open the SQLite store in WAL mode with a busy timeout ([#587](https://github.com/jeffrichley/agent_core/issues/587)) ([d3a3fec](https://github.com/jeffrichley/agent_core/commit/d3a3fec002e98aae1131371f994f57662bf8870b)), closes [#585](https://github.com/jeffrichley/agent_core/issues/585) [#586](https://github.com/jeffrichley/agent_core/issues/586)
* **scheduler:** report a dead scheduler to the bus instead of failing silently ([#589](https://github.com/jeffrichley/agent_core/issues/589)) ([8d1e77c](https://github.com/jeffrichley/agent_core/commit/8d1e77ce00102d5aa94de3169dbda9418cfd965d)), closes [#586](https://github.com/jeffrichley/agent_core/issues/586)
* **test:** update bus-tail summary fixture for tightened FileAttachment schema ([#64](https://github.com/jeffrichley/agent_core/issues/64)) ([581c8ec](https://github.com/jeffrichley/agent_core/commit/581c8eca8a883c8d15a099db07b851cde66fb13d))
</details>

<details><summary>agent-core-briefs: 0.11.0</summary>

## [0.11.0](https://github.com/jeffrichley/agent_core/compare/agent-core-briefs-v0.10.0...agent-core-briefs-v0.11.0) (2026-08-17)


### Features

* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))


### Bug Fixes

* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))
</details>

<details><summary>agent-core-channel: 0.9.0</summary>

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
* envelope extension hookspec  content-agnostic plugin seam for new kinds + renderers ([#124](https://github.com/jeffrichley/agent_core/issues/124)) ([ad4e166](https://github.com/jeffrichley/agent_core/commit/ad4e16686448f34f95e0203c8abccc79819163c8))
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
</details>

<details><summary>agent-core-discord: 0.10.0</summary>

## [0.10.0](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-discord-v0.10.0) (2026-08-17)


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
* **discord:** cancel both sweep tasks before awaiting  attachment sweep no longer starves no-sleep tests ([#76](https://github.com/jeffrichley/agent_core/issues/76) Task 5) ([5eebdfd](https://github.com/jeffrichley/agent_core/commit/5eebdfdf9033ccf9bb935b504c550465ecde6625))
* **discord:** evict missing-timestamp typing orphans regardless of host uptime ([#335](https://github.com/jeffrichley/agent_core/issues/335)) ([0fce89c](https://github.com/jeffrichley/agent_core/commit/0fce89c3c76e796ba1eaaf72a804a2fbc2467268))
* **discord:** gate meta-event handlers on channel allowlist ([#210](https://github.com/jeffrichley/agent_core/issues/210)) ([2ec23c4](https://github.com/jeffrichley/agent_core/commit/2ec23c4a9e32cd214a7f977a23f5ab2c1dc3c89c))
* **discord:** harden access-config reload loop against schema-invalid JSON ([#257](https://github.com/jeffrichley/agent_core/issues/257)) ([bd36b88](https://github.com/jeffrichley/agent_core/commit/bd36b8884abb062aadcea28ad4787a2dbcdca8ec))
* **discord:** on_message hands bot authors to the access gate ([#159](https://github.com/jeffrichley/agent_core/issues/159)) ([b36e44a](https://github.com/jeffrichley/agent_core/commit/b36e44a0a97ef1c3954df745caa144fb6acad997))
* **discord:** redact signed CDN urls from download_error + logs ([#76](https://github.com/jeffrichley/agent_core/issues/76) Task 3) ([79c9401](https://github.com/jeffrichley/agent_core/commit/79c94018b1aa8da4874f345e7ea1afc5e13b3905))
* **discord:** same cancel-all-before-await fix in start-rollback path ([#76](https://github.com/jeffrichley/agent_core/issues/76) Task 5) ([79bb4a7](https://github.com/jeffrichley/agent_core/commit/79bb4a7e57a1bc1a9b92c984b48c26c89d596098))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* **reply:** strip inbound-only Discord metadata keys in reply() and escalate Unrecognized-shape ack urgency ([#224](https://github.com/jeffrichley/agent_core/issues/224)) ([ca550c5](https://github.com/jeffrichley/agent_core/commit/ca550c5691b4a3228d0b57f46bff474cd543b143))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))
</details>

<details><summary>agent-core-hatchery: 0.12.0</summary>

## [0.12.0](https://github.com/jeffrichley/agent_core/compare/agent-core-hatchery-v0.11.0...agent-core-hatchery-v0.12.0) (2026-08-17)


### Features

* **core:** endpoints.d + jobs.d conf.d-style merging ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([e385077](https://github.com/jeffrichley/agent_core/commit/e385077bfa448639680719dfa0a818f0cba048c9))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* enable mypy --strict for agent-core-hatchery ([#519](https://github.com/jeffrichley/agent_core/issues/519)) ([2365ab2](https://github.com/jeffrichley/agent_core/commit/2365ab27de4a450ec2a8c8d6211f7fdb43678a3d))
* **hatchery:** add --no-daemon-reload to hatch without a live daemon ([#483](https://github.com/jeffrichley/agent_core/issues/483)) ([7bf06c6](https://github.com/jeffrichley/agent_core/commit/7bf06c64519033ffe4b768e1bb0318282f908e77))
* **hatchery:** agent-core-hatchery package ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2cde559](https://github.com/jeffrichley/agent_core/commit/2cde55997abe5614ef0bac2154a51d95a80fef9a))
* **hatchery:** basic Hatcher orchestration (render ’ write ’ validate) ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([bca1c1d](https://github.com/jeffrichley/agent_core/commit/bca1c1d4c97d23ed01c23e22881bb64445f08de6))
* **hatchery:** channel scaffolding modules  Discord, webcam, GitHub backup ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([ccae9a4](https://github.com/jeffrichley/agent_core/commit/ccae9a4ec686af6084d2f12dc6272b2637c53f79))
* **hatchery:** cli with --config mode (Phase 2 stop) ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([19039ab](https://github.com/jeffrichley/agent_core/commit/19039ab4d4afc38309ec89411194bcb306fb6e43))
* **hatchery:** config and daemon-fragment templates ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([b895feb](https://github.com/jeffrichley/agent_core/commit/b895febe930968c6742fb1a6d055397fc9354d5f))
* **hatchery:** daemon_config writer for endpoints.d + jobs.d fragments ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([7c967a4](https://github.com/jeffrichley/agent_core/commit/7c967a43ccc659d0b18b397015fd09655582aed6))
* **hatchery:** daemon-fragment writing + parse validation in Hatcher ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([2f93f97](https://github.com/jeffrichley/agent_core/commit/2f93f97d388b48bc4cd666d045ee56e40e70e233))
* **hatchery:** elder-letter manifest resolver ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([f27992f](https://github.com/jeffrichley/agent_core/commit/f27992ffb7610f73fe36e8aad3eaa6446bea2a9b))
* **hatchery:** elder-letters manifest + Pepper's bundled snapshot ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([1a15482](https://github.com/jeffrichley/agent_core/commit/1a154827c890fcc5515b0361bd156b111098fe19))
* **hatchery:** file_classes manifest loader ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([dadb2da](https://github.com/jeffrichley/agent_core/commit/dadb2dac61bb0561a1e3a445d87df0ce2e7b1050))
* **hatchery:** file-classes.yaml manifest ([#75](https://github.com/jeffrichley/agent_core/issues/75)) ([d27d0fa](https://github.com/jeffrichley/agent_core/commit/d27d0fafb88c6b4ddb2275ad9bb6db20c5385ad8))
* **hatchery:** hatch’run handoff  venv build + .mcp.json gen + daemon probe ([#410](https://github.com/jeffrichley/agent_core/issues/410)) ([04a54df](https://github.com/jeffrichley/agent_core/commit/04a54dfe4dbdb33b0187f88602d780e802879cc1))
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
</details>

<details><summary>agent-core-inbound: 0.11.0</summary>

## [0.11.0](https://github.com/jeffrichley/agent_core/compare/agent-core-inbound-v0.10.0...agent-core-inbound-v0.11.0) (2026-08-17)


### Features

* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **inbound:** inbound notifications v1.a  GitHub ’ Wren via Tailscale Funnel ([#196](https://github.com/jeffrichley/agent_core/issues/196)) ([d180b73](https://github.com/jeffrichley/agent_core/commit/d180b73fe8981bc8cc7add7342800a3a8291d23d))
* **inbound:** v2  schema-flexible GitHub event matching ([#199](https://github.com/jeffrichley/agent_core/issues/199)) ([a178628](https://github.com/jeffrichley/agent_core/commit/a1786282dde9cdfd493312468f9a533b9568945d))
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
</details>

<details><summary>agent-core-qa: 0.9.1</summary>

## [0.9.1](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-qa-v0.9.1) (2026-08-17)


### Bug Fixes

* **qa:** gate daemon readiness on a tool that needs the started handle ([#590](https://github.com/jeffrichley/agent_core/issues/590)) ([b601f65](https://github.com/jeffrichley/agent_core/commit/b601f6568f26638413bdf1c8a69d2b5b9c453c24))
</details>

<details><summary>agent-core-voice: 0.11.0</summary>

## [0.11.0](https://github.com/jeffrichley/agent_core/compare/agent-core-voice-v0.10.0...agent-core-voice-v0.11.0) (2026-08-17)


###   BREAKING CHANGES

* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130))

### Features

* **bus:** offload VoiceEndpoint construction to start() and add slow-deliver watchdog ([#331](https://github.com/jeffrichley/agent_core/issues/331)) ([57d98f5](https://github.com/jeffrichley/agent_core/commit/57d98f546d34afa250b69cc57256e2220a263228))
* **core:** hoist JsonlAuditLog base into core, subclass in briefs/voice/webcam ([#465](https://github.com/jeffrichley/agent_core/issues/465)) ([2c7843a](https://github.com/jeffrichley/agent_core/commit/2c7843afd278ed5732388ebb6a3b8350f4f14810))
* **deps:** version-pin sibling deps and remove qwen-tts from voice ([#430](https://github.com/jeffrichley/agent_core/issues/430)) ([3b173ee](https://github.com/jeffrichley/agent_core/commit/3b173eebd24fb8eab995785a9e154a0dda870f19))
* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))
* **release:** phase 2.6  end-to-end install validation (closes 3 release-pipeline bugs) ([#121](https://github.com/jeffrichley/agent_core/issues/121)) ([ce8ee59](https://github.com/jeffrichley/agent_core/commit/ce8ee5971acb46d64944f3df3149249214dabcf8))
* **supervision:** migrate leaky asyncio.create_task sites to BusHandle.spawn() ([#302](https://github.com/jeffrichley/agent_core/issues/302)) ([0716587](https://github.com/jeffrichley/agent_core/commit/07165879a4f1055ff1d0636169bdc2a178ea57da))
* **versioning:** add tags=true to member cache-keys (tag changes build output) ([2100d79](https://github.com/jeffrichley/agent_core/commit/2100d796fc383c1ce02eafd7a6a7f79216e0e384))
* **versioning:** VCS-derived versions via uv-dynamic-versioning (all 10 members) ([59be759](https://github.com/jeffrichley/agent_core/commit/59be759b9ca55a3faa27e9668490adc25127fc69))
* voice-library bus-async migration (Phase 1-4 + caller audit) ([#130](https://github.com/jeffrichley/agent_core/issues/130)) ([574044c](https://github.com/jeffrichley/agent_core/commit/574044c4560f504a02c49b736fcc80cbe170672a))
* **voice:** add format selection (mp3, ogg) to synthesize_speech ([#258](https://github.com/jeffrichley/agent_core/issues/258)) ([2414f9a](https://github.com/jeffrichley/agent_core/commit/2414f9acedec83b16801f8f2a7c64ecefa9502d0))
* **voice:** agent-core-voice  per-agent Qwen3-TTS over the bus ([42713d7](https://github.com/jeffrichley/agent_core/commit/42713d78cc6bcecb9d88c114d1a180e07216968a))
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
</details>

<details><summary>agent-core-webcam: 0.11.0</summary>

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
* **voice:** agent-core-voice  per-agent Qwen3-TTS over the bus ([42713d7](https://github.com/jeffrichley/agent_core/commit/42713d78cc6bcecb9d88c114d1a180e07216968a))


### Bug Fixes

* 6 HIGH findings from the adversarial review (leaks, fragility, footguns) ([#469](https://github.com/jeffrichley/agent_core/issues/469)) ([f546c54](https://github.com/jeffrichley/agent_core/commit/f546c548d2da6455e611c5b5820a21fa8c86211e))
* **audit:** atomic append + disk-failure swallow test (voice & webcam) ([3ad7430](https://github.com/jeffrichley/agent_core/commit/3ad7430b837e779668b38a5f933a8746c702e371))
* **core:** import Iterable from collections.abc (UP035) ([67c03f6](https://github.com/jeffrichley/agent_core/commit/67c03f675dc8c99d59306467409d5d38a0c7b415))
* **daemon:** add tool.uv.cache-keys git entry to all members (Defect A) ([b5bb4e2](https://github.com/jeffrichley/agent_core/commit/b5bb4e2d0509e2728a3d55694dd54e490fe7e2e8))
* **deps:** raise agent-core sibling caps from &lt;0.9 to &lt;0.10 ([#577](https://github.com/jeffrichley/agent_core/issues/577)) ([a8ecc74](https://github.com/jeffrichley/agent_core/commit/a8ecc749efab0d343cab15ab50ba15eb45ea840a))
* **presence:** a dead sensor must not read as a quiet one ([#609](https://github.com/jeffrichley/agent_core/issues/609)) ([0414d19](https://github.com/jeffrichley/agent_core/commit/0414d19c1153f02cc05aa252b99cf8befc6ca1df))
* rename flagship dist to agent-core-bus + bump inter-package pins to 0.8.x ([#474](https://github.com/jeffrichley/agent_core/issues/474)) ([48426e7](https://github.com/jeffrichley/agent_core/commit/48426e7ff753d8f35d4a4626308546e9d0f331f7))
* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))
</details>

---
This PR was generated with [Release Please](https://github.com/googleapis/release-please). See [documentation](https://github.com/googleapis/release-please#release-please).