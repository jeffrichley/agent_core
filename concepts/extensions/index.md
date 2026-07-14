# Extensions

agent-core is designed to be extended without forking. The extension model is built on [Pluggy](https://pluggy.readthedocs.io/), the same hook framework used by pytest. A plugin is a Python package that declares an entry point in the `agent_core` group; the bus runner discovers and loads it at startup.

## Entry point registration

```
# your package's pyproject.toml
[project.entry-points."agent_core"]
my_plugin = "my_package.plugin"
```

Inside `my_package/plugin.py`, mark hook implementations with the `hookimpl` marker:

```
import pluggy

hookimpl = pluggy.HookimplMarker("agent_core")

@hookimpl
def register_endpoint_types() -> dict:
    return {"my_plugin.my_endpoint": MyEndpoint}
```

The runner collects all installed plugins via `importlib.metadata` and calls their hook implementations in registration order.

## What a plugin can contribute

### New endpoint types

```
@hookimpl
def register_endpoint_types() -> dict[str, type[Endpoint]]:
    return {"my_plugin.widget": WidgetEndpoint}
```

The returned dict maps a type-id string to an `Endpoint` class. Operators reference the type-id in their YAML config under `endpoints[].type`. Duplicate type-ids across plugins raise `PluginRegistryError` at startup.

### New bus hook types

```
@hookimpl
def register_bus_hook_types() -> dict[str, type[BusHook]]:
    return {"my_plugin.rate_limit": RateLimitHook}
```

Bus hooks intercept envelopes at `pre_publish` or `pre_deliver`. A hook can mutate the envelope (e.g., stamp metadata) or drop it (return `None`). Operators configure hooks in YAML under `bus_hooks[].type`.

### New envelope kinds + renderers

```
@hookimpl
def register_envelope_renderers() -> dict[str, Any]:
    return {"MyKind": render_my_kind}
```

Registering a renderer makes `MyKind` a first-class envelope kind. Envelopes with `kind="MyKind"` carry a dict payload (the plugin owns validation). The renderer is a callable `(envelope: dict) -> str` that formats the body of the `<inbox>` block for inline-wake notifications. See the [envelope extensions doc](https://github.com/jeffrichley/agent_core/blob/main/docs/extensions.md) for a worked example including payload publication.

### Post-construction endpoint wiring

```
@hookimpl
def configure_endpoint_instance(
    instance, endpoint_name, endpoint_config, services
) -> None:
    if isinstance(instance, MyEndpoint):
        instance.set_broker(services.notify_broker)
```

Use this for wiring that cannot happen in `__init__` because it depends on shared runtime services (`RunnerServices` carries the `notify_broker` and optional `mcp_audit_writer`).

For cross-endpoint wiring (e.g., pairing two endpoints that need references to each other), use `wire_endpoints_after_registration`, which is called once all endpoints are constructed.

### CLI subcommands

```
@hookimpl
def register_cli_subapps(app: typer.Typer) -> None:
    from my_package.cli import subapp
    app.add_typer(subapp, name="widget")
```

This extends the top-level `agent-core` CLI without the core package depending on your plugin. Import lazily inside the hookimpl to preserve layering.

### Config validation

```
@hookimpl
def validate_config(raw_config: dict) -> None:
    if "widget" not in raw_config:
        raise ValueError("my_plugin requires a [widget] config section")
```

Validation hooks run early, before any endpoint is constructed. Raise to block startup with a clear error message.

### Bus log projectors

```
@hookimpl
def register_bus_log_projectors() -> dict[str, Any]:
    return {"MyKind": MyKindProjector()}
```

Projectors map envelope kinds (or `Event.type` strings) to display logic for the bus log. Last-write-wins on duplicate keys.

## Built-in type aliases

Core ships a built-in plugin that registers the following aliases so you can reference them directly in YAML config:

**Endpoint types:**

| Alias                     | Class                                                        |
| ------------------------- | ------------------------------------------------------------ |
| `builtin.stub`            | `agent_core.endpoints.stub.StubEndpoint`                     |
| `builtin.discord`         | `agent_core_discord.endpoint.DiscordEndpoint`                |
| `builtin.claude_code_mcp` | `agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint` |
| `builtin.scheduler`       | `agent_core.endpoints.scheduler.SchedulerEndpoint`           |
| `builtin.handoff_jobs`    | `agent_core.endpoints.handoff_jobs.HandoffJobsEndpoint`      |

**Hook tool types** (used in pipeline config):

| Alias                       | Class                                                       |
| --------------------------- | ----------------------------------------------------------- |
| `builtin.handoff_writer`    | `agent_core.hooks.tools.handoff_writer.HandoffWriter`       |
| `builtin.identity_injector` | `agent_core.hooks.tools.identity_injector.IdentityInjector` |
| `builtin.time_injector`     | `agent_core.hooks.tools.time_injector.TimeInjector`         |

Your plugin's type-ids coexist with these. You cannot override a built-in type-id; use a distinct namespace (e.g., `my_org.my_endpoint`).

## Hook ordering and collision policy

Pluggy calls all implementations of a hook and collects their return values. For `register_endpoint_types` and `register_envelope_renderers`, the runner merges the dicts — a duplicate key across two plugins raises `PluginRegistryError` at startup. This is a programming error, not a configuration issue; fix it by choosing a distinct type-id or kind name.

The `register_bus_log_projectors` hook is the exception: last-write-wins on duplicates is intentional, allowing a plugin to override a built-in projector.

## Writing an extension

For a step-by-step guide with a working example, see [Write an extension](https://jeffrichley.github.io/agent_core/guides/write-an-extension/index.md).
