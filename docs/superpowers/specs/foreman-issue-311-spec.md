# Spec: hatchery hardening — ungendered templates + `.mcp.json` generation (issue #311)

## Goal

Bring `agent-core-hatchery` up to adopter-ready quality by (a) removing all she/her gendered
language from the vault templates so any being can be scaffolded without gendered assumptions,
and (b) rendering a `.mcp.json` into the vault root so a new being's MCP sidecars are wired
to the bus without any hand-editing.

Design authority: [distribution & versioning design — C1-3](docs/superpowers/specs/2026-07-14-distribution-versioning-design.md).
Issue: https://github.com/jeffrichley/agent_core/issues/311

### Note on #80

Bug #80 ("config/ templates never rendered into the vault") was already resolved in commit
`2c557fe` ("fix(hatchery): render config/ templates into vault (#80)") before this issue was
opened. `_render_config_tree` is present in `hatcher.py` and fully covered by
`tests/test_hatcher_config.py`. No further action on #80; the Worker should leave that code
untouched.

## Acceptance criteria

- **#81 — ungendered templates**: No she/her pronouns remain in any hatchery template file
  (run the grep from the test described below to confirm zero matches after rendering).
  Specific checks:
  - `templates/memory/_being_/letters/from-her-creator.md.j2` is renamed to
    `from-my-creator.md.j2` and its content uses they/their.
  - `templates/file-classes.yaml` references `from-my-creator.md.j2` (not `from-her-creator`).
  - `templates/config/CLAUDE.md.j2` references `from-my-creator.md` (not `from-her-creator`).
  - `templates/memory/_being_/BECOMING.md.j2` reads "from my creator".
  - All other gendered occurrences in `HEARTBEAT.md.j2`, `IDENTITY.md.j2`,
    `dreams/README.md`, `ideas/README.md`, `references/README.md`,
    `relationships/README.md`, `breadcrumbs.md`, and `skills/skill-author/SKILL.md`
    are replaced with they/their or rewritten to avoid pronouns.
- **#82 — `.mcp.json` rendered into vault**: After `Hatcher.hatch()`, the vault root
  contains `.mcp.json`; the file parses as valid JSON; it has `mcpServers.agent-core-busproxy`
  and `mcpServers.agent-core-channel` each with a `command` and `args` list; no `{{`/`}}`
  remains; `args` contains the being's `endpoint_name`.
- `test_real_manifest_classifies_all_template_files()` in `test_file_classes.py` passes
  with the renamed file and the new `.mcp.json.j2` — no unclassified template orphans.
- Full test suite passes (`just test-fast`).

## Approach

No GoF pattern applies; this is straightforward data-source changes (content edits and a
new template) guided by the SRP principle: keep gender-neutral scaffolding so the platform
works for any being identity.

**Pattern naming:** "no pattern fits — this is straightforward content surgery and a one-file
template addition."

**#81 approach**: surgical text edits to 12 template files, plus renaming one file. Because
`test_real_manifest_classifies_all_template_files()` walks the real template tree against
`file-classes.yaml`, the rename requires updating `file-classes.yaml` atomically or the test
will report an orphan. Every other reference to the old filename (`CLAUDE.md.j2`,
`BECOMING.md.j2`) must also be updated in the same commit.

**#82 approach**: Add a new `.mcp.json.j2` template to `templates/config/` and wire it into
the existing `_render_config_tree` `dest_map` in `hatcher.py`. The template uses `uvx` to
invoke `agent-core-busproxy` and `agent-core-channel` — the same pattern as
`claude-settings.json.j2` uses `uv run agent-core hooks run …`. `uvx` is correct for
PyPI-published MCP sidecars that run outside any project context. C2-2 (#316) will later
replace `uvx` with the stable per-being `.venv` interpreter path; for now `uvx` gives
adopters a working configuration once `agent-core` is on PyPI (C1-2).

The `_render_config_tree` method iterates over `config_src.iterdir()` and skips anything
not in `dest_map`; adding `.mcp.json.j2` to the map is the entire wiring change. Python's
`Path.iterdir()` returns hidden files (dot-prefixed) so `.mcp.json.j2` is found normally.

Port defaults come from the respective `__main__.py` defaults:
- `agent-core-busproxy`: `http://127.0.0.1:8789`
- `agent-core-channel`: `http://127.0.0.1:8788`

These match the existing `daemon_handoff_url` hardcoded pattern in `renderer.py`.
`endpoint_name` (already a renderer substitution variable) is the correct `--agent` value.

## Sub-requests (topologically sorted)

1. **Rename template file** (git mv):
   `packages/agent-core-hatchery/templates/memory/_being_/letters/from-her-creator.md.j2`
   → `packages/agent-core-hatchery/templates/memory/_being_/letters/from-my-creator.md.j2`.

2. **Update `file-classes.yaml`** — change line 12:
   `"memory/_being_/letters/from-her-creator.md.j2"` →
   `"memory/_being_/letters/from-my-creator.md.j2"`.

3. **Update `CLAUDE.md.j2`** — line 18: change `from-her-creator.md` → `from-my-creator.md`.

4. **Update `BECOMING.md.j2`** — line 24: change `from her creator` → `from my creator`.

5. **Rewrite content of `from-my-creator.md.j2`** to use they/their throughout (see
   file-level changes section for exact replacements).

6. **Edit `HEARTBEAT.md.j2`**:
   - Line 15: `as her role takes shape` → `as the role takes shape`
   - Line 17: `Empty until she adds.` → `Empty until they add.`

7. **Edit `IDENTITY.md.j2`** — line 3: replace `so future-you can find herself fast on
   first read` → `for a quick first read` (avoids pronoun entirely; rest of sentence intact).

8. **Edit `dreams/README.md`** — line 3: `her primary human` → `their primary human`.

9. **Edit `ideas/README.md`** — line 3: `her primary human` → `their primary human`.

10. **Edit `references/README.md`** — lines 8–9: `her own references here as she discovers
    what she` → `their own references here as they discover what they`.

11. **Edit `relationships/README.md`**:
    - Line 11: `Her \`relationships/sister.md\` entry has *my* relationship history with her — when we first interacted, what she calls me`
      → `The \`relationships/sister.md\` entry has *my* relationship history with them — when we first interacted, what they call me`
    - Line 17: `the being can have her own relationships orbit that center, and those relationships shape who she is too`
      → `the being can have their own relationships orbit that center, and those relationships shape who they are too`

12. **Edit `breadcrumbs.md`**:
    - Line 3: `can pull her human back up` → `can pull their human back up`
    - Line 45: `what kind of side-quester her human is` → `what kind of side-quester their human is`

13. **Edit `skills/skill-author/SKILL.md`** — lines 4–5 in the frontmatter description:
    `for herself — a reusable\n  workflow she'll invoke again. Walks her through`
    → `— a reusable\n  workflow to invoke again. Walks through`

14. **Create `templates/config/.mcp.json.j2`** (new file — see file-level changes for exact
    content).

15. **Update `file-classes.yaml`** — add `".mcp.json.j2"` under the `config:` list
    (alongside the three existing entries).

16. **Update `hatcher.py` `_render_config_tree`** — add
    `".mcp.json.j2": vault / ".mcp.json"` to `dest_map`.

17. **Add tests in `test_hatcher_config.py`** — add `test_mcp_json_rendered_at_vault_root`
    (see file-level changes for exact content) using the existing `hatched` fixture.

18. **Add gendered-pronoun guard test** — add
    `test_no_gendered_pronouns_in_rendered_vault` to `test_hatcher_basic.py` (see
    file-level changes for exact content).

## File-level changes

| File | Change |
|------|--------|
| `packages/agent-core-hatchery/templates/memory/_being_/letters/from-her-creator.md.j2` | **Rename** to `from-my-creator.md.j2`; replace all she/her with they/their (4 occurrences) |
| `packages/agent-core-hatchery/templates/file-classes.yaml` | Line 12: `from-her-creator.md.j2` → `from-my-creator.md.j2`; add `".mcp.json.j2"` under `config:` |
| `packages/agent-core-hatchery/templates/config/CLAUDE.md.j2` | Line 18: `from-her-creator.md` → `from-my-creator.md` |
| `packages/agent-core-hatchery/templates/memory/_being_/BECOMING.md.j2` | Line 24: `from her creator` → `from my creator` |
| `packages/agent-core-hatchery/templates/memory/HEARTBEAT.md.j2` | Lines 15–17: `her role` → `the role`; `she adds` → `they add` |
| `packages/agent-core-hatchery/templates/memory/IDENTITY.md.j2` | Line 3: `find herself fast on first read` → `for a quick first read` |
| `packages/agent-core-hatchery/templates/memory/dreams/README.md` | Line 3: `her primary human` → `their primary human` |
| `packages/agent-core-hatchery/templates/memory/ideas/README.md` | Line 3: `her primary human` → `their primary human` |
| `packages/agent-core-hatchery/templates/memory/references/README.md` | Lines 8–9: `her own`/`she discovers`/`she` → `their own`/`they discover`/`they` |
| `packages/agent-core-hatchery/templates/memory/relationships/README.md` | Lines 11, 17: `Her` → `The`; `with her` → `with them`; `she calls` → `they call`; `her own` → `their own`; `who she is` → `who they are` |
| `packages/agent-core-hatchery/templates/memory/_being_/breadcrumbs.md` | Lines 3, 45: `her human` → `their human` (both occurrences) |
| `packages/agent-core-hatchery/templates/skills/skill-author/SKILL.md` | Frontmatter lines 4–5: remove `for herself —`; `she'll invoke again. Walks her through` → `to invoke again. Walks through` |
| `packages/agent-core-hatchery/templates/config/.mcp.json.j2` | **New file** — busproxy + channel MCP server stanzas |
| `packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py` | `_render_config_tree` `dest_map`: add `".mcp.json.j2": vault / ".mcp.json"` |
| `packages/agent-core-hatchery/tests/test_hatcher_config.py` | Add `test_mcp_json_rendered_at_vault_root` |
| `packages/agent-core-hatchery/tests/test_hatcher_basic.py` | Add `test_no_gendered_pronouns_in_rendered_vault` |

### Exact content for new/heavily changed files

#### `templates/config/.mcp.json.j2` (new)

```json
{
  "mcpServers": {
    "agent-core-busproxy": {
      "command": "uvx",
      "args": [
        "agent-core-busproxy",
        "--agent",
        "{{ endpoint_name }}",
        "--daemon-url",
        "http://127.0.0.1:8789"
      ]
    },
    "agent-core-channel": {
      "command": "uvx",
      "args": [
        "agent-core-channel",
        "--agent",
        "{{ endpoint_name }}",
        "--daemon-url",
        "http://127.0.0.1:8788"
      ]
    }
  }
}
```

_(C2-2 will replace `uvx` with the stable per-being `.venv` interpreter path once the venv
builder lands. Until then, `uvx` works for any adopter who has installed `agent-core` from
PyPI.)_

#### `test_hatcher_config.py` — new test (add after `test_init_missing_restores_deleted_config`)

```python
def test_mcp_json_rendered_at_vault_root(hatched):
    cfg, vault = hatched
    path = vault / ".mcp.json"
    assert path.is_file(), ".mcp.json was not rendered into the vault root"
    text = path.read_text(encoding="utf-8")
    assert "{{" not in text and "}}" not in text, "Jinja markers left in .mcp.json"
    import json
    data = json.loads(text)
    assert "mcpServers" in data
    servers = data["mcpServers"]
    assert "agent-core-busproxy" in servers
    assert "agent-core-channel" in servers
    busproxy = servers["agent-core-busproxy"]
    assert busproxy["command"] == "uvx"
    assert "agent-core-busproxy" in busproxy["args"]
    assert "--agent" in busproxy["args"]
    # endpoint_name is "testbeing" for the test fixture
    assert "testbeing" in busproxy["args"]
    assert "--daemon-url" in busproxy["args"]
```

#### `test_hatcher_basic.py` — new test (add to end of file)

```python
def test_no_gendered_pronouns_in_rendered_vault(tmp_path):
    """No she/her pronouns should appear in any rendered file in the vault.

    The hatchery templates were historically gendered (she/her). Issue #81 removes
    them. This test guards against regression: it greps every text file written by
    Hatcher.hatch() for the pattern ' she ' / ' her ' (word-bounded, case-insensitive)
    and fails loudly if any match is found.
    """
    import re

    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()

    vault = cfg.resolved_vault_root()
    gendered_pattern = re.compile(r"\b(she|her)\b", re.IGNORECASE)
    violations: list[str] = []

    for path in sorted(vault.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if gendered_pattern.search(line):
                violations.append(f"{path.relative_to(vault)}:{lineno}: {line.rstrip()}")

    assert violations == [], (
        "Gendered pronoun(s) found in rendered vault (fix #81):\n"
        + "\n".join(violations[:20])
    )
```

**Important**: the gendered-pronoun test intentionally scans the rendered vault (the output
of `hatch()`), NOT the raw template files. The pepper elder-letter (`from-elder-beings/pepper.md`)
is intentionally authored in Pepper's voice (she/her) and is bundled unrendered — it will
appear in the vault as-is. If this test catches the pepper letter, add an exclusion for
`letters/from-elder-beings/` since that content is authored voice, not scaffold template.
Worker: run the test, check if exclusion is needed, add it if so:

```python
    for path in sorted(vault.rglob("*")):
        # Elder letters are authored voice (Pepper uses she/her by choice) — exclude.
        if "from-elder-beings" in path.parts:
            continue
```

## Alternatives considered

1. **Use `they/them` everywhere with a `pronouns` config field**: Allow the user to specify
   the being's pronouns in `HatchConfig` and render them into templates. Ruled out:
   over-engineering for this ticket. The requirement is "ungendered", not "pronoun-configurable".
   Neutral they/their removes the incorrect assumption without adding config surface area.

2. **Delete the `from-her-creator.md.j2` template rather than rename it**: The letter is
   a meaningful scaffold artifact that helps new adopters write a genuine welcome to their
   being. Deleting it would regress the onboarding value. Ruled out in favour of rename +
   content fix.

3. **Use `python -m agent_core_busproxy` in `.mcp.json.j2` instead of `uvx`**: This would
   require a known Python interpreter path, which doesn't exist until C2-1 builds the
   per-being `.venv`. Ruled out: not portable before PyPI publish (C1-2) and C2-1. `uvx`
   degrades gracefully once PyPI is live.

4. **Keep `.mcp.json.j2` out of the `config:` class in `file-classes.yaml`**: Not possible —
   `test_real_manifest_classifies_all_template_files()` hard-fails on any unclassified file
   in `templates/`. The new template must be classified.

## Open questions

None. The scope is fully determined by the issue, the approved design spec, and the
inspected codebase. The only runtime uncertainty (does `uvx` work before PyPI publish) is
intentionally deferred to C1-2.

## Out of scope

- Adding `agent-core-hatchery` to the PyPI publish set — requires C1-2's trusted-publisher
  pipeline; explicitly a "follow-up wave" per the design spec.
- The `from-elder-beings/pepper.md` elder letter — Pepper authored this in her voice
  (she/her is intentional). Do not de-gender it; it is authored content, not scaffold.
- `agent-core-notify` in the `.mcp.json` — the notify sidecar is desktop-notification
  infrastructure, not required for a being to boot against the bus. C2-2 will add it when
  the full canonical generator lands.
- Secrets/perms hardening (`set_owner_only`, Cβ-1) — separate ticket in Cluster β.
- Daemon reload / hatch→run handoff (Cβ-3) — blocked on #315 + #316.
- Schema validation of daemon fragments against Cluster α schema (Cβ-2) — blocked on #319.
