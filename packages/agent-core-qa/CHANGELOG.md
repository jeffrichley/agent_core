# Changelog

## [0.9.1](https://github.com/jeffrichley/agent_core/compare/v0.9.0...agent-core-qa-v0.9.1) (2026-08-17)


### Bug Fixes

* **qa:** gate daemon readiness on a tool that needs the started handle ([#590](https://github.com/jeffrichley/agent_core/issues/590)) ([b601f65](https://github.com/jeffrichley/agent_core/commit/b601f6568f26638413bdf1c8a69d2b5b9c453c24))

## [0.9.0](https://github.com/jeffrichley/agent_core/compare/v0.8.1...agent-core-qa-v0.9.0) (2026-08-03)


### Features

* **mypy:** enable --strict for briefs, busproxy, inbound, voice, webcam, qa ([#532](https://github.com/jeffrichley/agent_core/issues/532)) ([9663d87](https://github.com/jeffrichley/agent_core/commit/9663d8783176116a7ae352b2ba07933bf056fbe7))
* **qa:** session-scoped auto-start daemon fixture, replace skip-unless-live autouse ([#534](https://github.com/jeffrichley/agent_core/issues/534)) ([407c387](https://github.com/jeffrichley/agent_core/commit/407c3874a0636641b0b546a4c262408c839973d3))


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.1](https://github.com/jeffrichley/agent_core/compare/v0.8.0...agent-core-qa-v0.8.1) (2026-07-22)


### Bug Fixes

* republish package family to complete rate-limited first publish ([#477](https://github.com/jeffrichley/agent_core/issues/477)) ([32977ac](https://github.com/jeffrichley/agent_core/commit/32977ac78da4a15553329bb4e1fdaa9e894834ad))

## [0.8.0](https://github.com/jeffrichley/agent_core/compare/v0.7.0...agent-core-qa-v0.8.0) (2026-07-21)


### Features

* **hatchery:** ungendered templates + .mcp.json generation ([#81](https://github.com/jeffrichley/agent_core/issues/81)/[#82](https://github.com/jeffrichley/agent_core/issues/82)) ([#357](https://github.com/jeffrichley/agent_core/issues/357)) ([f48762d](https://github.com/jeffrichley/agent_core/commit/f48762df78eb12cfa9524e9b5a277741534ea8a7)), closes [#311](https://github.com/jeffrichley/agent_core/issues/311)
* **release:** configure release-please for 12-package synchronized version train ([#428](https://github.com/jeffrichley/agent_core/issues/428)) ([c1b4097](https://github.com/jeffrichley/agent_core/commit/c1b409765c61d09a6657b0e8b5e43a27cb235b3e))


### Bug Fixes

* log or justify bare except-pass swallows + test get_client factory (closes [#408](https://github.com/jeffrichley/agent_core/issues/408)) ([#471](https://github.com/jeffrichley/agent_core/issues/471)) ([e67783c](https://github.com/jeffrichley/agent_core/commit/e67783ccb4dfab6f1783d6a67223f475f81526ea))
