# API Reference

Auto-generated from the source docstrings (Google style). This page covers the
public `agent_core` surface an adopter builds against. Members prefixed with `_`
are internal and omitted.

!!! tip "Machine-readable"
    An AI agent can ingest this whole site — including this reference — via
    [`llms-full.txt`](https://jeffrichley.github.io/agent_core/llms-full.txt).

## The bus

::: agent_core.bus.core.Bus
    options:
      show_root_heading: true
      heading_level: 3

::: agent_core.bus.core.BusConfig
    options:
      heading_level: 3

::: agent_core.bus.core.SupervisorConfig
    options:
      heading_level: 3

::: agent_core.bus.handle.BusHandle
    options:
      heading_level: 3

## Envelopes

::: agent_core.bus.envelope.Envelope
    options:
      heading_level: 3

## Endpoints

::: agent_core.bus.protocol.Endpoint
    options:
      heading_level: 3

::: agent_core.bus.core.EndpointSpec
    options:
      heading_level: 3

## Persistence

::: agent_core.bus.persistence.Persistence
    options:
      heading_level: 3

## Config reference

- [Bus config keys](bus-config.md) — every `bus:` and `bus.supervisor:` YAML key with type, default, and effect description.

## Extensions

The extension system is built on [pluggy](https://pluggy.readthedocs.io/) hook
specifications. The hookspecs live in `agent_core.plugins.specs.AgentCoreSpecs`;
because that module is an implicit namespace package, its members are documented
in prose rather than autodoc. See the [Extensions concept](../concepts/extensions.md)
for the full hook catalogue and the [Write an extension](../guides/write-an-extension.md)
guide for the plugin-author recipe.
