# Envelope extension hookspec — design

> **Status:** Awaiting Pepper criterion-check; Jeff has delegated implementation authority.
> **Authors:** Wren (draft), Pepper (criterion-check + first-consumer architect).
> **Date:** 2026-05-25.
> **Scope:** v0.4.0 candidate feature — content-agnostic public extension seam for new envelope kinds + renderers.
> **First consumer:** `pepper-roots` Desire plugin (private repo).

## 0. Preamble — context + architectural intent

### How we got here

The morning's spec drafted a `metadata.want.*` namespace + `_inbox_attrs` surfacing for the wake-self-A primitive. After philosophy + minion research, Pepper and Wren converged on "no public agent_core change; mediation stays internal." That convergence relayed to Jeff at midday.

Jeff's reframe at 4pm:

> *"i want to extend the bus to have 'arbitrary' extensions. we need to be able to add extensions that dont have shapes yet as well as renderers. that way you can inject a random 'Thought' or 'Desire' and the renderers would render it properly. ... it isn't just extending the base message and it isnt something you have to think more about by digging into meta info."*

He overrode the no-public-change convergence in one specific direction: the **capability** is worth landing in public agent_core, even though specific **content** (Desire, Thought) stays per-plugin.

The reconciliation with the philosophy work:
- **Content** (e.g., `Desire` semantics, `metadata.desire.*` shape) → private, lives in plugin (pepper-roots)
- **Convention** (e.g., "this is what a Desire looks like") → private, lives in plugin
- **Mediation** (the discipline of re-evaluating past-Desire) → private, lives in CLAUDE.md
- **Capability** (the bus can be extended with new envelope kinds + renderers) → **PUBLIC**, lives in agent_core

The earlier ego-leak concern was specifically about Want-content-as-public-infra. Capability-as-public-infra is a structurally different shape: the bus knows that plugins exist; it doesn't know what they are or mean.

### Architectural intent (load-bearing)

**The bus stays content-agnostic. Plugins contribute first-class envelope kinds + their own renderers. The seam is general; the specific kinds (Desire today, Thought / Memory / Reflection tomorrow) are plugin-private.**

This matches Letta's `letta.system` adapter prior art (minion-2 finding earlier today): runtime owns delivery; adapter layer owns wrap construction; agent owns evaluation. The runtime/adapter split is what we're adding.

## 1. Decisions locked

1. **Envelope.kind opens to `str`.** Existing built-in kinds remain valid str literals. Plugin kinds become valid by registration.

2. **Plugin renderers are first-class via hookspec.** Renderer functions are registered at plugin load; rendering.py dispatches to them for kinds not in the built-in renderer set.

3. **Built-in payload validation stays strict.** Existing Pydantic models for TextMessage/Event/etc. remain unchanged; plugin kinds validate via plugin code (deferred hookspec; see §7).

4. **Backward compatibility is required.** All existing code doing `envelope.kind == "TextMessage"` works unchanged. All existing typed payload access (`envelope.payload.text`) works unchanged for built-in kinds.

5. **No payload-validator hookspec in v0.** Plugins validate their own payload shapes via their own code (e.g., Pydantic at the plugin level). If we observe friction across N plugins, lift to a hookspec later. YAGNI for one plugin (Desire).

6. **First consumer: pepper-roots `Desire` plugin.** Validates the seam empirically. Wren-side adoption (if/when) is the second consumer.

## 2. Components

### 2.1 Schema change — `bus/envelope.py`

```python
class Envelope(BaseModel):
    # ... existing fields ...
    kind: str   # was Literal["TextMessage", "Event", ...]
    payload: EnvelopePayload | dict[str, Any]
    # ... existing fields ...

    @model_validator(mode="after")
    def validate_kind_matches_payload(self) -> "Envelope":
        # For built-in kinds, payload is a Pydantic model; check .kind matches.
        if hasattr(self.payload, "kind"):
            if self.payload.kind != self.kind:
                raise ValueError(...)
        else:
            # Plugin kind with dict payload: check dict has matching "kind" key.
            if isinstance(self.payload, dict) and self.payload.get("kind") != self.kind:
                raise ValueError(...)
        return self
```

The `EnvelopePayload` discriminated union stays as-is for built-in kinds; the `| dict[str, Any]` alternative catches plugin payloads.

### 2.2 Hookspec addition — `plugins/specs.py`

```python
@hookspec
def register_envelope_renderers() -> dict[str, Callable[[dict], str]]:
    """Return {kind: renderer_fn} for plugin-registered envelope kinds.
    
    The renderer takes the envelope dict and returns the rendered body
    string for inclusion inside an <inbox> tag. The kind discriminator
    (e.g., "Desire") becomes a first-class envelope kind once registered.
    
    Duplicate kinds across plugins raise PluginRegistryError at startup.
    """
```

### 2.3 Plugin-manager wiring — `plugins/manager.py`

```python
def get_envelope_renderers(pm: pluggy.PluginManager) -> dict[str, Callable[[dict], str]]:
    return _merge_type_maps(pm.hook.register_envelope_renderers(), kind="envelope-renderer")
```

### 2.4 Renderer dispatch — `agent-core-channel/rendering.py`

The existing `_RENDERERS` dict gets merged with plugin-registered renderers at module init (or first-use; TBD by implementation). Dispatch order:
1. Plugin-registered renderer for `kind` → use it
2. Built-in renderer for `kind` → use it
3. Generic/fallback → use it

Plugin renderers can override built-in renderers if explicitly registered (operator-aware decision; warned at startup if collision).

### 2.5 Plugin author experience

To add a new envelope kind:

```python
# my_plugin/plugin.py
from pydantic import BaseModel
from typing import Literal
from agent_core.plugins.specs import hookimpl

class DesirePayload(BaseModel):
    kind: Literal["Desire"] = "Desire"
    text: str
    desire_created_at: str
    desire_created_by: str

def render_desire(envelope: dict) -> str:
    payload = envelope["payload"]
    return (
        f"<desire created_at='{payload['desire_created_at']}' "
        f"created_by='{payload['desire_created_by']}'>"
        f"{payload['text']}"
        f"</desire>"
    )

@hookimpl
def register_envelope_renderers():
    return {"Desire": render_desire}
```

Then to publish:
```python
envelope = Envelope(
    id=...,
    kind="Desire",
    payload={"kind": "Desire", "text": "...", "desire_created_at": "...", "desire_created_by": "..."},
    ...
)
bus.publish(envelope)
```

At fire time, the inline wake rendering surfaces a `<desire ...>` tag with the renderer's output.

## 3. Data flow

```
Plugin loaded at startup
  │
  ▼
PluginManager.register() invokes register_envelope_renderers hookimpl
  ↓
get_envelope_renderers(pm) collects {kind: renderer_fn} mappings
  ↓
rendering.py's _RENDERERS dict gets merged
  ↓
(later) Plugin sends envelope with kind="Desire":
  envelope.kind="Desire", envelope.payload={...}
  ↓
Bus delivers envelope to target endpoint
  ↓
Endpoint queues envelope; debounced wake fires
  ↓
agent-core-channel inline hydration calls render_envelope(envelope):
  → looks up renderer for kind="Desire" in _RENDERERS
  → finds plugin-registered render_desire
  → invokes it; gets back "<desire ...>...</desire>"
  ↓
Wake notification arrives at substrate with <inbox><desire ...>...</desire></inbox>
```

## 4. Testing

### 4.1 Built-in regression
- All existing kinds (TextMessage, Event, ToolInvocation, Cancellation, Progress, Acknowledgment) validate + render exactly as before. Existing test suite passes unchanged.

### 4.2 Plugin-renderer dispatch
- A test plugin registers `register_envelope_renderers() -> {"TestExt": fn}`. Envelope with `kind="TestExt"` is rendered via the plugin's fn.

### 4.3 Schema opens to str
- Envelope with `kind="TestExt"` and `payload={"kind": "TestExt", "text": "x"}` validates without error.
- Envelope with mismatched kind (`kind="A", payload.kind="B"`) raises validation error (covers both built-in and plugin paths).

### 4.4 Collision detection
- Two plugins registering the same kind raise `PluginRegistryError` at startup.

### 4.5 Fallback
- Envelope with unknown plugin kind (no renderer registered) falls back to generic JSON rendering (no crash).

## 5. Acceptance criteria

- [ ] Schema change in envelope.py with backward-compat validation
- [ ] Hookspec added to plugins/specs.py + helper in plugins/manager.py
- [ ] Renderer dispatch in rendering.py wired to plugin registry
- [ ] §4 tests all pass; existing tests unaffected
- [ ] Documentation: brief section in agent_core README + spec doc committed
- [ ] PR merged to main with CI green
- [ ] Per [[artifact-verified-task-done]]: every acceptance criterion artifact-verified at completion

## 6. Deferred / open questions

**6.1 Payload-validator hookspec.** Plugins validate their own payload shapes for v0. If 2+ plugins both want bus-side validation, add a hookspec for plugin-registered Pydantic validators. YAGNI today.

**6.2 Bus-log projector extension.** Existing bus_log/projectors.py has plugin-aware projection (the `register_bus_log_projectors` hookspec exists already). Plugin kinds will need projector entries; that uses the existing seam unchanged. No new work needed for v0.

**6.3 Cross-plugin kind collision policy.** v0 raises at startup. v0.1 could add namespace prefixes ("desire" → "pepper-roots.Desire") if collision becomes real.

**6.4 Renderer for inbox-tag attributes.** Currently `_inbox_attrs` is the framework attrs (kind/from/urgency/envelope_id) + discord-namespace metadata. Plugin renderers handle the BODY but not the attrs. If a plugin needs custom attrs on the inbox tag itself, that's a separate hookspec. v0.1+ if needed.

**6.5 Versioning of plugin-registered kinds.** Not in v0. If two plugin versions register the same kind with different renderers, last-loaded wins (warning logged). Formal versioning if cross-plugin compatibility becomes a real concern.

**6.6 Renderer authority/trust model.** Plugins run as in-process code; no sandboxing. Same trust shape as existing bus-hook plugins. Out of scope.

## 7. Cross-references

- Existing pluggy infrastructure: `plugins/specs.py`, `plugins/manager.py` (this PR adds one hookspec)
- Existing rendering pipeline: `agent-core-channel/rendering.py` (this PR extends `_RENDERERS` dispatch)
- Prior art: Letta's `letta.system` adapter (minion-2 finding from 2026-05-25 morning research)
- Philosophy convergence: morning brainstorm landed on "internal mediation"; afternoon reframe added "+capability seam"; this spec implements the latter
- First consumer: `jeffrichley/pepper-roots` (private repo, Desire plugin)
- Historical predecessor: `chore/wake-self-A-want-namespace` branch (local-only, pre-reframe draft)
- Related memory: [[artifact-verified-task-done]] (gates the acceptance criteria), [[prove-before-claim]] (meta-rule §5 testing applies)

## 8. Sign-offs

- [ ] Pepper criterion-check on architectural shape + acceptance criteria
- [ ] Wren implementation per §2 components, §4 tests
- [ ] No additional Jeff sign-off needed per his "do all 1-4 without me" delegation
