# Secret material handling — Design (Theme D, Cluster α)

**Theme:** agent_core#267 (Theme D — Security hardening) · epic #262
**Date:** 2026-07-15
**Status:** approved design, pre-implementation
**Cluster:** Dα of Theme D. Sibling clusters (later brainstorms): Dβ bus transport auth, Dγ inbound integrity & abuse resistance, Dδ untrusted-input boundary.

## Problem

agent-core's secret material is protected only by "can you read this file / this process's environment?":

- **Vault master password persisted plaintext next to the vault.** `credentials/cli.py:61-62` appends `AGENT_CORE_VAULT_PASSWORD=<plaintext>` to `~/.agent-core/.env` via a bare `open(_env_path, "a")`. The KeePass vault (`~/.agent-core/credentials.kdbx`) is unlocked by a password sitting in a plaintext file in the same directory. (Cβ #325 added owner-only perms, so it is no longer *world*-readable — but it is still plaintext, co-located with what it unlocks.) `[P0]`
- **Secrets printed to stdout.** `creds get --json` (`cli.py:88+`) emits `{"password": ...}` to stdout, which lands in terminal scrollback, shell history, redirected files, and CI logs. `[P0]`
- **Secrets inherited by every subprocess.** The vault password + webhook secret live in `os.environ`; a child process inherits the parent's whole environment by default, so every `git`, every `subprocess.Popen`, and every foreman worker receives secrets it has no need for (leakable via `/proc/<pid>/environ`, a crash dump, or a stray `print(os.environ)`). `[P0]`
- **`.env` loader injects arbitrary keys.** `cli.py:28-35` reads `~/.agent-core/.env` and `os.environ.setdefault(key, value)` for **every** key found — no filtering. A tampered/clobbered `.env` can set `PATH` / `PYTHONPATH` / `LD_PRELOAD` and hijack which binaries or modules the daemon loads (code execution, not just secret exposure). `[P2]`

## Design decisions (from the brainstorm, approved)

1. **Vault master password → OS keyring, with an owner-only encrypted-file fallback.** Store the master password in the OS secret store via the `keyring` library (macOS Keychain / Windows Credential Manager-DPAPI / Linux Secret Service). Where no keystore is available (headless Linux, CI, containers), fall back to an owner-only file. The password stops being ambient plaintext on the real (Windows/macOS) deployments; the fallback keeps headless runs working.

2. **`creds get` becomes metadata-only.** The CLI never emits a secret value. `creds get X` reports existence + length + last-updated ("exists, 24 chars, updated 3d ago"). Humans who need an actual value open the KeePass GUI (`credentials.kdbx` is a real `.kdbx`); agents/code use the in-process vault API. `set` / `list` / `delete` are unchanged (they do not emit secrets). No `--reveal` / `--clip` escape hatch — deleting the emission path is safer than gating it.

3. **Secrets out of `os.environ` entirely (least ambient exposure).** No secret is loaded into the process environment. Consumers fetch each secret from the vault API / keyring at its point of use, so a spawned child inherits nothing secret. The set is bounded (audited on main): the vault password (→ keyring, decision 1), the inbound webhook secret, and a few tokens (`DISCORD_*`, `AGENTMAIL_API_KEY`, `X_TOKEN`). This is not a sprawling refactor — a fixed list of read sites.

4. **`.env` loader allowlists keys.** The loader injects only a known allowlist of `AGENT_CORE_*` / expected config keys and silently ignores everything else. A rogue `.env` cannot smuggle in `PATH` / `PYTHONPATH` / `LD_*`. Allowlist (not blocklist) — safe by default; a blocklist always misses a variable. Once the vault password lives in the keyring, `.env` should carry almost nothing anyway.

5. **Migration is first-class — no being loses access.** Security hardening must not cut Pepper or Wren off from their own credentials. Every change ships a migration that keeps the old access path working until the new path is verified, then removes the old path:
   - Vault password: read the existing plaintext from `.env` → store in keyring → scrub the line from `.env`.
   - Being tokens (Discord, API keys) currently reached via env: ensure they exist in the vault → switch the read site to the vault API → remove from env. Coordinated with Pepper for her credentials.
   - The Pepper cutover scheduled task referencing `creds get apex --json` → re-point to the vault API (or hand to Pepper-side if it lives there).
   - The migration inventories entry **names** (never values) so nothing is orphaned.

## Architecture

### 1. Keyring-backed master password (`credentials/`)

- A small secret-store abstraction with two backends: `keyring` (primary) and an owner-only encrypted file (fallback). Backend selection: try `keyring`; on `keyring.errors.NoKeyringError` / unavailable backend, fall back to the file, logging which backend is in use at debug level.
- The vault open path fetches the master password from the store (never from `os.environ`). If the store has no password yet, prompt interactively (existing CLI flow) and persist to the store.
- Service/username namespacing in keyring: service `agent-core`, username keyed per instance so prod/source/being vaults don't collide.

### 2. Metadata-only `creds get` (`credentials/cli.py`)

- `get_credential` stops printing the value. It resolves the entry and prints structural metadata only: name, value length, last-modified. Exit non-zero if the entry is absent (so scripts can still test "is this set?").
- Remove the `--json {"password": ...}` value emission. If a machine-readable existence check is wanted, `--json` may return `{"name": ..., "exists": true, "length": N}` — never the secret.

### 3. Vault-API secret access, no ambient env (`bus/`, `inbound/`, being adapters)

- A single typed accessor (e.g. `secrets.get(name)`) that reads from the vault/keyring on demand and returns the value to the immediate caller only.
- Each current env read site (webhook secret in the funnel handler; Discord/AgentMail/X tokens in their adapters) switches to the accessor. The daemon no longer sets these into `os.environ` at startup.
- Subprocess spawns pass an explicit minimal `env=` (no secrets) — belt-and-suspenders, since nothing secret is in `os.environ` to inherit anyway.

### 4. `.env` allowlist loader (`credentials/cli.py:28-35`)

- Replace `os.environ.setdefault(key, value)`-for-every-key with a check against an `_ENV_ALLOWLIST` frozenset of accepted keys. Non-allowlisted keys are skipped (optionally logged at debug). The allowlist holds only non-secret config keys now that the vault password moves to the keyring.

## Ticket decomposition (dependency-ordered)

- **Dα-1 — Keyring master-password store + owner-only fallback + `.env`→keyring migration.** *(no dep)* Introduces the secret-store abstraction; migrates the existing plaintext vault password out of `.env`.
- **Dα-2 — `creds get` → metadata-only + migrate the `creds get --json` scheduled task.** *(no dep)* Removes the stdout secret-emission P0.
- **Dα-3 — Secrets-out-of-env: vault-API accessor + switch webhook/token read sites + scrubbed subprocess env.** *(blocked_by Dα-1 — needs the keyring store in place)* Removes the ambient-env P0; coordinated with Pepper for being tokens.
- **Dα-4 — `.env` loader allowlist.** *(no dep)* Closes the PATH/PYTHONPATH injection vector.

## Testing / validation

- Keyring: unit-test the store abstraction with a fake keyring backend; test the fallback path by simulating `NoKeyringError`; test the `.env`→keyring migration is idempotent and scrubs the source line.
- `creds get`: assert no test ever sees a secret value in captured stdout; assert metadata (length/exists) is correct; assert exit codes for present/absent.
- Out-of-env: assert the named secrets are absent from `os.environ` after daemon start; assert a spawned child's `env` contains no secret keys; assert each read site resolves via the accessor.
- `.env` allowlist: assert a `.env` containing `PATH=/evil` does not modify `os.environ["PATH"]`; assert allowlisted keys still load.
- Cross-platform: `keyring` backends differ per OS — gate backend-specific tests by platform; the fallback path must be exercised on Linux CI (no Secret Service).

## Strengths to preserve

Constant-time HMAC with `sha256=` guard, server-stamped sender identity, structural-only audit summaries, hard-refused non-loopback bind, `docs_url=None`. Nothing in Dα weakens these.
