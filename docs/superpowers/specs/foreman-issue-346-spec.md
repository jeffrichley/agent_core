# Spec: `creds get` metadata-only — remove stdout secret emission (issue #346)

## Goal

Eliminate the `[P0]` stdout secret-emission defect in `creds get` by making the command output metadata only (name, value length, last-modified timestamp) and never the credential value. This is Dα-2 in the Theme D security cluster. See issue #346 and design authority at `docs/superpowers/specs/2026-07-15-security-secret-handling-design.md` (decision 2).

## Acceptance criteria

- `creds get X` (text mode) prints three lines: `Service`, `Length` (in chars), and `Modified` (UTC timestamp or `(unknown)` if pykeepass has no mtime). It no longer prints `Username`, `URL`, `Notes`, or the password value.
- `creds get X --json` outputs `{"name": "X", "exists": true, "length": N}` — never the password, username, url, or notes.
- The `--json` flag's help text is updated from `"Output as JSON with password"` to `"Output as JSON (metadata only, no secret)"`.
- `creds get X` exits non-zero (code 1) when the service is absent (unchanged).
- `creds set`, `creds list`, and `creds delete` are unchanged.
- `Credential.mtime: datetime | None = None` field exists in `packages/credentials/src/agent_core_credentials/models.py`; `models.py` imports `from datetime import datetime`.
- `CredentialStore.get()` populates `mtime=entry.mtime` on the returned `Credential`.
- `packages/credentials/tests/test_cli.py`:
  - `test_creds_set_and_get` no longer asserts `"jeff@test.com" in result.output`; asserts `"Length:" in result.output` and `"jeff@test.com" not in result.output`.
  - `test_creds_get_json` is rewritten: asserts `"secret123" not in result.output`; parses JSON and asserts `data["exists"] is True`, `data["name"] == "apex"`, `data["length"] == 9`, and `"password" not in data`.
  - New test `test_creds_get_text_shows_length`: asserts text mode shows `Length:` and the correct char count, password absent, username absent.
  - New test `test_creds_get_json_schema`: asserts exact key set `{"name", "exists", "length"}`, no password in output.
- `docs/cutover/pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md`: `apex_weekly_slots` step 1 is updated to replace `pepper creds get apex --json` with an in-process vault API call and a note that the scheduler.db entry on Pepper's machine needs updating.
- `just check` passes (ruff + full suite, coverage ≥ 85 %).

## Approach

No GoF pattern fits. The guiding principle is information hiding / least-privilege output: the credential value stays in-process (the `Credential` object is constructed normally, `cred.password` provides the length), but the emission layer (`cli.py`) never writes it to stdout.

**`Credential` model — add `mtime`:** `packages/credentials/src/agent_core_credentials/models.py` gains `from datetime import datetime` and a `mtime: datetime | None = None` trailing field on the `Credential` dataclass. Since it has a default, it is appended after the existing optional fields (`url`, `notes`) and the dataclass ordering rule is satisfied. Existing construction sites that omit `mtime` are unaffected. `CredentialSummary` is not changed (list output is already secret-free).

**`CredentialStore.get()` — populate mtime:** pykeepass `entry.mtime` is a `datetime.datetime` in UTC sourced from `LastModificationTime` in the kdbx XML (verified in `pykeepass/baseelement.py`). Passing `mtime=entry.mtime` when constructing `Credential` makes the last-modification time available to the CLI without adding any new method to the store.

**`cli.py:get_credential()` — remove emission paths:** Replace the entire body of the text branch and the JSON branch:

- Text branch: print `Service`, `Length` (via `len(cred.password)`), and `Modified` (via `cred.mtime.strftime('%Y-%m-%d %H:%M UTC')` when `mtime` is not `None`, otherwise `(unknown)`).
- JSON branch: `json.dumps({"name": cred.service, "exists": True, "length": len(cred.password)})`. Key is `name` not `service`, matching the design doc's specified shape.

No `--reveal` or `--clip` escape hatch is added — the design doc explicitly rejects this ("deleting the emission path is safer than gating it").

**Test updates:** `test_creds_set_and_get` still asserts the password is absent, but must stop asserting the username is present (since we no longer print it). `test_creds_get_json` is inverted: the password must be absent and the JSON shape must match `{"name", "exists", "length"}`. Two new tests cover the text-mode metadata fields and the JSON schema explicitly — these serve as regression guards against any future attempt to re-add a password field.

**Migration doc:** The `apex_weekly_slots` prompt in the scheduled-tasks inventory is documentation of Pepper's old scheduler state (captured from `~/.pepper/scheduler.db`). The prompt text instructs `pepper creds get apex --json` for credentials. After this change that command returns no password. The inventory doc is updated to replace that step with an in-process vault API call pattern and a note that the live scheduler.db on Pepper's machine needs updating separately.

## Sub-requests (topologically sorted)

1. **Add `from datetime import datetime` to `packages/credentials/src/agent_core_credentials/models.py`** and add `mtime: datetime | None = None` as the last field of the `Credential` dataclass (after `notes: str = ""`).

2. **Update `CredentialStore.get()` in `packages/credentials/src/agent_core_credentials/store.py`** to pass `mtime=entry.mtime` when constructing the returned `Credential`.

3. **Rewrite `get_credential()` in `packages/credentials/src/agent_core_credentials/cli.py`** (lines 87–121):
   - Update `--json` option help: `"Output as JSON (metadata only, no secret)"`.
   - Replace text branch (lines 115–121) with:
     ```python
     rprint(f"[bold]Service:[/bold]   {cred.service}")
     rprint(f"[bold]Length:[/bold]    {len(cred.password)} chars")
     if cred.mtime is not None:
         rprint(f"[bold]Modified:[/bold]  {cred.mtime.strftime('%Y-%m-%d %H:%M UTC')}")
     else:
         rprint("[bold]Modified:[/bold]  (unknown)")
     ```
   - Replace JSON branch (lines 103–114) with:
     ```python
     print(json.dumps({"name": cred.service, "exists": True, "length": len(cred.password)}))
     ```

4. **Update `packages/credentials/tests/test_cli.py`**:
   - Add `import json` at the top.
   - **`test_creds_set_and_get`**: replace the `assert "jeff@test.com" in result.output` line with `assert "jeff@test.com" not in result.output` and add `assert "Length:" in result.output`. Keep `assert "secret123" not in result.output`.
   - **`test_creds_get_json`**: replace `assert "secret123" in result.output` with:
     ```python
     assert "secret123" not in result.output
     data = json.loads(result.output)
     assert data == {"name": "apex", "exists": True, "length": 9}
     assert "password" not in data
     ```
   - **New `test_creds_get_text_shows_length`**: set a credential with password `"secret123"`, invoke `["get", "apex"]`, assert exit code 0, `"Length:" in result.output`, `"9" in result.output`, `"secret123" not in result.output`, `"jeff@test.com" not in result.output`.
   - **New `test_creds_get_json_schema`**: set a credential with password `"secret123"`, invoke `["get", "apex", "--json"]`, parse JSON, assert `set(data.keys()) == {"name", "exists", "length"}`, `data["length"] == 9`, `"secret123" not in result.output`.

5. **Update `docs/cutover/pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md`**: In the `apex_weekly_slots` section, replace step 1 (`Get Apex credentials: \`pepper creds get apex --json\``) with:
   ```
   1. Get Apex credentials via the vault API (creds get no longer emits the secret after Dα-2):
      ```python
      from agent_core_credentials import get_credential
      cred = get_credential("apex")
      # Use cred.username and cred.password in-process; never echo them to any output
      ```
      **Note:** the `apex_weekly_slots` entry in Pepper's `~/.pepper/scheduler.db` also needs updating to match this pattern.
   ```

6. **Run `just check`** (ruff + full test suite with coverage). The `fast_vault_kdf` autouse fixture in `conftest.py` keeps per-test KDF cost at ~0.6s, so new tests adding vault opens will not be slow by nature.

## File-level changes

| File | Change |
|------|--------|
| `packages/credentials/src/agent_core_credentials/models.py` | **Modify** — add `from datetime import datetime` import; add `mtime: datetime \| None = None` field at end of `Credential` dataclass |
| `packages/credentials/src/agent_core_credentials/store.py` | **Modify** — pass `mtime=entry.mtime` in `CredentialStore.get()` when constructing `Credential` |
| `packages/credentials/src/agent_core_credentials/cli.py` | **Modify** — rewrite `get_credential()` text and JSON branches to emit metadata only; update `--json` help text |
| `packages/credentials/tests/test_cli.py` | **Modify** — add `import json`; update `test_creds_set_and_get` and `test_creds_get_json`; add `test_creds_get_text_shows_length` and `test_creds_get_json_schema` |
| `docs/cutover/pepper-flip-2026-05-06-config/scheduled-tasks-inventory.md` | **Modify** — replace `pepper creds get apex --json` step in `apex_weekly_slots` prompt with vault API call pattern and migration note |

## Alternatives considered

1. **Add a `--reveal` flag that prints the password only when explicitly requested.** Keeps the old behaviour accessible. Ruled out: the design doc explicitly prohibits it — "No `--reveal` / `--clip` escape hatch — deleting the emission path is safer than gating it." A flag is a gate; a missing code path is a wall.

2. **Add a new `creds meta X` sub-command and leave `creds get X` unchanged.** Zero regression risk, fully backwards-compatible. Ruled out: the P0 is that `creds get --json` can be called today and emits the password; leaving `creds get --json` in place leaves the vulnerability. The new semantics must own the existing command name so all callers are forced to adapt.

3. **Return `{"service": ..., "exists": true, "length": N}` (keeping `service` key name) rather than `{"name": ..., ...}`.** Consistent with the existing JSON schema used by `creds list`. Ruled out: the design doc specifies `{"name":...,"exists":true,"length":N}` exactly, and `name` is clearer for a metadata-only shape where `service` implies the full record. YAGNI — there is no existing consumer of `creds get --json` in production code (only the Pepper scheduled task, which is being migrated).

## Open questions

*None.* pykeepass `entry.mtime` is confirmed to exist in `baseelement.py` (`_get_times_property('LastModificationTime')`). The design doc's exact JSON shape is specified. The test patterns and conftest fixture behaviour are verified from the existing suite.

## Out of scope

- `creds set`, `creds list`, `creds delete` — no changes.
- Dα-1 (keyring master password) — separate ticket, no dependency.
- Dα-3 (secrets out of env / vault-API accessor) — separate ticket; `CredentialStore` still reads `AGENT_CORE_VAULT_PASSWORD` from env for the master password in this ticket.
- Dα-4 (`.env` allowlist loader) — separate ticket.
- Adding `mtime` to `CredentialSummary` or to the output of `creds list` — not requested.
- Updating `Credential` construction sites other than `CredentialStore.get()` (e.g., tests that construct `Credential(service=..., ...)` directly) — `mtime` defaults to `None` so they are unaffected.
- Updating Pepper's live `~/.pepper/scheduler.db` — operator action, outside this repo.
