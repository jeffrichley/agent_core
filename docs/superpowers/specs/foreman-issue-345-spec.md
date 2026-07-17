# Spec: keyring master-password store + owner-only file fallback + .env→keyring migration (issue #345)

## Goal

Eliminate the `[P0]` plaintext vault master password in `~/.agent-core/.env` (see `packages/credentials/src/agent_core_credentials/cli.py:61-62` and `store.py:27-35`) by introducing a `master_password` module that stores the password in the OS keyring on macOS/Windows and in an owner-only Fernet-encrypted file pair on headless Linux/CI. A one-time idempotent migration moves the existing plaintext `AGENT_CORE_VAULT_PASSWORD` from `.env` into the new store on first vault open. See issue #345 and the design authority at `docs/superpowers/specs/2026-07-15-security-secret-handling-design.md` (decisions 1 + 5).

## Acceptance criteria

- `packages/credentials/src/agent_core_credentials/master_password.py` exists and exports:
  - `KEYRING_SERVICE: str = "agent-core"` — the keyring service name.
  - `_username_for(vault_path: Path) -> str` — returns `str(vault_path.resolve())` as the per-vault keyring username.
  - `_hash_for(vault_path: Path) -> str` — returns the first 12 hex chars of `sha256(str(vault_path.resolve()).encode())` for use in fallback filenames.
  - `class EncryptedFileStore` — fallback backend. `__init__(vault_path: Path)` sets `self._key_path = vault_path.parent / f".vault-key-{_hash_for(vault_path)}"` and `self._data_path = vault_path.parent / f".vault-pass-{_hash_for(vault_path)}"`. `get() -> str | None`: returns `None` if `_data_path` does not exist; else loads or generates the 32-byte Fernet key from `_key_path` (chmod 0o600 on write), decrypts and returns the UTF-8 password. `set(password: str) -> None`: generates or loads the Fernet key, encrypts the password, writes ciphertext to `_data_path` (chmod 0o600). Key file and data file are written with `0o600` on creation.
  - `class _KeyringStore` — keyring backend. `__init__(vault_path: Path)` sets `self._username = _username_for(vault_path)`. `get() -> str | None`: returns `keyring.get_password(KEYRING_SERVICE, self._username)`. `set(password: str) -> None`: calls `keyring.set_password(KEYRING_SERVICE, self._username, password)`.
  - `_get_backend(vault_path: Path)` — factory. Calls `keyring.get_password("__agent-core-probe__", "__probe__")` inside a try/except; on `keyring.errors.NoKeyringError` (or any other `Exception`), logs at DEBUG and returns `EncryptedFileStore(vault_path)`; on success (returns `None` or a string) returns `_KeyringStore(vault_path)`.
  - `_read_env_file_password(env_path: Path) -> str | None` — reads `.env` line by line; returns the value of the first non-comment `AGENT_CORE_VAULT_PASSWORD=...` line, or `None` if absent or empty.
  - `_scrub_env_file_password(env_path: Path) -> None` — rewrites `.env` omitting all non-comment lines containing `AGENT_CORE_VAULT_PASSWORD=`. No-op if the file does not exist. Idempotent.
  - `get_master_password(vault_path: Path, env_path: Path = _ENV_PATH) -> str | None` — calls `_get_backend(vault_path).get()`; if that returns a truthy value, returns it. Otherwise calls `_read_env_file_password(env_path)`, and if found: calls `_get_backend(vault_path).set(env_password)`, calls `_scrub_env_file_password(env_path)`, returns the password. Returns `None` if neither store nor `.env` has a password.
  - `set_master_password(vault_path: Path, password: str) -> None` — calls `_get_backend(vault_path).set(password)`.
  - Module-level `_ENV_PATH = Path.home() / ".agent-core" / ".env"`.
- `packages/credentials/src/agent_core_credentials/store.py` — `CredentialStore.__init__` gains a keyword-only `_master_password: str | None = None` DI argument stored as `self._password_override`. `_get_password()` returns `self._password_override` if non-None; otherwise calls `get_master_password(self.vault_path)` (imported at module top from `agent_core_credentials.master_password`); if the result is falsy, raises `ValueError("No master password found. Run 'agent-core-creds init' to initialize the vault, or ensure the OS keyring is accessible.")`. The old `os.environ.get("AGENT_CORE_VAULT_PASSWORD")` logic is removed entirely.
- `packages/credentials/src/agent_core_credentials/cli.py` — `init_vault()` replaces the `open(_env_path, "a")` block (lines 61-63) with a call to `set_master_password(_vault_path, password)`. No password is written to `.env`. The import `from agent_core_credentials.master_password import set_master_password` is added at the top of the file.
- `packages/credentials/pyproject.toml` — `dependencies` list includes `"keyring>=24.0"` and `"cryptography>=41.0"`.
- `packages/credentials/src/agent_core_credentials/__init__.py` — re-exports `get_master_password` and `set_master_password` from `agent_core_credentials.master_password`; both names appear in `__all__`.
- New test file `packages/credentials/tests/test_master_password.py` covers:
  - `test_keyring_store_set_and_get` — `_KeyringStore.set` + `get` round-trips via a fake in-memory keyring (monkeypatched `keyring.get_password` / `keyring.set_password`).
  - `test_encrypted_file_store_set_and_get` — `EncryptedFileStore.set("secret")` then `get()` round-trips in `tmp_path`.
  - `test_encrypted_file_store_get_returns_none_when_empty` — `EncryptedFileStore.get()` returns `None` when no data file exists.
  - `test_encrypted_file_store_files_are_owner_only` (POSIX only, `@pytest.mark.skipif(sys.platform == "win32", ...)`) — after `set()`, both key and data file have mode `0o600`.
  - `test_get_backend_uses_keyring_when_available` — when `keyring.get_password` is patched to return `None` (no error), `_get_backend()` returns a `_KeyringStore`.
  - `test_get_backend_falls_back_on_nokeyringerror` — when `keyring.get_password` is patched to raise `keyring.errors.NoKeyringError`, `_get_backend()` returns an `EncryptedFileStore`.
  - `test_get_master_password_reads_from_backend` — when backend `get()` returns a value, `get_master_password()` returns it without touching `.env`.
  - `test_get_master_password_migrates_from_env` — when backend `get()` returns `None` but a `.env` file contains `AGENT_CORE_VAULT_PASSWORD=s3cr3t`, `get_master_password()` returns `"s3cr3t"`, calls `_get_backend().set()` with that value, and scrubs the line from `.env`.
  - `test_get_master_password_migration_is_idempotent` — calling `get_master_password()` twice when the password is already in the backend does not call `_scrub_env_file_password` again (the `.env` value was removed on first call, so subsequent calls find it only in the backend).
  - `test_get_master_password_returns_none_when_no_source` — returns `None` when backend is empty and `.env` has no password line.
  - `test_scrub_env_file_password_removes_only_password_line` — a `.env` with other keys (`FOO=bar`) plus `AGENT_CORE_VAULT_PASSWORD=x` is scrubbed; `FOO=bar` remains; `AGENT_CORE_VAULT_PASSWORD` is gone.
- `packages/credentials/tests/test_store.py` — `vault` fixture replaces `monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")` with `CredentialStore(vault_path, _master_password="testpass")`. `test_missing_password_env_raises` is updated: assert `ValueError` with the new message `"No master password found"` (not the old `"AGENT_CORE_VAULT_PASSWORD environment variable is not set"`) when `CredentialStore` is constructed without `_master_password` and `_get_backend()` returns a store whose `get()` returns `None` (monkeypatch `get_master_password` to return `None`).
- `packages/credentials/tests/test_cli.py` — for every test that sets `monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")`: replace with `monkeypatch.setattr("agent_core_credentials.master_password.get_master_password", lambda vault_path: "testpass")`. Test `test_creds_init_creates_vault` is updated to: (a) patch `set_master_password` to record calls, (b) assert the password was passed to `set_master_password`, (c) assert `"AGENT_CORE_VAULT_PASSWORD="` is NOT present in the env file.
- `just check` passes (ruff + full suite, coverage ≥ 85%).

## Approach

**Pattern**: Strategy (GoF). `_KeyringStore` and `EncryptedFileStore` implement the same two-method interface (`get() -> str | None`, `set(password: str) -> None`). `_get_backend()` selects the strategy at runtime. `get_master_password()` / `set_master_password()` are the entry points that delegate to whichever strategy is live. This isolates the OS-specific branching in one factory function.

**Backend selection**: `_get_backend()` probes the keyring with a non-secret key (`"__agent-core-probe__"`, `"__probe__"`). If `keyring.get_password` returns `None` (no entry stored — normal) the keyring is functional and `_KeyringStore` is returned. If it raises `keyring.errors.NoKeyringError` (or any other exception), `EncryptedFileStore` is returned. The probe is a harmless read — it returns `None` if no such entry exists, which is always the case. Log at `DEBUG` which path was taken; log at `DEBUG` with `exc_info=True` for unexpected exceptions.

**Encrypted file fallback**: Uses `cryptography.fernet.Fernet`, already available transitively via `pykeepass`. The key is generated with `Fernet.generate_key()` on first write and stored in a sibling file (`vault_path.parent / f".vault-key-{_hash_for(vault_path)}"`). Both files are `chmod 0o600` immediately after creation (before writing content on the key file, after encrypting for the data file). On `get()`, if the key file is missing, `_load_key()` generates and persists a fresh key — this is safe because a missing key file means the data file (if somehow present) can't be decrypted anyway. The hash suffix prevents collisions when multiple vaults share the same parent directory.

**One-time migration**: `get_master_password()` checks the backend first. If empty, it reads `.env` with `_read_env_file_password()` (parses the file directly — not `os.environ` — so `_load_env()` calling `os.environ.setdefault` before this has no effect on migration). On finding a password in `.env`, it writes to the backend then scrubs the `.env` line. Idempotent: once migrated, the keyring/file has the password and `.env` doesn't — subsequent calls go straight through the backend.

**DI for `CredentialStore`**: the `_master_password` constructor keyword bypasses all keyring/file logic for tests. This mirrors the established `runner=subprocess.run` DI pattern used throughout the codebase (e.g., `daemon_probe.py`).

**Error message change**: `CredentialStore._get_password()` now raises `ValueError("No master password found. Run 'agent-core-creds init' to initialize the vault, or ensure the OS keyring is accessible.")` instead of mentioning the env var. The existing test `test_missing_password_env_raises` must be updated to match.

**`cli.py` import change**: `set_master_password` is imported at the top of `cli.py` (module level, not inside the function). `_load_env()` continues to load other env vars for non-password keys; removing `AGENT_CORE_VAULT_PASSWORD` from `.env` means it simply won't appear on future calls. `_load_env()` itself is not changed in this ticket (Dα-4 handles the allowlist).

## Sub-requests (topologically sorted)

1. **Add `keyring>=24.0` and `cryptography>=41.0`** to `dependencies` in `packages/credentials/pyproject.toml`.

2. **Create `packages/credentials/src/agent_core_credentials/master_password.py`** with the complete implementation: `KEYRING_SERVICE`, `_ENV_PATH`, `_username_for`, `_hash_for`, `EncryptedFileStore`, `_KeyringStore`, `_get_backend`, `_read_env_file_password`, `_scrub_env_file_password`, `get_master_password`, `set_master_password`.

3. **Update `packages/credentials/src/agent_core_credentials/store.py`** — add `from agent_core_credentials.master_password import get_master_password` at the top; add `_master_password: str | None = None` keyword arg to `CredentialStore.__init__` (store as `self._password_override`); replace `_get_password()` body with the override-then-`get_master_password` logic; remove `import os`.

4. **Update `packages/credentials/src/agent_core_credentials/cli.py`** — add `from agent_core_credentials.master_password import set_master_password` at the top; replace lines 61-63 (`with open(_env_path, "a") ...`) with `set_master_password(_vault_path, password)`.

5. **Update `packages/credentials/src/agent_core_credentials/__init__.py`** — add `from agent_core_credentials.master_password import get_master_password, set_master_password` and add `"get_master_password"` and `"set_master_password"` to `__all__`.

6. **Create `packages/credentials/tests/test_master_password.py`** with all tests listed in Acceptance criteria (12 tests total).

7. **Update `packages/credentials/tests/test_store.py`** — change `vault` fixture to use `CredentialStore(vault_path, _master_password="testpass")`; update `test_missing_password_env_raises` to monkeypatch `get_master_password` and assert the new error message.

8. **Update `packages/credentials/tests/test_cli.py`** — for each test that previously called `monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", "testpass")`, add instead `monkeypatch.setattr("agent_core_credentials.master_password.get_master_password", lambda vault_path: "testpass")`; also add `monkeypatch.setattr("agent_core_credentials.master_password.set_master_password", lambda vault_path, password: None)` for the `init` tests so no real keyring access occurs. Update `test_creds_init_creates_vault` to assert password NOT written to `.env` and that the `set_master_password` stub was called.

9. **Run `just check`** and confirm green.

## File-level changes

| File | Change |
|------|--------|
| `packages/credentials/pyproject.toml` | **Modify** — add `"keyring>=24.0"` and `"cryptography>=41.0"` to `dependencies` |
| `packages/credentials/src/agent_core_credentials/master_password.py` | **New** — Strategy backends, factory, migration helpers, `get_master_password`, `set_master_password` |
| `packages/credentials/src/agent_core_credentials/store.py` | **Modify** — add `_master_password` DI arg; replace `_get_password()` to use `get_master_password()`; remove `os.environ` reference; remove `import os` |
| `packages/credentials/src/agent_core_credentials/cli.py` | **Modify** — add `set_master_password` import; replace `.env` write in `init_vault()` with `set_master_password()` call |
| `packages/credentials/src/agent_core_credentials/__init__.py` | **Modify** — add `get_master_password` and `set_master_password` to imports and `__all__` |
| `packages/credentials/tests/test_master_password.py` | **New** — 12 unit tests covering all backends, factory, migration, and scrubbing |
| `packages/credentials/tests/test_store.py` | **Modify** — `vault` fixture uses `_master_password=` DI; `test_missing_password_env_raises` patches `get_master_password` and checks new error message |
| `packages/credentials/tests/test_cli.py` | **Modify** — replace `monkeypatch.setenv("AGENT_CORE_VAULT_PASSWORD", ...)` with `setattr` on `master_password.get_master_password`; stub `set_master_password`; update `test_creds_init_creates_vault` assertions |

## Alternatives considered

1. **Keep env var, just restrict who sets it (Dα-1 lite)**: Make `_load_env()` the only path that sets `AGENT_CORE_VAULT_PASSWORD` in `os.environ`, with no keyring. Ruled out: the env var is still plaintext in process memory and inherited by every child process — the design explicitly identifies child-process inheritance as a separate P0 (covered by Dα-3). Keyring is the right mechanism for Dα-1.

2. **Use a third-party encrypted keyring backend like `keyrings.cryptfile`** instead of a custom `EncryptedFileStore`. Ruled out: adds an extra optional dep; `keyrings.cryptfile` itself requires a password to unlock (creating a circular dependency — what protects the key file's password?). Rolling our own Fernet backend with a generated key is simpler, self-contained, and already has `cryptography` available transitively.

3. **Store the master password inline in the `.kdbx` header** (custom KeePass field). Ruled out: pykeepass does not expose a stable API for header modifications; this would couple us to KeePass internals and make upgrades brittle. The problem is "where do we store the password that unlocks the vault," and using the vault itself to store it is circular.

4. **Prompt interactively inside `CredentialStore._get_password()`** when no password is found (instead of raising `ValueError`). Ruled out: `CredentialStore` is in `store.py` with no dependency on `typer` or a TTY — mixing I/O into a data-layer class violates SRP. Prompting is the CLI's responsibility (`init_vault()` already does it). A `ValueError` on missing password gives the CLI layer full control over the user interaction.

## Open questions

*None.* All file paths, function names, and class signatures are verified against the actual repo. The `keyring` and `cryptography` APIs used are stable public APIs. Backend selection via probe call is the standard pattern per keyring's own documentation.

## Out of scope

- Dα-2 (`creds get` metadata-only) — separate ticket, no dependency on Dα-1.
- Dα-3 (secrets out of env — vault-API accessor, subprocess env scrubbing) — blocked by Dα-1; the Worker on Dα-3 must wait for this ticket to merge.
- Dα-4 (`.env` allowlist loader) — separate ticket. `_load_env()` in `cli.py:27-35` is not changed here; it continues to load all keys including non-secret config.
- Removing `AGENT_CORE_VAULT_PASSWORD` from `os.environ` after `_load_env()` sets it — this is addressed in Dα-3's subprocess env scrubbing.
- Rotating or re-keying the Fernet key in `EncryptedFileStore` — not needed; the key is machine-local and long-lived.
- Windows file permission enforcement for `EncryptedFileStore` — `chmod 0o600` is a no-op on Windows (NTFS ACLs differ from POSIX modes); the keyring backend (Windows Credential Manager via DPAPI) is used on Windows, making the fallback Linux/CI-specific in practice.
- Adding a `creds migrate-password` subcommand — the migration is automatic on first vault open; no explicit CLI command is needed.
