# Write an Extension

agent-core uses [Pluggy](https://pluggy.readthedocs.io/) for runtime extension points. A plugin is a regular Python package that declares an `agent_core` entry point and implements one or more hook functions with `@hookimpl`. The daemon discovers all installed plugins at startup.

This guide shows the full recipe: package layout, the entry-point declaration, a complete `register_endpoint_types()` implementation, and a summary of the other hooks you can implement.

Sources verified: `packages/core/src/agent_core/plugins/specs.py`, `packages/core/docs/plugins.md`, `docs/extensions.md`.

See also: [Concepts — Extensions](https://jeffrichley.github.io/agent_core/concepts/extensions/index.md).

______________________________________________________________________

## When do you need a plugin?

You need a plugin when you want the daemon to resolve a `type:` string in `agent_core.yaml` to your endpoint class. Without a plugin registration the daemon fails to start with an unknown type error.

You also need a plugin to:

- Register new envelope kinds and their inline-wake renderers (`register_envelope_renderers`).
- Add CLI subcommands to the `agent-core` CLI (`register_cli_subapps`).
- Validate operator-supplied config at startup (`validate_config`).
- Do cross-endpoint wiring after all endpoints are constructed (`wire_endpoints_after_registration`).

______________________________________________________________________

## Package layout

```
my_plugin/
├── pyproject.toml
└── src/
    └── my_plugin/
        ├── __init__.py
        ├── agent_core_plugin.py   # hookimpl module
        └── endpoints/
            └── greeter.py         # your endpoint class
```

The hookimpl module name is arbitrary — what matters is the entry-point target.

______________________________________________________________________

## Step 1 — Declare the entry point

In `pyproject.toml`, add your module under the `agent_core` entry-point group:

```
[project]
name = "my-plugin"
# ... other metadata ...

[project.entry-points."agent_core"]
my_plugin = "my_plugin.agent_core_plugin"
```

The key (`my_plugin`) is an arbitrary label. The value is the dotted import path to the module that contains your `@hookimpl` functions.

______________________________________________________________________

## Step 2 — Implement the hookimpl module

```
# src/my_plugin/agent_core_plugin.py
import pluggy

# The marker must use the "agent_core" project name — this is the
# entry-point group name and the Pluggy project name.
hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict:
    """Return {type_id: EndpointClass} registrations.

    type_id is what operators write as `type:` in agent_core.yaml.
    Import lazily so the plugin does not force the dependency on
    packages that don't use this endpoint type.
    """
    from my_plugin.endpoints.greeter import GreeterEndpoint

    return {
        "my_plugin.greeter": GreeterEndpoint,
    }
```

After `pip install -e .` (or a wheel install), `uv run agent-core daemon start` discovers this plugin via the `agent_core` entry-point group and registers `my_plugin.greeter` as a known type.

Duplicate type ids raise at startup

If two installed plugins register the same `type_id`, the daemon raises `PluginRegistryError` immediately. Use a namespace prefix (e.g. `my_org.greeter`) to avoid collisions with built-in or third-party plugins.

______________________________________________________________________

## Step 3 — Wire it in config

```
# agent_core.yaml
endpoints:
  - type: my_plugin.greeter
    name: greeter
    params:
      prefix: "Hi"
```

The daemon calls `GreeterEndpoint(name="greeter", prefix="Hi")`. Any `params:` keys are passed as keyword arguments to `__init__` (after `name`).

______________________________________________________________________

## Other hooks you can implement

All hook signatures live in `agent_core.plugins.specs.AgentCoreSpecs`. Implement only the ones you need — Pluggy ignores unimplemented hooks.

### `register_envelope_renderers`

Register new envelope kinds and their inline-wake renderer callables:

```
@hookimpl
def register_envelope_renderers() -> dict:
    from my_plugin.rendering import render_desire

    # The key becomes a first-class envelope kind.
    # The callable receives the full envelope dict and returns
    # the rendered body string inside the <inbox> tag.
    return {"Desire": render_desire}


def render_desire(envelope: dict) -> str:
    text = envelope["payload"].get("text", "")
    return f"<desire>{text}</desire>"
```

To publish envelopes of this kind, set `kind="Desire"` and carry a dict payload whose `"kind"` key matches:

```
from agent_core.bus.envelope import Envelope

env = Envelope(
    id="...",
    correlation_id="...",
    to="target-agent",
    kind="Desire",
    payload={"kind": "Desire", "text": "I want to learn more about this."},
    created_at=datetime.now(UTC),
)
await handle.publish(env)
```

### `register_cli_subapps`

Mount a Typer subapp onto the top-level `agent-core` CLI:

```
@hookimpl
def register_cli_subapps(app) -> None:
    from my_plugin.cli import my_subapp

    app.add_typer(my_subapp, name="greeter")
```

After this, `uv run agent-core greeter --help` works.

### `validate_config`

Inspect the raw config dict at daemon startup and raise to block launch:

```
@hookimpl
def validate_config(raw_config: dict) -> None:
    endpoints = raw_config.get("endpoints", [])
    for ep in endpoints:
        if ep.get("type") == "my_plugin.greeter":
            params = ep.get("params", {})
            if "prefix" not in params:
                raise ValueError("my_plugin.greeter requires a 'prefix' param")
```

### `configure_endpoint_instance`

Post-construction wiring for a specific endpoint instance — called after construction, before `bus.start()`:

```
@hookimpl
def configure_endpoint_instance(
    instance,
    endpoint_name: str,
    endpoint_config: dict,
    services,
) -> None:
    from my_plugin.endpoints.greeter import GreeterEndpoint

    if isinstance(instance, GreeterEndpoint):
        instance.attach_notify_broker(services.notify_broker)
```

`services` is a `RunnerServices` instance with:

- `notify_broker` — the shared notification broker for push fan-out.
- `mcp_audit_writer` — the daemon-wide MCP audit writer (may be `None`).

### `wire_endpoints_after_registration`

Cross-endpoint wiring once all endpoints are constructed and registered on the bus, but before `bus.start()`:

```
@hookimpl
def wire_endpoints_after_registration(
    endpoints: dict,
    raw_endpoint_configs: dict,
    services,
) -> None:
    orchestrator = endpoints.get("my-orchestrator")
    agent = endpoints.get("my-agent")
    if orchestrator and agent:
        agent.set_orchestrator(orchestrator)
```

### `reserved_endpoint_params`

If your post-construction wiring hook reads `params:` keys that are not constructor kwargs, declare them here so the runner strips them before calling `cls(name=..., **params)`:

```
@hookimpl
def reserved_endpoint_params() -> list[str]:
    return ["my_orchestrator_name"]
```

______________________________________________________________________

## Built-in alias reference

Core ships an entrypoint-loaded alias plugin that maps these built-in type ids:

| type id                   | Class                                                        |
| ------------------------- | ------------------------------------------------------------ |
| `builtin.claude_code_mcp` | `agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint` |
| `builtin.stub`            | `agent_core.endpoints.stub.StubEndpoint`                     |
| `builtin.scheduler`       | `agent_core.endpoints.scheduler.SchedulerEndpoint`           |
| `builtin.handoff_jobs`    | `agent_core.endpoints.handoff_jobs.HandoffJobsEndpoint`      |

Your plugin's hooks run alongside these defaults.

______________________________________________________________________

## Next steps

- [Add an endpoint](https://jeffrichley.github.io/agent_core/guides/add-an-endpoint/index.md) — implement the `Endpoint` protocol your plugin registers
- [Send and consume envelopes](https://jeffrichley.github.io/agent_core/guides/send-and-consume/index.md) — publish plugin-registered envelope kinds
- [Concepts — Extensions](https://jeffrichley.github.io/agent_core/concepts/extensions/index.md) — envelope extension design, renderer dispatch order, collision policy
