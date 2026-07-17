# Spec: secrets out of env — vault-API accessor + scrubbed subprocess env (issue #347)

## Goal

Eliminate the ambient-process-environment P0 by introducing a typed `secrets.get(name)` accessor that reads from the KeePass vault on demand, and switching each known env-read site (inbound webhook secret, Discord bot tokens, AgentMail API key) to call it. Belt-and-suspenders: the daemon subprocess spawn passes an explicit filtered `env=` so nothing secret can be inherited even if an operator's shell still carries the old vars. Migration safety is built in — the accessor falls back to `os.environ[name]` when the vault entry is absent, so no being loses access before the vault is populated. See issue #347 and the design authority at `docs/superpowers/specs/2026-07-15-security-secret-handling-design.md` (decisions 3 + 5).

**Dependency:** this ticket is `blocked_by Dα-1`, which replaces `os.environ["AGENT_CORE_VAULT_PASSWORD"]` with a keyring-backed master password so the vault can be opened without a secret already in the environment. The Worker must not begin until Dα-1 is merged.

## Acceptance criteria

- `packages/credentials/src/agent_core_credentials/secrets.py` exists and exports:
  - `class SecretNotFoundError(Exception)` — raised when neither vault nor env has the secret.
  - `def get(name: str) -> str` — resolution order: vault entry with `service == name` → `os.environ.get(name)` → raises `SecretNotFoundError`. Vault failures (no vault file, bad password, pykeepass error) are caught, logged at `DEBUG`, and fall through to env.
- `packages/credentials/src/agent_core_credentials/__init__.py` re-exports `SecretNotFoundError` and `get` from the `secrets` sub-module via `__all__`.
- `InboundEndpoint.__init__` resolves the webhook secret by calling `secrets.get(webhook_secret_env)` instead of `os.environ.get(webhook_secret_env)`. The existing `RuntimeError` is still raised (with the env var name in the message) when neither vault nor env has the secret.
- `DiscordEndpoint.start()` resolves the bot token by calling `secrets.get(self.token_env)` instead of `os.environ.get(self.token_env)`. The existing `RuntimeError` is still raised (with `self.token_env` in the message) when neither vault nor env has the token.
- `agent_core.email.client.get_client()` resolves `AGENTMAIL_API_KEY` by calling `secrets.get("AGENTMAIL_API_KEY")` instead of `os.environ.get("AGENTMAIL_API_KEY")`. The existing failure message/exit is triggered on `SecretNotFoundError`.
- `packages/core/src/agent_core/daemon/cli.py` passes `env=_daemon_safe_env()` to the `subprocess.Popen` call at line 159. `_daemon_safe_env()` returns `{k: v for k, v in os.environ.items() if k not in _DAEMON_ENV_BLOCKLIST}` where `_DAEMON_ENV_BLOCKLIST` is a module-level `frozenset` containing `{"AGENT_CORE_VAULT_PASSWORD", "FOREMAN_GITHUB_WEBHOOK_SECRET", "AGENTMAIL_API_KEY"}`.
- `packages/core/src/agent_core/daemon/windows_service.py` applies the same `_daemon_safe_env()` to its `subprocess.Popen` call at line 100 (imports and references the same frozenset from `daemon/cli.py`).
- `agent-core-credentials` is listed in the `dependencies` array of each package that now imports from it: `packages/agent-core-inbound/pyproject.toml`, `packages/agent-core-discord/pyproject.toml`, `packages/core/pyproject.toml`.
- New test file `packages/credentials/tests/test_secrets.py` covers all four resolution paths (vault hit, env fallback, vault-error fallback, not-found error).
- All existing tests in `packages/agent-core-inbound/tests/`, `packages/agent-core-discord/tests/`, and `packages/credentials/tests/` continue to pass without modification — the env fallback ensures tests that use `monkeypatch.setenv` keep working.
- `just check` passes (ruff + full suite, coverage ≥ 85 %).

## Approach

No GoF pattern fits cleanly here. This is straightforward SRP decomposition: one function owns secret resolution, one frozenset owns the subprocess blocklist, three call sites are swapped.

**Resolution order in `secrets.get()`:** vault-first, env-fallback, then `SecretNotFoundError`. The vault-first path opens `CredentialStore(default_vault_path())` (which in Dα-1 uses the keyring for master password, never `os.environ`), calls `.get(name)`, and returns `cred.password`. Any `Exception` from vault access is caught and logged at `DEBUG`; the fallback continues to `os.environ`. If `os.environ.get(name)` also returns `None` or `""`, `SecretNotFoundError` is raised. This fallback guarantees migration safety — operators can populate the vault one secret at a time, and reads continue to succeed from the env during the window when a vault entry doesn't yet exist.

**Why `secrets.py` lives in `agent-core-credentials`:** the vault access (pykeepass) is already confined to that package. Pulling `secrets.get()` up into `agent-core` core would add `pykeepass` as a transitive dep of the core bus daemon, which is architecturally wrong. Instead, each consuming package (`agent-core-inbound`, `agent-core-discord`, `agent-core` for email) adds `agent-core-credentials` as an explicit dependency.

**Existing tests continue to pass:** the env fallback ensures any test that calls `monkeypatch.setenv("SOME_KEY", "val")` will still have the value found at the env-fallback step after a vault miss. Tests that assert `RuntimeError` on missing secrets will still fire because both vault (no vault file in CI) and env (deleted by `monkeypatch.delenv`) miss, causing `SecretNotFoundError`, which the endpoint converts to `RuntimeError`. No test changes are required for existing tests.

**Subprocess env scrubbing:** `_daemon_safe_env()` in `daemon/cli.py` strips a bounded frozenset of known secret-key names. It is `os.environ` minus `_DAEMON_ENV_BLOCKLIST`. The blocklist covers `AGENT_CORE_VAULT_PASSWORD` (belt-and-suspenders against Dα-1 migration lag), `FOREMAN_GITHUB_WEBHOOK_SECRET`, and `AGENTMAIL_API_KEY`. Discord token env var names are per-being and vary by instance, so they cannot be statically enumerated — after Dα-3 switches the Discord read site to vault, these vars should not be in the operator's env at all. Windows service Popen imports the same frozenset from `daemon.cli` so there is a single source of truth.

## Sub-requests (topologically sorted)

1. **Create `packages/credentials/src/agent_core_credentials/secrets.py`** — `SecretNotFoundError` exception and `get(name: str) -> str` with vault-first, env-fallback resolution.

2. **Update `packages/credentials/src/agent_core_credentials/__init__.py`** — add `"secrets", "SecretNotFoundError"` to `__all__`; add `from agent_core_credentials.secrets import SecretNotFoundError, get as get_secret` import.

3. **Create `packages/credentials/tests/test_secrets.py`** — four unit tests covering (a) vault-hit returns password, (b) vault-miss falls through to env, (c) vault-exception falls through to env, (d) neither source → `SecretNotFoundError`.

4. **Add `agent-core-credentials` to `packages/agent-core-inbound/pyproject.toml`** `dependencies` list.

5. **Update `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py`** — replace `os.environ.get(webhook_secret_env)` (line 79) with a `secrets.get()` call and catch `SecretNotFoundError` to re-raise as the existing `RuntimeError`.

6. **Add `agent-core-credentials` to `packages/agent-core-discord/pyproject.toml`** `dependencies` list.

7. **Update `packages/agent-core-discord/src/agent_core_discord/endpoint.py`** — replace `os.environ.get(self.token_env)` (line 499) with a `secrets.get()` call; catch `SecretNotFoundError` and re-raise as the existing `RuntimeError`. Remove the `import os` usage from this path (keep the `os` import if used elsewhere in the file).

8. **Add `agent-core-credentials` to `packages/core/pyproject.toml`** `dependencies` list.

9. **Update `packages/core/src/agent_core/email/client.py`** — replace `os.environ.get("AGENTMAIL_API_KEY")` (line 26) with `secrets.get("AGENTMAIL_API_KEY")`; catch `SecretNotFoundError` in place of the `if not api_key:` check and print the same error message.

10. **Update `packages/core/src/agent_core/daemon/cli.py`** — add `_DAEMON_ENV_BLOCKLIST: frozenset[str]` constant and `_daemon_safe_env() -> dict[str, str]` helper at module level; pass `env=_daemon_safe_env()` to the `subprocess.Popen` call at line 159.

11. **Update `packages/core/src/agent_core/daemon/windows_service.py`** — import `_daemon_safe_env` from `agent_core.daemon.cli`; pass `env=_daemon_safe_env()` to the `subprocess.Popen` call at line 100.

12. **Run `just check`** and confirm green.

## File-level changes

| File | Change |
|------|--------|
| `packages/credentials/src/agent_core_credentials/secrets.py` | **New** — `SecretNotFoundError` + `get(name)` accessor (vault-first, env-fallback) |
| `packages/credentials/src/agent_core_credentials/__init__.py` | **Modify** — re-export `SecretNotFoundError` and `get_secret` from secrets sub-module |
| `packages/credentials/tests/test_secrets.py` | **New** — four unit tests for all resolution paths |
| `packages/agent-core-inbound/pyproject.toml` | **Modify** — add `"agent-core-credentials"` to `dependencies` |
| `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py` | **Modify** — replace `os.environ.get(webhook_secret_env)` with `secrets.get()` call at line 79; handle `SecretNotFoundError` |
| `packages/agent-core-discord/pyproject.toml` | **Modify** — add `"agent-core-credentials"` to `dependencies` |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify** — replace `os.environ.get(self.token_env)` with `secrets.get()` call at line 499; handle `SecretNotFoundError` |
| `packages/core/pyproject.toml` | **Modify** — add `"agent-core-credentials"` to `dependencies` |
| `packages/core/src/agent_core/email/client.py` | **Modify** — replace `os.environ.get("AGENTMAIL_API_KEY")` with `secrets.get()` at line 26; catch `SecretNotFoundError` |
| `packages/core/src/agent_core/daemon/cli.py` | **Modify** — add `_DAEMON_ENV_BLOCKLIST` frozenset + `_daemon_safe_env()` helper; pass `env=_daemon_safe_env()` to `Popen` at line 159 |
| `packages/core/src/agent_core/daemon/windows_service.py` | **Modify** — import `_daemon_safe_env` from `agent_core.daemon.cli`; pass `env=_daemon_safe_env()` to `Popen` at line 100 |

## Alternatives considered

1. **Place `secrets.get()` in `agent-core` (core) instead of `agent-core-credentials`.** Avoids adding `agent-core-credentials` as a dep to `agent-core-inbound` and `agent-core-discord`. Ruled out: `agent-core` core would then depend on `pykeepass` (via `agent-core-credentials`), adding a C-extension KeePass library to the core bus daemon. SRP says the vault implementation belongs in the credentials package.

2. **Use dependency injection — pass a `secret_resolver: Callable[[str], str]` into `InboundEndpoint` and `DiscordEndpoint`.** Avoids adding `agent-core-credentials` as a dep to each endpoint package. Ruled out: the accessor is simple (one function, vault-first + env-fallback); DI would add constructor churn and YAML config complexity for no benefit at this scale. The endpoint packages will have `agent-core-credentials` installed in any real deployment; adding it to `pyproject.toml` is correct declaration hygiene, not overengineering.

3. **Skip the env fallback entirely — require vault entries at startup.** Purist security posture: no env path means no ambient exposure risk. Ruled out: the issue explicitly requires "No being loses access — old env path works until the accessor is verified." A broken Pepper or Wren during the migration window is a P0 outage, not a P0 security fix.

4. **Extend the subprocess blocklist to pattern-match `DISCORD_*_TOKEN`.** Catches any future Discord-token-in-env accidentally. Ruled out: Python `frozenset` doesn't support glob patterns; a prefix/suffix scan would require a function instead of a constant; and after Dα-3 switches the Discord read sites to vault, these vars should never appear in the operator's env. YAGNI.

## Open questions

*None.* The design authority is unambiguous for Dα-3's scope; file paths and function names are verified in the repo.

## Out of scope

- Dα-1 (keyring master password) — dependency, not scope here.
- Dα-2 (`creds get` metadata-only) — separate ticket, no dependency.
- Dα-4 (`.env` allowlist loader) — separate ticket; the `_load_env()` function in `credentials/cli.py:27-35` is not changed here.
- Adding `DISCORD_*` token names to `_DAEMON_ENV_BLOCKLIST` — names are per-instance and unbounded; after Dα-3, these vars should not be in the env at all (no need to enumerate them in the blocklist).
- Any `X_TOKEN` production wiring — `X_TOKEN` appears only in test fixtures as a placeholder `token_env`; there is no production `X_TOKEN` env read site to switch.
- Migration operational steps (populating vault entries for Pepper/Wren tokens, coordinating with Pepper-side cutover) — out of scope for this code ticket; those are operator actions.
- Changing the YAML config schema for `webhook_secret_env` or `token_env` parameters — the env var names continue to double as vault service names; no config key renames in this ticket.
