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

- `register_endpoint_types()`  
  Return endpoint type registrations used for YAML `endpoints[].type`.
- `register_bus_hook_types()`  
  Return bus-hook type registrations used for YAML `bus_hooks[].type`.
- `register_hook_tool_types()`  
  Return hook-tool type registrations used for pipeline tool ids.
- `configure_endpoint_instance(instance, endpoint_name, endpoint_config, services)`  
  Post-construction endpoint wiring.
- `configure_bus_hook_instance(instance, stage, hook_config, services)`  
  Post-construction bus hook wiring.
- `validate_config(raw_config)`  
  Validate runner config early; raise to block startup.

## Resolution order

Runtime resolution uses the plugin registry maps built at startup.
Unknown type ids fail startup/config load immediately.

## RunnerServices

Wiring hooks receive a `RunnerServices` instance.

Current fields:

- `notify_broker`: shared notify broker used for push fan-out

Treat this object as runtime-owned shared state; do not replace it.

## Built-in behavior

Core ships with a built-in runtime plugin that:

- attaches `notify_broker` to endpoints implementing `NotificationBrokerAwareEndpoint`
- keeps bus-hook wiring and config validation as no-op defaults

Core also ships an entrypoint-loaded alias plugin:

- endpoint aliases:
  - `builtin.claude_code_mcp` -> `agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint`
  - `builtin.discord` -> `agent_core_discord.endpoint.DiscordEndpoint`
  - `builtin.stub` -> `agent_core.endpoints.stub.StubEndpoint`
  - `builtin.scheduler` -> `agent_core.endpoints.scheduler.SchedulerEndpoint`
  - `builtin.handoff_jobs` -> `agent_core.endpoints.handoff_jobs.HandoffJobsEndpoint`
- hook tool aliases:
  - `builtin.handoff_writer` -> `agent_core.hooks.tools.handoff_writer.HandoffWriter`
  - `builtin.identity_injector` -> `agent_core.hooks.tools.identity_injector.IdentityInjector`
  - `builtin.time_injector` -> `agent_core.hooks.tools.time_injector.TimeInjector`
- bus hook aliases:
  - `builtin.daily_raw_jsonl` -> `agent_core.bus_hooks.daily_raw_jsonl.DailyRawJsonlHook`

Your plugin hooks run alongside these defaults.

## Handoff daemon setup (default pattern)

Use `builtin.handoff_jobs` to host the enqueue endpoint, then point
`builtin.handoff_writer` at that endpoint. The hook remains enqueue-only.

```yaml
http:
  bind_host: 127.0.0.1
  bind_port: 8788

endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs

pipelines:
  SessionEnd:
    - type: builtin.handoff_writer
      params:
        output_path: "C:\\Users\\you\\Memory\\agent\\handoff.md"
        vault_root: "C:\\Users\\you\\Memory\\agent"
        handoff_status_path: "C:\\Users\\you\\Memory\\agent\\handoff-status.json"
        handoff_jobs_url: "http://127.0.0.1:8788/internal/handoff-jobs"
        agent_name: "YourAgent"
```

With this setup:

- hook writes no handoff files and runs no LLM extraction
- daemon worker writes `handoff.md`
- daemon worker is the single writer of `handoff-status.json`
