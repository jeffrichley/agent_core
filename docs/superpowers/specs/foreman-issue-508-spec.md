# Spec: document bus config keys in prose reference page (issue #508)

## Goal

Create `docs/reference/bus-config.md` — a standalone prose reference that lists every `bus:` and `bus.supervisor:` YAML key with its type, default, and effect — so adopters no longer have to reverse-engineer `agent_core.yaml` comments to understand the config surface. Update the sample file(s) and the existing "Configuration knobs" section in `docs/concepts/bus.md` to link to the new page. Addresses sub-ticket #508 of #398 (Theme F Track A, issue #262).

## Acceptance criteria

- `docs/reference/bus-config.md` exists and documents every key in `BusSectionConfig` (`packages/core/src/agent_core/bus/config.py:40–56`) and `SupervisorSectionConfig` (`packages/core/src/agent_core/bus/config.py:23–37`): `storage_path`, `redelivery_timeout_seconds`, `max_delivery_attempts`, `ttl_sweep_seconds`, `redelivery_sweep_seconds`, `acked_retention_days`, `max_pending_per_endpoint`, `slow_deliver_warn_seconds`, `watchdog_timeout_seconds`, `backup_dir`, `backup_interval_seconds`, and all 10 `supervisor` sub-keys. Each entry states the YAML key name, type (as it appears in YAML, e.g. `string`, `integer`, `string | null`), default value, and a 1–3 sentence effect description.
- `mkdocs.yml` nav exposes `docs/reference/bus-config.md` so it appears in the site sidebar under the Reference section (required: `strict: true` in `mkdocs.yml` makes an orphaned page a build warning).
- `docs/reference/index.md` includes a link to `reference/bus-config.md` beneath the existing API Reference content.
- `docs/concepts/bus.md` "Configuration knobs" section links to `reference/bus-config.md` instead of (or in addition to) the generic "see the API Reference" pointer.
- `agent_core.yaml` has a comment above the `bus:` block pointing to the new page (e.g. `# Full key reference: docs/reference/bus-config.md`).
- `docs/examples/being-agent-core.yaml` has the same comment above its `bus:` block.
- `mkdocs build --strict` passes after the changes (no broken internal links, no nav-orphan warnings).

## Approach

No GoF pattern applies. This is a documentation task following the single-responsibility principle: the new `docs/reference/bus-config.md` page becomes the single source of truth for bus config key semantics. Today that information is scattered across inline YAML comments in `agent_core.yaml`, brief inline comments in `packages/core/src/agent_core/bus/core.py`, and the partial table in `docs/concepts/bus.md`. Centralising it into a reference page + linking from all the current fragmentary sources is the minimum change that satisfies the issue.

**Source of truth for key names and defaults.** Every key name and default value in the reference page MUST be read from `packages/core/src/agent_core/bus/config.py` (the Pydantic config schema), not from the sample YAML or memory. The source file is the authoritative spec. The `BusSectionConfig` (maps to the `bus:` YAML block) and `SupervisorSectionConfig` (maps to `bus.supervisor:`) are both defined there; the runtime `BusConfig` dataclass in `core.py` carries additional inline comments that document intent and are worth consulting for the effect descriptions.

**Reference page structure.** Two H2 sections: `## bus:` covering the 11 top-level keys, and `## bus.supervisor:` covering the 10 supervision-layer keys. Within each section, each key is a definition-list entry (or a small table row) with `Type`, `Default`, and `Effect` columns. The page should open with a short orientation paragraph explaining that the config lives in `agent_core.yaml` and that `extra="forbid"` means any typo'd key name raises a `ValidationError` at boot.

**MkDocs nav.** Currently the nav has `- API Reference: reference/index.md` (a leaf entry). This must be expanded to a section so both `reference/index.md` and `reference/bus-config.md` appear in the sidebar:
```yaml
  - Reference:
      - API reference: reference/index.md
      - Bus config keys: reference/bus-config.md
```
This is a one-line restructure of the `nav:` block.

**Sample-file links.** The comment added to `agent_core.yaml` and `docs/examples/being-agent-core.yaml` should be a single line above the `bus:` key, referencing the docs URL or relative path. Using the relative doc path (`docs/reference/bus-config.md`) is preferred over an absolute site URL, as it remains correct locally and in the published site.

## Sub-requests (topologically sorted)

1. **Create `docs/reference/bus-config.md`** — the full prose reference page. Read every key name and default from `packages/core/src/agent_core/bus/config.py:23–56`; read effect descriptions from inline comments in `packages/core/src/agent_core/bus/core.py:87–111` and `bus.md`'s Configuration knobs table. The page must be `mkdocstrings`-free (no `:::` autodoc directives); it is authored prose only.

2. **Update `mkdocs.yml`** — expand the `- API Reference: reference/index.md` leaf entry into a two-entry section so `reference/bus-config.md` is visible in the sidebar nav.

3. **Update `docs/reference/index.md`** — add a "Config reference" subsection below the existing content that names `bus-config.md` and links to it: `[Bus config keys](bus-config.md)`.

4. **Update `docs/concepts/bus.md`** — replace the final sentence in the "Configuration knobs" section (`"For exact field names and defaults, see the [API Reference](../reference/index.md)."`) with a link to the new page: `"For the complete key list with types and defaults, see [Bus config keys](../reference/bus-config.md)."`. Keep the existing summary table intact; the link supplements it.

5. **Update `agent_core.yaml`** — add one comment line immediately above the `bus:` key (line 18):
   ```yaml
   # Full key reference: docs/reference/bus-config.md
   bus:
   ```

6. **Update `docs/examples/being-agent-core.yaml`** — add the same comment line immediately above its `bus:` key (currently line 27):
   ```yaml
   # Full key reference: docs/reference/bus-config.md
   bus:
   ```

## File-level changes

| File | Action | What changes |
|---|---|---|
| `docs/reference/bus-config.md` | **Create** | New prose reference page: all `bus:` and `bus.supervisor:` keys with type, default, effect |
| `mkdocs.yml` | **Modify** | Expand `- API Reference: reference/index.md` into a two-entry `Reference:` section including `bus-config.md` |
| `docs/reference/index.md` | **Modify** | Add a "Config reference" subsection linking to `bus-config.md` |
| `docs/concepts/bus.md` | **Modify** | Update "Configuration knobs" final sentence to link to `bus-config.md` |
| `agent_core.yaml` | **Modify** | Add one-line comment above `bus:` pointing to `docs/reference/bus-config.md` |
| `docs/examples/being-agent-core.yaml` | **Modify** | Add one-line comment above `bus:` pointing to `docs/reference/bus-config.md` |

No Python source files are modified. No new tests are needed (docs-only change; the `mkdocs build --strict` build is the gate).

## Alternatives considered

1. **Expand the existing "Configuration knobs" table in `docs/concepts/bus.md` in-place rather than creating a new page.** The concepts page is scoped to "the mental model" (per `concepts/index.md`); embedding a full reference table there mixes conceptual and reference content. The repo already has a `docs/reference/` tree with `reference/index.md` as the designated reference landing page — creating a sibling page there is the right fit. Ruled out.

2. **Rely solely on mkdocstrings auto-generation from `BusConfig` and `SupervisorConfig` docstrings.** The `BusConfig` in `core.py` uses `@dataclass` with inline `#` comments, not Google-style docstrings. `mkdocstrings` does not render `#` comments as field documentation; the auto-generated output shows field names and types but no effect descriptions. The `BusSectionConfig` in `config.py` also has no per-field docstrings. Authored prose is required to capture the effect descriptions. Ruled out.

3. **Only update `docs/concepts/bus.md` without a dedicated reference page.** The issue's "done when" criteria is "every bus config key documented with type + default + effect" — the current `bus.md` table already lists key names without types or defaults, and the link to "see the API Reference" is a dead end since the auto-generated reference doesn't document config YAML keys at the per-field level. A dedicated reference page is the minimum change that satisfies the criteria. Ruled out.

## Open questions

None. The key names, types, and defaults are fully readable from `packages/core/src/agent_core/bus/config.py`. Effect descriptions are documented in inline comments in `core.py` and the existing `bus.md` prose.

## Out of scope

- Per-package READMEs for `core`, `busproxy`, and other packages (the rest of A2-5, separate sub-tickets).
- "Add an endpoint" reference guide (also A2-5, separate).
- `docs/examples/pepper-agent-core.yaml` — Pepper-specific file being de-Wren-ified in A2-3 (#504); touch-free here.
- `docs/examples/voice-agent-core.yaml` — does not include a full `bus:` block (only `storage_path`) so the link comment is unnecessary there.
- Documenting `http:`, `bus_hooks:`, `logging:`, `mcp_audit:`, or `endpoints:` config sections — the issue asks specifically for bus config keys only.
- Expanding `BusConfig` or `BusSectionConfig` docstrings in source code — a separate concern; doc page is sufficient for adopters.
