# Spec: mypy --strict for agent-core-credentials (issue #496)

## Goal

Enable `mypy --strict` enforcement for `packages/credentials/src` by adding the package to the root `[tool.mypy] files` list, wiring a per-module strict-flags override (following the pattern established for discord in issue #444 and hatchery in issue #491), adding an `ignore_missing_imports` override for `pykeepass` (which ships no type stubs), removing the now-redundant inline `# type: ignore[import-untyped]` comments, and fixing all annotation gaps in the credentials source tree until `uv run mypy` exits 0. Part of the Track B B5 multi-package strict-mypy effort (#405, sub-ticket #496).

## Acceptance criteria

- `pyproject.toml` `[tool.mypy].files` includes `"packages/credentials/src"`.
- `pyproject.toml` has `[[tool.mypy.overrides]]` with `module = ["pykeepass", "pykeepass.*"]` and `ignore_missing_imports = true`.
- `pyproject.toml` has `[[tool.mypy.overrides]]` with `module = ["agent_core_credentials.*"]` and all individual strict flags (`disallow_any_generics`, `disallow_subclassing_any`, `disallow_untyped_calls`, `disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_untyped_decorators`, `warn_return_any`, `no_implicit_reexport`, `strict_equality`, `extra_checks`), following the existing discord/hatchery pattern.
- The `# type: ignore[import-untyped]` comment on the pykeepass import in `store.py` is removed.
- The `# type: ignore[import-untyped]` comment on the pykeepass import in `cli.py` is removed.
- `secrets.py::_open_store` has an explicit `-> CredentialStore` return type annotation.
- `uv run mypy` exits 0 with no errors.
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern fits. This is a typing-discipline closure — "make the right thing easy": once `packages/credentials/src` is in `[tool.mypy] files` with strict flags, type regressions are impossible to introduce silently.

**Why individual flags rather than `strict = true`.** The comment in `pyproject.toml` (above the discord override block, lines 114–118) explains the leakage problem verbatim: `strict = true` in a per-module override leaks flags like `disallow_any_generics` to the other packages in `files`. The individual-flag approach used for discord and hatchery is the correct scoping mechanism; this spec follows it.

**Why pykeepass needs `ignore_missing_imports`.** `pykeepass` ships no `py.typed` marker and no type stubs on PyPI (confirmed: no `py.typed` in `.venv/lib/python3.12/site-packages/pykeepass/`). This is analogous to `discord.py` needing its own override. The existing inline `# type: ignore[import-untyped]` comments in `store.py` (line 7) and `cli.py` (line 10) suppress the same error, but once the `ignore_missing_imports` override is in place those inline ignores become unused suppression comments. With `warn_unused_ignores = true` at the root level, unused ignores are hard errors — they must be removed alongside adding the override. The net effect is identical runtime behaviour (pykeepass symbols remain `Any`); the suppression mechanism is simply lifted from inline comments to the config-level override.

**Why keyring, cryptography, typer, and rich need no override.** `keyring>=24.0` (the minimum pinned) ships `py.typed` (confirmed: `.venv/lib/python3.12/site-packages/keyring/py.typed` exists). `cryptography`, `typer`, and `rich` all ship `py.typed`. No missing-stubs noise to suppress.

**Known annotation gap: `secrets.py::_open_store`.** This is the only function in the credentials source tree that lacks a return type annotation:

```python
def _open_store():   # <-- disallow_untyped_defs fires here
    from agent_core_credentials import default_vault_path
    from agent_core_credentials.store import CredentialStore
    return CredentialStore(default_vault_path())
```

Fix: add `-> CredentialStore` to the signature. The function is a DI seam (tests monkeypatch it) so its signature must not change beyond adding the return annotation.

**Store and CLI files are otherwise annotation-complete.** `_open(self) -> PyKeePass` and `_open_or_create(self) -> PyKeePass` annotate `PyKeePass` as the return type; since pykeepass is untyped, `PyKeePass` resolves to `Any` — which means the annotated return is effectively `Any`. `warn_return_any` only fires when returning `Any` from a function typed to return a non-`Any`; since the annotated return type is itself `Any`, no warning is raised. All other source files (`models.py`, `master_password.py`, `__init__.py`) are annotation-complete.

**`__init__.py` re-export discipline.** `no_implicit_reexport` uses the `__all__` list to determine explicit exports. All symbols in `__all__` are imported and re-exported explicitly; `CredentialStore` is imported for internal use but not in `__all__`, so it is correctly non-exported.

## Sub-requests (topologically sorted)

1. **Update `pyproject.toml` mypy configuration.** In the `[tool.mypy]` table, add `"packages/credentials/src"` as a fourth entry in `files` (after `"packages/agent-core-discord/src"`). Then add two new `[[tool.mypy.overrides]]` sections after the existing `agent_core_discord.*` block:

   ```toml
   # pykeepass publishes no type stubs — silence the missing-import noise so the
   # strict override below can focus on our own code.
   [[tool.mypy.overrides]]
   module = ["pykeepass", "pykeepass.*"]
   ignore_missing_imports = true

   # agent-core-credentials is held to full --strict (issue #496). Individual flags
   # are used (not the `strict = true` umbrella) for the same scoping reason as
   # the discord override above.
   [[tool.mypy.overrides]]
   module = ["agent_core_credentials.*"]
   disallow_any_generics = true
   disallow_subclassing_any = true
   disallow_untyped_calls = true
   disallow_untyped_defs = true
   disallow_incomplete_defs = true
   disallow_untyped_decorators = true
   warn_return_any = true
   no_implicit_reexport = true
   strict_equality = true
   extra_checks = true
   ```

2. **Remove `# type: ignore[import-untyped]` from `store.py`.** In `packages/credentials/src/agent_core_credentials/store.py` line 7, change:

   ```python
   from pykeepass import PyKeePass, create_database  # type: ignore[import-untyped]
   ```

   to:

   ```python
   from pykeepass import PyKeePass, create_database
   ```

3. **Remove `# type: ignore[import-untyped]` from `cli.py`.** In `packages/credentials/src/agent_core_credentials/cli.py` line 10, change:

   ```python
   from pykeepass import create_database  # type: ignore[import-untyped]
   ```

   to:

   ```python
   from pykeepass import create_database
   ```

4. **Add return type to `_open_store` in `secrets.py`.** In `packages/credentials/src/agent_core_credentials/secrets.py`, change:

   ```python
   def _open_store():
       """Open the credential store against the default vault path.

       Isolated into its own function so tests can monkeypatch it without
       touching the real vault.
       """
       from agent_core_credentials import default_vault_path
       from agent_core_credentials.store import CredentialStore

       return CredentialStore(default_vault_path())
   ```

   to:

   ```python
   def _open_store() -> "CredentialStore":
       """Open the credential store against the default vault path.

       Isolated into its own function so tests can monkeypatch it without
       touching the real vault.
       """
       from agent_core_credentials import default_vault_path
       from agent_core_credentials.store import CredentialStore

       return CredentialStore(default_vault_path())
   ```

   Use the quoted forward reference `"CredentialStore"` because `CredentialStore` is imported inside the function body (not at module scope); the string form avoids a `NameError` at annotation evaluation time if `from __future__ import annotations` were ever removed. Alternatively, if the file already has `from __future__ import annotations` at the top, the bare name `CredentialStore` is equally correct (annotations are lazy strings in that case).

   **Check first**: `secrets.py` line 1 begins with `from __future__ import annotations` (confirmed by reading the file). With that import in place, `-> CredentialStore` (unquoted) is fine — mypy evaluates it as a string lazily, and the name `CredentialStore` is defined by the time any caller evaluates the annotation. Use the unquoted form `-> CredentialStore` for consistency with the rest of the codebase.

5. **Run mypy and fix any remaining issues.** After sub-requests 1–4:

   ```bash
   uv run mypy
   ```

   Expected output: `Success: no issues found`. If mypy surfaces additional issues beyond those listed above (e.g., in any of the Typer CLI command decorators or in `master_password.py`'s exception-handler scope), fix them in the same commit. Common patterns to watch for:
   - A bare `Callable` or bare `dict`/`list` in a type annotation — add type parameters (`Callable[..., Any]`, `dict[str, Any]`, etc.).
   - A `warn_return_any` hit caused by an untyped call returning `Any` that flows into a typed assignment.

6. **Commit and verify the full gate.**

   ```bash
   git add pyproject.toml \
     packages/credentials/src/agent_core_credentials/secrets.py \
     packages/credentials/src/agent_core_credentials/store.py \
     packages/credentials/src/agent_core_credentials/cli.py
   # (add any additional files patched in step 5)
   git commit -m "feat: enable mypy --strict for agent-core-credentials"
   just check
   ```

   Expected: green (lint, typecheck, contracts, test, patch-cov all pass).

## File-level changes

| File | Change |
|---|---|
| `pyproject.toml` | Add `"packages/credentials/src"` to `[tool.mypy].files`; add `[[tool.mypy.overrides]]` for `pykeepass.*` (`ignore_missing_imports = true`) and for `agent_core_credentials.*` (individual strict flags) |
| `packages/credentials/src/agent_core_credentials/secrets.py` | Add `-> CredentialStore` return type annotation to `_open_store` |
| `packages/credentials/src/agent_core_credentials/store.py` | Remove `# type: ignore[import-untyped]` from pykeepass import (now covered by config-level override) |
| `packages/credentials/src/agent_core_credentials/cli.py` | Remove `# type: ignore[import-untyped]` from pykeepass import (now covered by config-level override) |
| Other `packages/credentials/src/` files (if mypy surfaces gaps after sub-requests 1–4) | Minor annotation fixes as discovered by running `uv run mypy` |

No new files are created. No public interfaces (method signatures, class names, credential models) change.

## Alternatives considered

1. **Keep the inline `# type: ignore[import-untyped]` comments instead of adding a pykeepass override.** The inline comments already suppress pykeepass errors and were sufficient before this ticket. However, adding the config-level `ignore_missing_imports` override while retaining the inline comments causes `warn_unused_ignores = true` (already set at the root level) to fire — mypy treats the inline suppression as unnecessary and exits non-zero. The two mechanisms are mutually exclusive once both are present. Ruled out: would break the mypy gate.

2. **Use `strict = true` in the per-module override instead of individual flags.** The pyproject.toml comment (lines 114–118) already explains why: the umbrella `strict = true` in a `[[tool.mypy.overrides]]` block leaks flags like `disallow_any_generics` to the other packages in `files` (core, channel, discord). The individual-flag approach is the established repo pattern. Ruled out: would widen the strictness scope unintentionally.

3. **Leave `credentials` out of mypy scope for now (no-op).** The B5 track-B epic explicitly targets all 10 untyped packages. Credentials is size S and already well-annotated; the gap is a single missing return type plus the pykeepass import handling. Deferring buys nothing and leaves a gap in the "11 of 12 packages strict" milestone. Ruled out.

## Open questions

1. **Are there annotation gaps beyond `_open_store` that only surface when mypy runs?** The source files are reviewed line-by-line above and appear annotation-complete. The Worker should run `uv run mypy` after sub-requests 1–4 and fix any additional issues discovered. If no additional issues surface, the expected output is `Success: no issues found` with zero extra changes.

2. **Does the hatchery strict spec (#491) also land before this ticket?** This ticket has no dependency on #491 (credentials has no `audit.py` and shares no module with hatchery). If hatchery has already been added to `[tool.mypy].files`, the `files` list will have more than 3 entries — the Worker should add `"packages/credentials/src"` after whatever is currently last, not hardcode its position.

## Out of scope

- Enabling mypy `--strict` for any other package in the workspace — each is its own sub-ticket under B5 (#405).
- Adding `packages/credentials/tests` to mypy — test files are not in the `src/` tree and are not part of the production package.
- Changing the `CredentialStore` or `Credential` public API, adding methods, or restructuring modules — this is a typing-discipline ticket only; no behavioral or structural changes.
- Adding stubs or type annotations for `pykeepass` itself — out of scope for this repo; the `ignore_missing_imports` override is the correct long-term approach.
