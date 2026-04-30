# agent_core Plugin Hooks (v1)

`agent_core` uses [Pluggy](https://pluggy.readthedocs.io/) for runtime extension points.
Plugins are discovered through setuptools entry points in the `agent_core` group.

## Register a plugin

Add an entry point in your package's `pyproject.toml`:

```toml
[project.entry-points."agent_core"]
my_plugin = "my_package.agent_core_plugin"
```

Then implement one or more hook functions with:

```python
import pluggy

hookimpl = pluggy.HookimplMarker("agent_core")
```

## Hook surface

- `resolve_endpoint_class(endpoint_class)`  
  Return a class to construct an endpoint from YAML `endpoints[].class`.
- `resolve_bus_hook_class(hook_class)`  
  Return a class to construct a bus hook from YAML `bus_hooks`.
- `resolve_hook_tool_class(tool_class)`  
  Return a class for hook pipeline tools.
- `resolve_class(class_path)`  
  Generic fallback resolver; used after the specific `resolve_*` hooks.
- `configure_endpoint_instance(instance, endpoint_name, endpoint_config, services)`  
  Post-construction endpoint wiring.
- `configure_bus_hook_instance(instance, stage, hook_config, services)`  
  Post-construction bus hook wiring.
- `validate_config(raw_config)`  
  Validate runner config early; raise to block startup.

## Resolution order

Runtime class resolution uses this precedence:

1. Specific resolver (`resolve_endpoint_class`, `resolve_bus_hook_class`, or `resolve_hook_tool_class`)
2. Generic resolver (`resolve_class`)
3. Dotted import fallback (`module.Class`)

Returning `None` means "not handled".

## RunnerServices

Wiring hooks receive a `RunnerServices` instance.

Current fields:

- `notify_broker`: shared notify broker used for push fan-out

Treat this object as runtime-owned shared state; do not replace it.

## Built-in behavior

Core ships with a built-in runtime plugin that:

- attaches `notify_broker` to endpoints implementing `NotificationBrokerAwareEndpoint`
- keeps bus-hook wiring and config validation as no-op defaults

Your plugin hooks run alongside these defaults.
