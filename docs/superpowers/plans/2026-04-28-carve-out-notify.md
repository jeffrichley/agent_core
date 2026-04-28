# Carve out `agent-core-notify` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the notify subsystem out of `packages/core/` into its own workspace member at `packages/notify/`, shipping as `agent-core-notify` on PyPI. Module rename: `agent_core.notify` → `agent_core_notify` (separate top-level per spec §5.2).

**Architecture:** New workspace member `packages/notify/` holds the notify MCP server: `pyproject.toml`, `src/agent_core_notify/`, `towncrier.toml`, `changelog.d/`, `CHANGELOG.md`. `desktop-notifier` dep moves out of core into the new package (it's notify-only). Root `pyproject.toml`, `.mcp.json`, and `.importlinter` get small updates. No tests move (notify has none today). No `agent-core` runtime dep is added — notify imports nothing from `agent_core` today.

**Tech Stack:** uv workspace, hatchling, pytest, ruff, import-linter, towncrier — all already wired up by Step 1.

**Spec:** [`docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md`](../specs/2026-04-28-monorepo-workspace-design.md)

**Migration step:** This is migration **Step 2a** (notify only). Step 2b (email carve-out) is deferred to a separate plan after sub-project B (lifecycle CLI / plugin discovery) resolves how core's CLI registers subcommands from sibling packages without a hard import.

**Scope guardrails:**
- This plan ships only the notify carve-out. Email stays in core untouched.
- The only Python content edits are the two notify files' docstrings (module renamed). Everything else is configuration.
- Pepper is not touched.

---

## Conventions to follow throughout

- Run on a feature branch off `main`; do not commit directly to `main`.
- Use `git mv` for renames so git records them as renames.
- Commit messages follow Conventional Commits: `chore(notify):`, `build(notify):`, `docs(notify):`, etc.
- After every code-changing commit, add a towncrier fragment in the relevant package's `changelog.d/`.
- Baselines (verify they hold throughout):
  - pytest: **183 passed, 2 skipped**
  - ruff: **9 errors** (pre-existing on main, out of scope)
  - import-linter: **all contracts pass**

---

## File structure summary

**Created in this plan:**
- `packages/notify/pyproject.toml`
- `packages/notify/towncrier.toml`
- `packages/notify/CHANGELOG.md`
- `packages/notify/changelog.d/.gitkeep`
- `packages/notify/changelog.d/+carve-notify.added.md`

**Moved (via `git mv`):**
- `packages/core/src/agent_core/notify/` → `packages/notify/src/agent_core_notify/`

**Modified:**
- `packages/notify/src/agent_core_notify/__init__.py` — docstring updated to new module name
- `packages/notify/src/agent_core_notify/mcp_server.py` — docstring's launch-instruction comment updated
- `packages/core/pyproject.toml` — remove `desktop-notifier` dep, remove `agent-core-notify` script
- `pyproject.toml` (root) — add `agent-core-notify` to `[tool.uv.sources]`, add `packages/notify/src` to `[tool.ruff] src`
- `.mcp.json` — change `agent_core.notify.mcp_server` → `agent_core_notify.mcp_server`
- `.importlinter` — add `agent_core_notify` to `root_packages`

**Deleted:**
- (nothing)

---

## Task 1: Pre-flight checks and feature branch

**Files:** none (read-only verification + branch creation).

- [ ] **Step 1: Verify clean working tree on `main`**

Run: `git status -sb && git rev-parse --abbrev-ref HEAD`
Expected: clean working tree, on branch `main`, in sync with `origin/main`. If not, stop.

- [ ] **Step 2: Snapshot current passing state**

Run: `uv run --no-sync pytest -q && uv run --no-sync ruff check . | tail -3 && uv run --no-sync lint-imports`
Expected:
- pytest: `183 passed, 2 skipped`
- ruff: `Found 9 errors. [*] 7 fixable...`
- lint-imports: `Contracts: 1 kept, 0 broken.`

If any number differs, stop and surface the discrepancy.

- [ ] **Step 3: Create the feature branch**

Run: `git checkout -b feat/carve-out-notify`
Expected: `Switched to a new branch 'feat/carve-out-notify'`.

---

## Task 2: Move and rename the notify subsystem

**Files:**
- Move: `packages/core/src/agent_core/notify/` → `packages/notify/src/agent_core_notify/`
- Modify (after move): `packages/notify/src/agent_core_notify/__init__.py`
- Modify (after move): `packages/notify/src/agent_core_notify/mcp_server.py` (docstring only)

- [ ] **Step 1: Create the target parent directory**

Run: `mkdir -p packages/notify/src`
Expected: `packages/notify/src` exists.

- [ ] **Step 2: Move the notify directory and rename the module in one git operation**

Run: `git mv packages/core/src/agent_core/notify packages/notify/src/agent_core_notify`
Expected: no output. `git status` shows two renames:
- `packages/core/src/agent_core/notify/__init__.py` → `packages/notify/src/agent_core_notify/__init__.py`
- `packages/core/src/agent_core/notify/mcp_server.py` → `packages/notify/src/agent_core_notify/mcp_server.py`

Both should appear as `R100` (100% rename, content unchanged) in `git status`.

- [ ] **Step 3: Update the new `__init__.py` docstring**

Open `packages/notify/src/agent_core_notify/__init__.py` and replace its single-line docstring:

From:
```python
"""agent_core.notify — Desktop notification MCP server."""
```

To:
```python
"""agent_core_notify — Desktop notification MCP server."""
```

- [ ] **Step 4: Update `mcp_server.py` launch-instruction docstring**

Open `packages/notify/src/agent_core_notify/mcp_server.py`. The module docstring contains a `Launch:` block referencing the old script path:

```python
Launch:
    agent-core-notify           # stdio transport (for .mcp.json)

Register in .mcp.json:
    {
        "mcpServers": {
            "notify": {
                "command": "uv",
                "args": ["run", "--directory", "E:/workspaces/ai/agents/agent_core", "agent-core-notify"]
            }
        }
    }
```

Replace that block with the current canonical invocation (which avoids the entry-point .exe per the prior fix):

```python
Launch:
    python -m agent_core_notify.mcp_server   # stdio transport (for .mcp.json)

Register in .mcp.json:
    {
        "mcpServers": {
            "notify": {
                "command": "uv",
                "args": [
                    "run",
                    "--no-sync",
                    "--directory",
                    "E:/workspaces/ai/agents/agent_core",
                    "python",
                    "-m",
                    "agent_core_notify.mcp_server"
                ]
            }
        }
    }
```

- [ ] **Step 5: Verify no other content edits**

Run: `git diff --stat HEAD -- packages/notify/`
Expected: only the two files above show diffs. The diffs are the docstring updates (small line counts).

- [ ] **Step 6: Commit**

```bash
git add -A packages/notify packages/core/src/agent_core
git commit -m "$(cat <<'EOF'
chore(notify): move notify subsystem to packages/notify with module rename

agent_core.notify → agent_core_notify. Files moved under
packages/notify/src/agent_core_notify/. Module-rename docstring updates
in __init__.py and mcp_server.py. No behavior change.
EOF
)"
```

Expected: commit succeeds. Confirm with `git log --stat -1 | head -10` that the file changes are renames + the two small docstring edits.

---

## Task 3: Create `packages/notify/pyproject.toml` and update root + core pyprojects

**Files:**
- Create: `packages/notify/pyproject.toml`
- Modify: `packages/core/pyproject.toml`
- Modify: `pyproject.toml` (root)

- [ ] **Step 1: Create `packages/notify/pyproject.toml`**

```toml
[project]
name = "agent-core-notify"
version = "0.1.0"
description = "Desktop notification MCP server for agent-core agents"
requires-python = ">=3.12"
dependencies = [
    "desktop-notifier>=5.0",
    "mcp>=1.9.0",
]

[project.scripts]
agent-core-notify = "agent_core_notify.mcp_server:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_notify"]
```

Notes:
- No `agent-core` workspace dep — notify imports nothing from `agent_core` today.
- `mcp` is added explicitly: it was a core dep, but core's MCP usage is for the bus's transport endpoints. Notify uses MCP independently.
- Hatchling `packages = ["src/agent_core_notify"]` is relative to this pyproject.

- [ ] **Step 2: Strip notify-specific items from `packages/core/pyproject.toml`**

Edit `packages/core/pyproject.toml`:

Remove from `[project] dependencies`:
```
    "desktop-notifier>=5.0",
```

Remove from `[project.scripts]`:
```
agent-core-notify = "agent_core.notify.mcp_server:run"
```

The `agent-core` script (the main CLI) stays. After this edit core's `[project.scripts]` block has only one entry.

Verify core's MCP usage is still legitimate: `grep -r "from mcp" packages/core/src/agent_core` should show usages by `agent_core.bus` (the transport endpoint and tests). If so, `mcp>=1.9.0` stays in core's deps.

- [ ] **Step 3: Update root `pyproject.toml`**

In root `pyproject.toml`, edit `[tool.uv.sources]` to add the new package:

From:
```toml
[tool.uv.sources]
agent-core = { workspace = true }
```

To:
```toml
[tool.uv.sources]
agent-core = { workspace = true }
agent-core-notify = { workspace = true }
```

Edit `[tool.ruff]` to add the new src path:

From:
```toml
[tool.ruff]
line-length = 100
src = ["packages/core/src"]
```

To:
```toml
[tool.ruff]
line-length = 100
src = ["packages/core/src", "packages/notify/src"]
```

- [ ] **Step 4: Run `uv sync`**

Run: `uv sync`
Expected: uv resolves the workspace, installs the new `agent-core-notify` editable from `packages/notify`, removes `desktop-notifier` from core's pin (it's now declared by notify instead — same version satisfied).

If `uv sync` errors, recheck `[tool.uv.sources]` and the new `pyproject.toml` syntax.

- [ ] **Step 5: Verify the new `agent-core-notify` script still exists**

Run: `ls .venv/Scripts/agent-core-notify* 2>&1`
Expected: `agent-core-notify.exe` still present (now resolved through `agent_core_notify.mcp_server:run`, not `agent_core.notify.mcp_server:run`).

- [ ] **Step 6: Verify gates still pass**

Run: `uv run --no-sync pytest -q`
Expected: `183 passed, 2 skipped` — unchanged.

Run: `uv run --no-sync ruff check . | tail -3`
Expected: same 9 errors.

- [ ] **Step 7: Commit**

```bash
git add packages/notify/pyproject.toml packages/core/pyproject.toml pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
build(notify): split agent-core-notify into its own workspace member

- packages/notify/pyproject.toml owns the agent-core-notify distribution.
- desktop-notifier dep moves from core to notify (it's notify-only).
- agent-core-notify script entrypoint moves to the new package.
- Root pyproject's [tool.uv.sources] and [tool.ruff].src grow by one entry.
EOF
)"
```

---

## Task 4: Update `.mcp.json`

**Files:**
- Modify: `.mcp.json`

- [ ] **Step 1: Edit `.mcp.json`**

The current value of the `args` array's last element is `"agent_core.notify.mcp_server"`. Change it to `"agent_core_notify.mcp_server"`.

Full file should read:
```json
{
  "mcpServers": {
    "notify": {
      "command": "uv",
      "args": [
        "run",
        "--no-sync",
        "--directory",
        "E:/workspaces/ai/agents/agent_core",
        "python",
        "-m",
        "agent_core_notify.mcp_server"
      ]
    }
  }
}
```

- [ ] **Step 2: Smoke-test the new module path**

Run: `echo '' | timeout 5 uv run --no-sync python -m agent_core_notify.mcp_server; echo "EXIT=$?"`
Expected: server starts, errors on the empty stdin (a known harmless validation error), exits with `EXIT=0` (the `os._exit(0)` safeguard).

If the module can't be found, `uv sync` may not have picked up the new package — re-run `uv sync` and try again.

- [ ] **Step 3: Commit**

```bash
git add .mcp.json
git commit -m "fix(notify): point .mcp.json at agent_core_notify (new module path)"
```

---

## Task 5: Update `.importlinter`

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1: Add `agent_core_notify` as a root package**

Edit `.importlinter`. Update the `[importlinter]` block:

From:
```ini
[importlinter]
root_packages =
    agent_core
```

To:
```ini
[importlinter]
root_packages =
    agent_core
    agent_core_notify
```

The `bus-core-self-contained` contract block stays as-is — the new package is not in scope of that contract (it's not part of `agent_core.bus`).

- [ ] **Step 2: Run `lint-imports`**

Run: `uv run --no-sync lint-imports`
Expected: `Contracts: 1 kept, 0 broken.` (Same as before — adding a root package doesn't add a contract; it makes `agent_core_notify` *eligible* to be referenced by future contracts.)

- [ ] **Step 3: Commit**

```bash
git add .importlinter
git commit -m "chore(notify): register agent_core_notify with import-linter"
```

---

## Task 6: Wire up towncrier for the notify package

**Files:**
- Create: `packages/notify/towncrier.toml`
- Create: `packages/notify/CHANGELOG.md`
- Create: `packages/notify/changelog.d/.gitkeep`
- Create: `packages/notify/changelog.d/+carve-notify.added.md`

- [ ] **Step 1: Create `packages/notify/towncrier.toml`**

```toml
[tool.towncrier]
name = "agent-core-notify"
package = "agent_core_notify"
package_dir = "src"
directory = "changelog.d"
filename = "CHANGELOG.md"
title_format = "## {version} ({project_date})"
issue_format = "[#{issue}](https://github.com/jeffrichley/agent_core/pull/{issue})"

[[tool.towncrier.type]]
directory = "added"
name = "Added"
showcontent = true

[[tool.towncrier.type]]
directory = "changed"
name = "Changed"
showcontent = true

[[tool.towncrier.type]]
directory = "deprecated"
name = "Deprecated"
showcontent = true

[[tool.towncrier.type]]
directory = "removed"
name = "Removed"
showcontent = true

[[tool.towncrier.type]]
directory = "fixed"
name = "Fixed"
showcontent = true

[[tool.towncrier.type]]
directory = "security"
name = "Security"
showcontent = true
```

Note: paths are relative to `packages/notify/` (the config file's directory) — this is the corrected pattern from PR #3.

- [ ] **Step 2: Create `packages/notify/CHANGELOG.md`**

```markdown
# Changelog

All notable changes to `agent-core-notify` are documented in this file. The
format is generated by towncrier from fragments in `changelog.d/` at release
time.

<!-- towncrier release notes start -->
```

- [ ] **Step 3: Create `packages/notify/changelog.d/` with a `.gitkeep`**

```bash
mkdir -p packages/notify/changelog.d
touch packages/notify/changelog.d/.gitkeep
```

- [ ] **Step 4: Create the carve-out fragment**

Create `packages/notify/changelog.d/+carve-notify.added.md`:

```markdown
Initial release as a standalone package. Carved out from `agent-core` 0.1.0; module renamed from `agent_core.notify` to `agent_core_notify`. No behavior change.
```

(`+`-prefixed; renamed to the real PR number before merge.)

- [ ] **Step 5: Verify towncrier renders**

Run: `uv run --no-sync towncrier build --draft --version 0.1.0 --config packages/notify/towncrier.toml --date 2026-04-28`
Expected: a draft preview that includes the "Added" section with the fragment text.

- [ ] **Step 6: Add a fragment to core too** (notify carve-out is a *change* from core's perspective)

Create `packages/core/changelog.d/+carve-notify.changed.md`:

```markdown
Notify subsystem (desktop notifications via `desktop-notifier`) extracted to the new `agent-core-notify` package. The `agent-core-notify` script and `agent_core.notify` module are no longer part of `agent-core`. Install `agent-core-notify` directly to use desktop notifications.
```

Verify core's draft renders this fragment:

Run: `uv run --no-sync towncrier build --draft --version 0.3.0 --config packages/core/towncrier.toml --date 2026-04-28`
Expected: draft preview shows the "Changed" section with this fragment.

- [ ] **Step 7: Commit**

```bash
git add packages/notify/towncrier.toml packages/notify/CHANGELOG.md \
        packages/notify/changelog.d/ \
        packages/core/changelog.d/
git commit -m "$(cat <<'EOF'
docs(notify): add towncrier config and carve-out fragments

Adds towncrier wiring for the new agent-core-notify package, plus
fragments in both packages capturing the carve-out (added in notify,
changed in core).
EOF
)"
```

---

## Task 7: Final integration smoke

**Files:** none (read-only verification).

- [ ] **Step 1: Re-run all gates**

Run individually so failure of one is visible:
- `uv run --no-sync pytest -q` — expected: 183 passed, 2 skipped
- `uv run --no-sync ruff check . | tail -3` — expected: 9 errors (baseline)
- `uv run --no-sync lint-imports` — expected: 1 kept, 0 broken
- `uv run --no-sync towncrier build --draft --version 0.3.0 --config packages/core/towncrier.toml --date 2026-04-28` — should show core's "Changed" section with the carve-out note
- `uv run --no-sync towncrier build --draft --version 0.1.0 --config packages/notify/towncrier.toml --date 2026-04-28` — should show notify's "Added" section

- [ ] **Step 2: Smoke-test agent-core hooks (still uses core, unchanged)**

Run: `echo '{}' | uv run --no-sync agent-core hooks run SessionStart`
Expected: JSON output with current time.

- [ ] **Step 3: Smoke-test the notify MCP server via the new module path**

Run: `echo '' | timeout 5 uv run --no-sync python -m agent_core_notify.mcp_server; echo "EXIT=$?"`
Expected: starts, validates, exits with `EXIT=0`.

- [ ] **Step 4: Smoke-test the bus CLI (still uses core, unchanged)**

Run: `uv run --no-sync agent-core bus status`
Expected: bus status output, no errors.

- [ ] **Step 5: Verify the old import path is dead**

Run: `uv run --no-sync python -c "import agent_core_notify.mcp_server; print('ok')"`
Expected: `ok`.

Run: `uv run --no-sync python -c "import agent_core.notify" 2>&1 || true`
Expected: `ModuleNotFoundError: No module named 'agent_core.notify'`. (We *want* this to fail — the old path should not exist anymore.)

- [ ] **Step 6: Hunt for stragglers**

Run: `uv run --no-sync python -c "import agent_core.notify"` quickly fails. Then grep for any remaining references:

```bash
git grep -n "agent_core\.notify\|agent_core/notify" -- ':!docs' ':!packages/core/changelog.d' ':!packages/notify/changelog.d'
```

Expected: no matches outside `docs/` and the carve-out changelog fragments. If any code or config file still references the old path, fix it before pushing.

- [ ] **Step 7: Verify Pepper is unaffected**

Run: `git -C E:/workspaces/ai/pepper status -sb 2>&1 | head -3`
Expected: clean working tree (we didn't touch Pepper).

---

## Task 8: Push branch and open PR

**Files:** none.

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/carve-out-notify`
Expected: branch pushed.

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "chore(notify): carve out agent-core-notify into its own workspace member" --body "$(cat <<'EOF'
## Summary
- Migration **Step 2a** of the monorepo workspace design (sub-project A).
- Splits `agent-core-notify` (the desktop-notification MCP server) out of `agent-core` core into its own workspace member at `packages/notify/`.
- Module renamed: `agent_core.notify` → `agent_core_notify` (separate top-level per spec §5.2).
- `desktop-notifier` runtime dep moves from core to notify (it's notify-only).

## Verified
- ✅ pytest: 183 passed, 2 skipped (unchanged)
- ✅ ruff: 9 errors (pre-existing baseline, unchanged)
- ✅ import-linter: 1 kept, 0 broken
- ✅ Both towncrier configs render their fragments
- ✅ `python -m agent_core_notify.mcp_server` starts and exits cleanly via `os._exit(0)`
- ✅ `import agent_core.notify` correctly fails (old path is dead)
- ✅ `agent-core hooks run SessionStart` and `agent-core bus status` still work
- ✅ Pepper untouched

## Out of scope
- `agent-core-email` carve-out (Step 2b) is deferred to a separate plan after sub-project B (lifecycle CLI / plugin discovery) lands.

## Spec
[`docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md`](docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md)

## Plan
[`docs/superpowers/plans/2026-04-28-carve-out-notify.md`](docs/superpowers/plans/2026-04-28-carve-out-notify.md)
EOF
)"
```

- [ ] **Step 3: Rename towncrier fragments to the real PR number**

After the PR is opened (e.g. PR `#N`):

```bash
git mv packages/notify/changelog.d/+carve-notify.added.md \
       packages/notify/changelog.d/N.added.md
git mv packages/core/changelog.d/+carve-notify.changed.md \
       packages/core/changelog.d/N.changed.md
git commit -m "docs(notify): bind towncrier fragments to PR #N"
git push
```

(Replace `N` with the actual PR number.)

- [ ] **Step 4: After merge, update ROADMAP**

Once merged, update `docs/ROADMAP.md` sub-project A row to record Step 2a shipped, with the merge SHA. (Follow-up commit on `main`.)

---

## Definition of done

- [ ] Branch `feat/carve-out-notify` exists with all task commits.
- [ ] All gates green locally (pytest 183/2, ruff 9, import-linter 1 kept).
- [ ] Notify MCP server runs from the new module path.
- [ ] Old `agent_core.notify` path no longer importable.
- [ ] PR opened with verification checklist.
- [ ] Towncrier fragments renamed to the real PR number.
- [ ] PR merged to `main`.
- [ ] ROADMAP updated post-merge.
