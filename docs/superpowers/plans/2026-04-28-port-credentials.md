# Port `agent-core-credentials` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Pepper's credentials subsystem (PyKeePass-backed AES-256 vault) into agent-core as a new workspace member `agent-core-credentials` at `packages/credentials/`. The package ships as both an importable library (`agent_core_credentials`) and a standalone CLI (`agent-core-creds`).

**Architecture:** Read source from Pepper at `E:\workspaces\ai\pepper\src\pepper\credentials\` and tests at `E:\workspaces\ai\pepper\tests\unit\test_credential_*.py`. Recreate them at `packages/credentials/` with module renamed to `agent_core_credentials`, default paths/env-vars de-Pepper'd, error messages and docstrings updated. **Pepper is not touched** — Pepper continues using its in-tree `pepper.credentials` until migration Step 8.

**Tech Stack:** Pydantic-free dataclasses (matching Pepper's pattern), `pykeepass`, `typer`, `rich`, pytest. All workspace tooling (uv, ruff, mypy, import-linter, towncrier) already wired.

**Spec:** [`docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md`](../specs/2026-04-28-monorepo-workspace-design.md)

**Migration step:** This is migration **Step 3** of sub-project A.

**Scope guardrails:**
- Port-by-recreation, not file-move. Pepper repo is untouched.
- The new package ships a standalone `agent-core-creds` CLI script. **No hard import from `agent_core.cli` into `agent_core_credentials`** — that would violate the core-depends-on-nothing rule. Adding a subcommand to `agent-core creds ...` is deferred to sub-project B (entry-points-based CLI subapp discovery).
- No agent-core core code is modified. `packages/core/` is read-only for this PR.

---

## Conventions to follow throughout

- Run on a feature branch off `main`; do not commit directly to `main`.
- Commit messages follow Conventional Commits: `feat(credentials):`, `test(credentials):`, `build(credentials):`, etc.
- After every code-changing commit, add a towncrier fragment in `packages/credentials/changelog.d/`.
- Baselines that must hold throughout (no regressions on existing packages):
  - core+notify pytest: **183 passed, 2 skipped** (the existing tests; we'll add more from credentials).
  - ruff: **9 errors** (pre-existing, out of scope).
  - import-linter: **all contracts pass**.

---

## Pepper source — what we're porting from

| Pepper file | Lines | Purpose |
|---|---|---|
| `pepper/credentials/__init__.py` | 50 | Public API: `get/set/list/delete_credential` functions, `_DEFAULT_PATH` |
| `pepper/credentials/models.py` | 26 | `Credential`, `CredentialSummary` dataclasses |
| `pepper/credentials/store.py` | 107 | `CredentialStore` PyKeePass wrapper |
| `pepper/credentials/cli.py` | 167 | Typer `creds_app`: `init`, `set`, `get`, `list`, `delete` |
| `tests/unit/test_credential_store.py` | 100 | Store CRUD tests |
| `tests/unit/test_credential_cli.py` | 182 | CLI command tests |

**Total:** ~632 lines.

## De-Pepperization rules (apply consistently throughout)

| Pepper convention | New convention |
|---|---|
| Module: `pepper.credentials` | `agent_core_credentials` |
| Default vault: `~/.pepper/credentials.kdbx` | `~/.agent-core/credentials.kdbx` |
| Default `.env`: `~/.pepper/.env` | `~/.agent-core/.env` |
| Master password env var: `PEPPER_VAULT_PASSWORD` | `AGENT_CORE_VAULT_PASSWORD` |
| Vault path env var: (none — hardcoded) | `AGENT_CORE_VAULT_PATH` (overrides default) |
| Error message: "Add it to ~/.pepper/.env" | "Add it to ~/.agent-core/.env or set AGENT_CORE_VAULT_PASSWORD in your shell" |

The vault-path env var is a **new addition** — Pepper hardcoded the path. For agent-core (consumed by multiple agents), the consumer should be able to override it. Default behavior matches Pepper's "hardcoded path" pattern, just under `~/.agent-core/` instead of `~/.pepper/`.

---

## File structure summary

**Created in this plan:**
- `packages/credentials/pyproject.toml`
- `packages/credentials/towncrier.toml`
- `packages/credentials/CHANGELOG.md`
- `packages/credentials/changelog.d/.gitkeep`
- `packages/credentials/changelog.d/+port-credentials.added.md`
- `packages/credentials/src/agent_core_credentials/__init__.py`
- `packages/credentials/src/agent_core_credentials/models.py`
- `packages/credentials/src/agent_core_credentials/store.py`
- `packages/credentials/src/agent_core_credentials/cli.py`
- `packages/credentials/tests/__init__.py`
- `packages/credentials/tests/test_models.py` (new — small)
- `packages/credentials/tests/test_store.py` (ported)
- `packages/credentials/tests/test_cli.py` (ported)

**Modified:**
- `pyproject.toml` (root): add `agent-core-credentials` to `[tool.uv.sources]`, add `packages/credentials/src` to `[tool.ruff] src`
- `.importlinter`: add `agent_core_credentials` to `root_packages`

**Untouched:**
- All `packages/core/**` and `packages/notify/**`
- `agent_core.yaml`, `.mcp.json`, `.claude/settings.json`
- `E:\workspaces\ai\pepper\**`

---

## Task 1: Pre-flight checks and feature branch

**Files:** none (read-only verification + branch creation).

- [ ] **Step 1: Verify clean working tree on `main`**

Run: `git status -sb && git rev-parse --abbrev-ref HEAD`
Expected: clean working tree, on `main`, in sync with `origin/main`.

The user's `.claude/settings.json` may have local uncommitted changes (their hook removal). Those are *expected* to be uncommitted; ignore them but do **not** stage them in any commit.

- [ ] **Step 2: Snapshot baselines**

Run: `uv run --no-sync pytest -q && uv run --no-sync ruff check . | tail -3 && uv run --no-sync lint-imports`
Expected:
- pytest: `183 passed, 2 skipped`
- ruff: `Found 9 errors. [*] 7 fixable...`
- lint-imports: `Contracts: 1 kept, 0 broken.`

If anything differs, stop and surface.

- [ ] **Step 3: Create the feature branch**

Run: `git checkout -b feat/port-credentials`
Expected: `Switched to a new branch 'feat/port-credentials'`.

---

## Task 2: Create `packages/credentials/` scaffolding

Sets up the empty package skeleton: pyproject, towncrier, dirs.

**Files:**
- Create: `packages/credentials/pyproject.toml`
- Create: `packages/credentials/towncrier.toml`
- Create: `packages/credentials/CHANGELOG.md`
- Create: `packages/credentials/changelog.d/.gitkeep`
- Create: `packages/credentials/src/agent_core_credentials/__init__.py` (empty placeholder for now — Task 5 fills it)
- Create: `packages/credentials/tests/__init__.py` (empty)

- [ ] **Step 1: Create `packages/credentials/pyproject.toml`**

```toml
[project]
name = "agent-core-credentials"
version = "0.1.0"
description = "KeePass-backed credential vault for agent-core agents"
requires-python = ">=3.12"
dependencies = [
    "pykeepass>=4.1.1.post1",
    "typer>=0.12",
    "rich>=13.0",
]

[project.scripts]
agent-core-creds = "agent_core_credentials.cli:creds_app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_credentials"]
```

Notes:
- No `agent-core` workspace dep — `agent_core_credentials` doesn't import from `agent_core`.
- The script entry-point `agent-core-creds = "...:creds_app"` works because `creds_app` is a `typer.Typer` instance (callable).

- [ ] **Step 2: Create `packages/credentials/towncrier.toml`**

```toml
[tool.towncrier]
name = "agent-core-credentials"
package = "agent_core_credentials"
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

Paths are relative to `packages/credentials/` (the corrected pattern from PR #3).

- [ ] **Step 3: Create `packages/credentials/CHANGELOG.md`**

```markdown
# Changelog

All notable changes to `agent-core-credentials` are documented in this
file. The format is generated by towncrier from fragments in
`changelog.d/` at release time.

<!-- towncrier release notes start -->
```

- [ ] **Step 4: Create empty src/test directories**

```bash
mkdir -p packages/credentials/changelog.d
mkdir -p packages/credentials/src/agent_core_credentials
mkdir -p packages/credentials/tests
touch packages/credentials/changelog.d/.gitkeep
touch packages/credentials/tests/__init__.py
```

Create `packages/credentials/src/agent_core_credentials/__init__.py` with this placeholder content (Task 5 will replace it with the public API):

```python
"""agent_core_credentials — KeePass-backed credential vault."""
```

- [ ] **Step 5: Commit**

```bash
git add packages/credentials/
git commit -m "$(cat <<'EOF'
build(credentials): scaffold agent-core-credentials package skeleton

Creates the empty packages/credentials/ workspace member with pyproject,
towncrier config, CHANGELOG stub, and src/tests directory layout.
Next tasks port the actual code from Pepper.
EOF
)"
```

(Note: at this point the package is not yet wired into the root pyproject's `[tool.uv.sources]`. That happens in Task 5 once the code is in place — until then, `uv sync` is not run for credentials and the new package isn't part of the venv.)

---

## Task 3: Port `models.py` and `store.py` with their tests

**Files:**
- Create: `packages/credentials/src/agent_core_credentials/models.py`
- Create: `packages/credentials/src/agent_core_credentials/store.py`
- Create: `packages/credentials/tests/test_models.py`
- Create: `packages/credentials/tests/test_store.py`

- [ ] **Step 1: Port `models.py`**

Read `E:\workspaces\ai\pepper\src\pepper\credentials\models.py` and write `packages/credentials/src/agent_core_credentials/models.py` with **identical content** — there's nothing Pepper-specific in this file (it's just two dataclasses). Keep the docstring as-is.

- [ ] **Step 2: Port `store.py` with de-Pepperization**

Read `E:\workspaces\ai\pepper\src\pepper\credentials\store.py` and write `packages/credentials/src/agent_core_credentials/store.py`. Apply these changes:

| In Pepper | Replace with |
|---|---|
| `from pepper.credentials.models import` | `from agent_core_credentials.models import` |
| Env var: `os.environ.get("PEPPER_VAULT_PASSWORD")` | `os.environ.get("AGENT_CORE_VAULT_PASSWORD")` |
| Error message: `"PEPPER_VAULT_PASSWORD environment variable is not set. Add it to ~/.pepper/.env"` | `"AGENT_CORE_VAULT_PASSWORD environment variable is not set. Add it to ~/.agent-core/.env or set it in your shell."` |

Everything else (the `CredentialStore` class structure, methods, behavior) stays identical.

- [ ] **Step 3: Write `test_models.py`**

Pepper had no separate `test_models.py` — its tests are in `test_credential_store.py` and `test_credential_cli.py`. Add a small dedicated test file to verify the dataclasses construct and serialize:

```python
"""Tests for the credential data models."""

from agent_core_credentials.models import Credential, CredentialSummary


class TestCredential:
    def test_construct_with_required_fields(self) -> None:
        cred = Credential(service="x", username="u", password="p")
        assert cred.service == "x"
        assert cred.username == "u"
        assert cred.password == "p"
        assert cred.url == ""
        assert cred.notes == ""

    def test_construct_with_all_fields(self) -> None:
        cred = Credential(
            service="x",
            username="u",
            password="p",
            url="https://x",
            notes="hi",
        )
        assert cred.url == "https://x"
        assert cred.notes == "hi"


class TestCredentialSummary:
    def test_construct_with_required_fields(self) -> None:
        s = CredentialSummary(service="x", username="u")
        assert s.service == "x"
        assert s.username == "u"
        assert s.url == ""

    def test_construct_with_url(self) -> None:
        s = CredentialSummary(service="x", username="u", url="https://x")
        assert s.url == "https://x"
```

- [ ] **Step 4: Port `test_credential_store.py` to `test_store.py`**

Read `E:\workspaces\ai\pepper\tests\unit\test_credential_store.py`. Write `packages/credentials/tests/test_store.py` with these textual replacements applied throughout:

| In Pepper | Replace with |
|---|---|
| `from pepper.credentials.store` | `from agent_core_credentials.store` |
| `from pepper.credentials.models` | `from agent_core_credentials.models` |
| `PEPPER_VAULT_PASSWORD` (in env mocks/setenv) | `AGENT_CORE_VAULT_PASSWORD` |
| `"~/.pepper/.env"` (in any error message assertion) | `"~/.agent-core/.env"` |

Otherwise the test file is identical. If a test asserts the **exact** error string, update the assertion to match the new error message from store.py Step 2.

- [ ] **Step 5: Stage the new files for the upcoming sync**

We can't run `pytest` against these files yet — the package isn't installed in the venv until Task 5 wires it into `[tool.uv.sources]`. That's expected; the tests will run end-to-end after Task 5.

- [ ] **Step 6: Commit**

```bash
git add packages/credentials/src/agent_core_credentials/models.py \
        packages/credentials/src/agent_core_credentials/store.py \
        packages/credentials/tests/test_models.py \
        packages/credentials/tests/test_store.py
git commit -m "$(cat <<'EOF'
feat(credentials): port models and KeePass store from Pepper

Direct port with module renamed (pepper.credentials → agent_core_credentials)
and env var renamed (PEPPER_VAULT_PASSWORD → AGENT_CORE_VAULT_PASSWORD).
Default .env path is ~/.agent-core/.env. CredentialStore behavior is
unchanged. Tests follow the same shape as Pepper's; a small
test_models.py is new (Pepper had no equivalent).
EOF
)"
```

---

## Task 4: Port `__init__.py` (public API) and `cli.py` with tests

**Files:**
- Modify: `packages/credentials/src/agent_core_credentials/__init__.py` (replace placeholder with public API)
- Create: `packages/credentials/src/agent_core_credentials/cli.py`
- Create: `packages/credentials/tests/test_cli.py`

- [ ] **Step 1: Write the public API `__init__.py`**

Replace the placeholder docstring in `packages/credentials/src/agent_core_credentials/__init__.py` with the full public API. Read `E:\workspaces\ai\pepper\src\pepper\credentials\__init__.py` for reference. The new file:

```python
"""agent_core_credentials — KeePass-backed credential vault.

Public API for credential operations. Used by the CLI and available
for direct import elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_core_credentials.models import Credential, CredentialSummary
from agent_core_credentials.store import CredentialStore

__all__ = [
    "Credential",
    "CredentialSummary",
    "default_vault_path",
    "delete_credential",
    "get_credential",
    "list_credentials",
    "set_credential",
]


def default_vault_path() -> Path:
    """Resolve the default vault path.

    Honours the AGENT_CORE_VAULT_PATH env var if set; otherwise falls
    back to ~/.agent-core/credentials.kdbx.
    """
    override = os.environ.get("AGENT_CORE_VAULT_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-core" / "credentials.kdbx"


def get_credential(service: str) -> Credential | None:
    """Retrieve a credential by service name."""
    return CredentialStore(default_vault_path()).get(service)


def set_credential(
    service: str,
    username: str,
    password: str,
    url: str = "",
    notes: str = "",
) -> None:
    """Store or overwrite a credential."""
    CredentialStore(default_vault_path()).set(service, username, password, url, notes)


def list_credentials() -> list[CredentialSummary]:
    """List all stored credentials without passwords."""
    return CredentialStore(default_vault_path()).list()


def delete_credential(service: str) -> bool:
    """Delete a credential. Returns True if found and deleted."""
    return CredentialStore(default_vault_path()).delete(service)
```

The `default_vault_path()` function is a **new addition** vs. Pepper — Pepper had a module-level `_DEFAULT_PATH` constant. Wrapping it in a function makes the env var override possible without import-time evaluation.

- [ ] **Step 2: Port `cli.py` with de-Pepperization**

Read `E:\workspaces\ai\pepper\src\pepper\credentials\cli.py` and write `packages/credentials/src/agent_core_credentials/cli.py`. Apply:

| In Pepper | Replace with |
|---|---|
| `"""Pepper creds CLI — manage stored credentials."""` | `"""agent-core creds CLI — manage stored credentials."""` |
| `from pepper.credentials.store import CredentialStore` | `from agent_core_credentials.store import CredentialStore` |
| `_vault_path = Path.home() / ".pepper" / "credentials.kdbx"` | `from agent_core_credentials import default_vault_path` (top of file)<br>`_vault_path = default_vault_path()` |
| `_env_path = Path.home() / ".pepper" / ".env"` | `_env_path = Path.home() / ".agent-core" / ".env"` |
| `if os.environ.get("PEPPER_VAULT_PASSWORD"):` | `if os.environ.get("AGENT_CORE_VAULT_PASSWORD"):` |
| `f.write(f"\nPEPPER_VAULT_PASSWORD={password}\n")` | `f.write(f"\nAGENT_CORE_VAULT_PASSWORD={password}\n")` |

Otherwise the file is identical.

- [ ] **Step 3: Port `test_credential_cli.py` to `test_cli.py`**

Read `E:\workspaces\ai\pepper\tests\unit\test_credential_cli.py`. Apply the same textual replacements as Task 3 Step 4 plus:

| In Pepper | Replace with |
|---|---|
| `from pepper.credentials.cli` | `from agent_core_credentials.cli` |
| `pepper.credentials.cli._vault_path` (any monkeypatch target) | `agent_core_credentials.cli._vault_path` |
| `pepper.credentials.cli._env_path` (any monkeypatch target) | `agent_core_credentials.cli._env_path` |

If a test invokes the CLI via `typer.testing.CliRunner` against `creds_app`, that import works as-is (just under the new module path).

- [ ] **Step 4: Commit**

```bash
git add packages/credentials/src/agent_core_credentials/__init__.py \
        packages/credentials/src/agent_core_credentials/cli.py \
        packages/credentials/tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(credentials): port public API and CLI from Pepper

Adds:
- agent_core_credentials.__init__ public API with default_vault_path()
  function honouring AGENT_CORE_VAULT_PATH override.
- agent_core_credentials.cli (typer creds_app: init/set/get/list/delete)
  exposed as the agent-core-creds entry point.
- Test suite ported from Pepper, adjusted for new module/env names.
EOF
)"
```

---

## Task 5: Wire the new package into the workspace and verify

**Files:**
- Modify: `pyproject.toml` (root)
- Modify: `.importlinter`

- [ ] **Step 1: Update root `pyproject.toml`**

In root `pyproject.toml`, edit `[tool.uv.sources]` to add the new package:

From:
```toml
[tool.uv.sources]
agent-core = { workspace = true }
agent-core-notify = { workspace = true }
```

To:
```toml
[tool.uv.sources]
agent-core = { workspace = true }
agent-core-notify = { workspace = true }
agent-core-credentials = { workspace = true }
```

Edit `[tool.ruff]` to add the new src path:

From:
```toml
[tool.ruff]
line-length = 100
src = ["packages/core/src", "packages/notify/src"]
```

To:
```toml
[tool.ruff]
line-length = 100
src = ["packages/core/src", "packages/notify/src", "packages/credentials/src"]
```

- [ ] **Step 2: Update `.importlinter`**

Add `agent_core_credentials` to `root_packages`:

From:
```ini
[importlinter]
root_packages =
    agent_core
    agent_core_notify
```

To:
```ini
[importlinter]
root_packages =
    agent_core
    agent_core_notify
    agent_core_credentials
```

The existing `bus-core-self-contained` contract block stays as-is. (Optional refinement: also add `agent_core_credentials` to that contract's `forbidden_modules` list to enforce the same isolation. Skip for now unless trivial — it can ride in a later sweep.)

- [ ] **Step 3: Run `uv sync`**

Run: `uv sync`
Expected: uv resolves the workspace, installs the new `agent-core-credentials` editable from `packages/credentials`. New transitive dep: `pykeepass` (and its own deps).

If `uv sync` errors, check the new pyproject's syntax.

- [ ] **Step 4: Verify entry point**

Run: `ls .venv/Scripts/agent-core-creds* 2>&1`
Expected: `agent-core-creds.exe` present.

- [ ] **Step 5: Run all tests**

Run: `uv run --no-sync pytest -q`
Expected: more tests than before (the credentials suite added). New baseline reported in your output.

The credentials tests must all pass. If any fail, investigate. Common failure modes:
- Test imports a path that's still `pepper.*` — fix it.
- A test reads an env var that's still `PEPPER_*` — fix it.
- A test asserts an exact error string and the new message differs — adjust the assertion.

- [ ] **Step 6: Run lint-imports**

Run: `uv run --no-sync lint-imports`
Expected: `Contracts: 1 kept, 0 broken.`

- [ ] **Step 7: Run ruff**

Run: `uv run --no-sync ruff check . | tail -3`
Expected: still 9 errors (the pre-existing baseline). Specifically, no NEW errors introduced by credentials. If the count went up, ruff is flagging something in the ported code — fix it before continuing.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .importlinter uv.lock
git commit -m "$(cat <<'EOF'
build(credentials): wire agent-core-credentials into the workspace

Adds agent-core-credentials to [tool.uv.sources] and [tool.ruff].src,
registers agent_core_credentials with import-linter. uv.lock reflects
the new editable install and pykeepass dep.
EOF
)"
```

- [ ] **Step 9: Add the carve-in fragment**

Create `packages/credentials/changelog.d/+port-credentials.added.md`:

```markdown
Initial release. Port of Pepper's PyKeePass-backed credential vault into agent-core. Provides `agent_core_credentials` library API and `agent-core-creds` CLI. AES-256 encrypted vault at `~/.agent-core/credentials.kdbx` (override via `AGENT_CORE_VAULT_PATH`); master password from `AGENT_CORE_VAULT_PASSWORD` env var.
```

(`+`-prefixed; renamed to PR number before merge.)

Verify it renders:

Run: `uv run --no-sync towncrier build --draft --version 0.1.0 --config packages/credentials/towncrier.toml --date 2026-04-28`
Expected: draft preview shows "Added" with the fragment text.

Commit:
```bash
git add packages/credentials/changelog.d/+port-credentials.added.md
git commit -m "docs(credentials): add carve-in fragment for initial release"
```

---

## Task 6: Final integration smoke

**Files:** none (read-only verification).

- [ ] **Step 1: Re-run all gates**

Run individually:
- `uv run --no-sync pytest -q` — all tests green (count higher than the 183 baseline by the credentials suite)
- `uv run --no-sync ruff check . | tail -3` — still 9 errors
- `uv run --no-sync lint-imports` — 1 kept, 0 broken

- [ ] **Step 2: Smoke-test the standalone `agent-core-creds` CLI**

Run: `uv run --no-sync agent-core-creds --help`
Expected: typer help output listing `init`, `set`, `get`, `list`, `delete` subcommands with the description "Manage stored credentials."

- [ ] **Step 3: Smoke-test the public-API import path**

Run:
```bash
uv run --no-sync python -c "from agent_core_credentials import default_vault_path, get_credential, set_credential, list_credentials, delete_credential; print('ok'); print(default_vault_path())"
```
Expected: `ok` followed by the resolved default path (`~\.agent-core\credentials.kdbx`).

- [ ] **Step 4: Smoke-test that other CLIs still work**

- `echo '{}' | uv run --no-sync agent-core hooks run SessionStart` — works.
- `uv run --no-sync agent-core bus status` — works.
- `echo '' | timeout 5 uv run --no-sync python -m agent_core_notify.mcp_server; echo "EXIT=$?"` — exits 0.

- [ ] **Step 5: Verify Pepper untouched**

Run: `git -C E:/workspaces/ai/pepper diff --stat 2>&1 | tail -3`
Expected: any uncommitted changes in Pepper are pre-existing user state, not anything caused by this work. (We never wrote to Pepper.)

- [ ] **Step 6: Hunt for stragglers in this PR**

```bash
git grep -n "pepper\.credentials\|PEPPER_VAULT_PASSWORD\|~/.pepper" -- packages/credentials/
```
Expected: **no matches** in the new package. Any matches mean a textual replacement was missed — fix and recommit.

---

## Task 7: Push branch and open PR

**Files:** none.

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/port-credentials`

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(credentials): port agent-core-credentials from Pepper" --body "$(cat <<'EOF'
## Summary
- Migration **Step 3** of the monorepo workspace design (sub-project A).
- Ports Pepper's PyKeePass credential vault into agent-core as a new
  workspace member at `packages/credentials/`. Module: `agent_core_credentials`.
- Ships as both an importable library and a standalone `agent-core-creds`
  CLI script.
- **Pepper is untouched.** Pepper continues using its in-tree
  `pepper.credentials` until migration Step 8.

## What's different from Pepper
- Module renamed: `pepper.credentials` → `agent_core_credentials`.
- Default vault path: `~/.agent-core/credentials.kdbx` (override via
  `AGENT_CORE_VAULT_PATH`).
- Master password env var: `AGENT_CORE_VAULT_PASSWORD`.
- New `default_vault_path()` function so the env override works without
  import-time evaluation.

## Verified
- ✅ All tests pass (existing 183 + new credentials suite).
- ✅ ruff: 9 errors (baseline, no new ones).
- ✅ import-linter: 1 kept, 0 broken.
- ✅ `agent-core-creds --help` works.
- ✅ Public API imports work.
- ✅ `agent-core hooks`, `agent-core bus`, and the notify MCP server still work.
- ✅ No `pepper.*` strings remain in `packages/credentials/`.

## Out of scope
- Adding `agent-core creds ...` as a subcommand of the umbrella `agent-core`
  CLI is deferred to sub-project B (entry-points-based CLI subapp discovery).
  Standalone `agent-core-creds` works today.

## Spec
[`docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md`](docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md)

## Plan
[`docs/superpowers/plans/2026-04-28-port-credentials.md`](docs/superpowers/plans/2026-04-28-port-credentials.md)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Rename the towncrier fragment to the real PR number**

After the PR is opened (e.g. PR `#N`):

```bash
git mv packages/credentials/changelog.d/+port-credentials.added.md \
       packages/credentials/changelog.d/N.added.md
git commit -m "docs(credentials): bind towncrier fragment to PR #N"
git push
```

- [ ] **Step 4: After merge, update ROADMAP**

Once merged, update `docs/ROADMAP.md` sub-project A row to record Step 3 shipped, with the merge SHA. Follow-up commit on `main`.

---

## Definition of done

- [ ] Branch `feat/port-credentials` exists with all task commits.
- [ ] All tests pass (existing 183 + new credentials suite).
- [ ] ruff baseline unchanged (9 errors).
- [ ] import-linter passes.
- [ ] `agent-core-creds --help` works.
- [ ] No `pepper.*` strings in `packages/credentials/`.
- [ ] PR opened with verification checklist.
- [ ] Towncrier fragment renamed to the real PR number.
- [ ] PR merged to `main`.
- [ ] ROADMAP updated post-merge.
