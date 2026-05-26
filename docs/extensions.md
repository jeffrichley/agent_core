# Envelope extensions

The bus is content-agnostic: built-in envelope kinds (`TextMessage`, `Event`,
`ToolInvocation`, `Cancellation`, `Progress`, `Acknowledgment`) ship in core,
and plugins can register additional first-class kinds + renderers without
modifying agent_core itself.

The seam is intentionally minimal: a `register_envelope_renderers` hookspec
plus open `Envelope.kind`. Plugin payloads are dicts; the plugin owns
validation. See
[`docs/superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md`](superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md)
for the full architectural rationale (capability-vs-content split; why
plugin-private renderers; the deferred items in §6).

## Plugin author quickstart

A plugin contributes a new envelope kind by:

1. Registering a renderer via the `agent_core` Pluggy hookspec.
2. Publishing envelopes with `kind=<your kind name>` and a dict payload
   whose `"kind"` field matches.

```python
# my_plugin/plugin.py
from agent_core.plugins.specs import hookimpl


def render_my_kind(envelope: dict) -> str:
    """Return the rendered body inside the <inbox> tag.

    Receives the full envelope dict (id, kind, from, payload, metadata,
    urgency, created_at, ...). Returns an HTML-escape-safe string;
    framework attrs (kind, from, urgency, envelope_id) are added by the
    rendering layer.
    """
    text = envelope["payload"].get("text", "")
    return f"<my-kind>{text}</my-kind>"


@hookimpl
def register_envelope_renderers() -> dict:
    return {"MyKind": render_my_kind}
```

Publish from your plugin code:

```python
from datetime import UTC, datetime
from agent_core.bus.envelope import Envelope

envelope = Envelope(
    id="...",
    correlation_id="...",
    to="target-agent",
    kind="MyKind",
    payload={"kind": "MyKind", "text": "hello from a plugin extension"},
    created_at=datetime.now(UTC),
)
bus.publish(envelope)
```

The receiving agent's inline-wake notification will surface the rendered
output inside an `<inbox>` block.

## Dispatch order

When `render_envelope(env)` runs, the rendering layer dispatches in this
order:

1. Plugin-registered renderer for `env["kind"]` (allows override of built-ins
   on collision; operator-aware decision).
2. Built-in renderer (`TextMessage`, `Event`, `Acknowledgment`).
3. Generic JSON renderer (`BriefRequest`, `ToolInvocation`, `Progress`,
   `ComposeBrief`).
4. Fallback marker with `render='fallback'` attribute.

## Constraints

- **No bus-side payload validation for plugin kinds in v0.** Built-in kinds
  remain strictly validated via their typed Pydantic models. Plugin kinds
  carry dict payloads; the plugin's own code is responsible for validating
  shape (Pydantic at the plugin level is the recommended pattern).
- **Duplicate-kind collisions across plugins raise at startup.**
  `PluginRegistryError` from `get_envelope_renderers(pm)` — fix by ensuring
  distinct plugins register distinct kind names.
- **Plugin renderers can override built-ins** by registering the same kind.
  This is by design (operator-aware) but should be rare — prefer distinct
  kind names.

## Wiring the renderer table

The bus runner aggregates plugin contributions via
`agent_core.plugins.manager.get_envelope_renderers(pm)` and hands the
result to
`agent_core_channel.rendering.set_plugin_renderers(renderers)` at
bootstrap. Tests register inline via direct `set_plugin_renderers(...)`
call; an autouse fixture in
`packages/agent-core-channel/tests/test_rendering_plugin_dispatch.py`
clears between tests.
