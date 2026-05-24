# Phase 3.5 — Three-instance daemon (`prod` / `source` / `test`) (Design)

> **Status:** Drafted 2026-05-23. Pending spec-review approval.
>
> **Issue:** to be filed by Pepper after spec sign-off; this doc precedes the GitHub issue.
>
> **Scope:** Extend Phase 3's two-instance model (`prod`, `dev`) to a three-instance model (`prod`, `source`, `test`). Hard-cutover rename of `dev` to `source` so the name reflects what the instance actually is (the daemon running from unbuilt repo source). Add `test` as a sandboxed instance that installs from release wheels using the SAME `release.py` code path as `prod` — enabling end-to-end deploy-path validation before prod refreshes. No bus envelope schema changes; one new error class (the rename parse error) total.

## Problem

Phase 3 (PR #108) shipped a two-instance daemon model: `prod` (installed from release artifacts to `~/.agent-core/`, port 8789) and `dev` (runs editably from the workspace `.venv`, home at `~/.agent-core-dev/`, port 8788). The intent was to give a developer working on agent_core itself an inner-loop daemon that doesn't compete with their prod install.

Two gaps surfaced when Jeff asked how to validate the new release pipeline without deploying to his actual prod daemon:

1. **"`dev`" is a misnomer.** Phase 3's "dev" is specifically the *repo-developer inner loop* (editable mode, no install step) — it does not mean what most teams mean by "dev" (a separate-but-prod-shape instance for staging/validation). The misleading name will compound across every future touch as engineers (human or agent) discover the surprise that `daemon install --instance dev` is deliberately an error.
2. **No instance to validate prod's deploy path against.** The unvetted piece of the release pipeline is `daemon refresh` actually pulling and installing from a real GH Release on a real daemon box (the daemon-side install path was unit-tested with stubs, never exercised end-to-end). Today there is no first-class way to install a release into a sandboxed home — only `AGENT_CORE_HOME=/tmp/sandbox daemon install --release vX.Y.Z` as a documented "test escape hatch." That escape hatch works but is ad-hoc; the validation workflow would benefit from being first-class.

### Concrete failure mode (this conversation)

On 2026-05-23, Jeff (away from his daemon box) wanted to validate PR #119 + a future v0.3.0 release before merging the release PR. The only available paths were: hand-rolled env-var sandboxing, or wait until home. He chose to bite the bullet and make the third instance first-class instead: "I'd rather just bite the bullet and do it right. I usually do."

### Goal

A three-instance daemon model that maps cleanly onto how teams actually think about environments:

- **`prod`** — what users run. Installed from release artifacts.
- **`source`** — repo-developer inner loop. Runs editable code from the workspace `.venv`. (Phase 3's `dev`, renamed.)
- **`test`** — a sandboxed instance that installs from release wheels via the SAME code path as `prod`. Where deploy-path validation happens before a prod refresh.

## Out of scope

- **Adding a new envelope kind or any bus-side schema change.** Phase 3.5 lives entirely at the instance-resolution + home-path layer; the bus / endpoint / channel machinery is unchanged.
- **`--from-local` flag for `daemon install --instance test`.** Pre-release validation by installing locally-built wheels into the test instance is a plausible future need, but no symptom has named itself yet. Reserved as a future-flag slot; deferred to a follow-up ticket when demand surfaces.
- **Autostart support for `source` or `test`.** Phase 4 (PR #110) makes autostart prod-only. Phase 3.5 expands the rejection list to `{source, test}` but does not add autostart-for-test. Long-running staging via autostart is a different design problem (two autostart entries, start ordering, boot conflicts) without a named symptom.
- **A `dev` → `source` deprecation alias.** Hard cutover instead. Phase 3 shipped only ~5 days before Phase 3.5; usage hasn't had time to accumulate; the inventory pass enumerated every reference; a missed caller fails loud-and-recoverable at CLI parse time with a clear "unknown value 'dev'" message.
- **Generalization to N>3 instances.** Two is too few; three matches how most teams reason; more would speculate on demand we don't see. Rule of three: extract when a fourth instance type names itself.

## Design

### Architecture

Three changes, all inside Phase 3's existing design surface:

**1. Extend the `Instance` enum** from `{prod, dev}` to `{prod, source, test}`. Phase 3's `dev` value renames to `source`. `test` is the new third value. Hard cutover — `--instance dev` becomes an unknown-value error at CLI parse time after the rename PR lands.

**2. `test` is prod's twin in a sandbox.** `home_for(test)` → `~/.agent-core-test/`; `default_port(test)` → 8787 (decrementing from prod=8789, source=8788). `daemon install --instance test --release vX.Y.Z` routes through the SAME install code path as `--instance prod` — same `release.py` functions, just with `home` set to the test home. The difference from `source`: test installs from release wheels; source runs editable from the workspace `.venv` and rejects `daemon install`.

**3. Autostart stays prod-only.** PR #110's `install-autostart` rejection list expands from `{dev}` to `{source, test}`. Both non-prod instances are manual-only.

### Components

**Architectural keystone:** `test` does NOT have a parallel "matches-prod" install implementation. It LITERALLY invokes the same `release.py` functions as `prod` — `resolve_version`, `list_release_wheels`, `download_wheels`, `download_requirements`, `ensure_venv`, `install_requirements`, `install_wheels`, `write_stamp` — with the `home` path resolved via `instance.home_for(Instance.TEST)`. The install code path is *identity*, not *parity*. Any drift between test-install and prod-install would invalidate the whole premise of test as a deploy-path-validation surface. The Test Plan (§Testing) names a specific test that ENFORCES the identity (asserts the same functions get called with same-shape arguments for both instances).

Five pieces:

**1. `packages/core/src/agent_core/daemon/instance.py` — extend the enum, add `TEST` mapping.**

```python
class Instance(StrEnum):
    PROD = "prod"
    SOURCE = "source"  # renamed from DEV
    TEST = "test"      # NEW


def home_for(instance: Instance) -> Path:
    return {
        Instance.PROD: Path.home() / ".agent-core",
        Instance.SOURCE: Path.home() / ".agent-core-source",  # renamed from -dev
        Instance.TEST: Path.home() / ".agent-core-test",      # NEW
    }[instance]


def default_port(instance: Instance) -> int:
    return {Instance.PROD: 8789, Instance.SOURCE: 8788, Instance.TEST: 8787}[instance]
```

`resolve_instance` keeps its precedence (`--instance` flag > `AGENT_CORE_INSTANCE` env > default `prod`). `AGENT_CORE_HOME` escape hatch stays — still useful for ad-hoc test-home variants beyond the standard `~/.agent-core-test/`.

**2. `packages/core/src/agent_core/daemon/cli.py` — update the CLI surface.**

- The `--instance` choice list expands from `{prod, dev}` to `{prod, source, test}`. `dev` parse-errors with `unknown value 'dev', expected one of: prod, source, test`.
- `daemon install --instance source` (formerly `dev`) keeps the deliberate-error semantic. Error message updated to name `source`.
- `daemon refresh --instance source` keeps the bounce-only semantic (no install work; just stop + start).
- `daemon refresh --instance test` is a proper install-and-restart (same as `prod`'s refresh).
- `daemon init --instance test` scaffolds `~/.agent-core-test/agent_core.yaml` with port 8787 + the minimal `builtin.stub` endpoint default (same shape as `prod`'s init template).

**3. `packages/core/src/agent_core/daemon/release.py` — NO CHANGES.**

Anchor for the keystone. The install code is already instance-agnostic — it takes a `home` Path and acts on it. Both `prod` and `test` call sites route through `home_for(instance)` and pass the resolved Path. Zero new code, zero parallel implementation. Explicit non-change, same load-bearing pattern as #114's `envelope.py` non-change anchoring the no-schema-migration promise: the validity of the whole feature depends on this non-change.

**4. `packages/core/src/agent_core/daemon/config_template.py` — add `TEST` scaffold.**

The existing `build_default_config(instance)` gains a `TEST` branch. Scaffolds the same shape as `prod`'s default config (port 8787, one `builtin.stub` endpoint named `stub`, same `bus.storage_path` resolution pattern, just rooted at the test home).

**5. Tests** — keystone enforcer + standard category coverage (see §Testing).

### Data Flow

Three flows. The first is the keystone in concrete sequence form; the others are compact.

**Flow 1: `daemon install --instance test --release vX.Y.Z`** (load-bearing)

1. CLI parse: `--instance test` resolves to `Instance.TEST` via existing `resolve_instance(flag="test")`.
2. Home resolution: `home_for(Instance.TEST)` → `~/.agent-core-test/` (or whatever `AGENT_CORE_HOME` overrides to).
3. Install code path: SAME function as `prod`'s install. Instance-agnostic; receives `home: Path` and operates on it.
4. Concrete sequence inside `release.py`:
   - `resolve_version(release_tag)` → returns `vX.Y.Z` (or errors).
   - `list_release_wheels(release_tag, ...)` → returns 10 wheel URLs from the GH Release.
   - `download_wheels(home / "releases" / release_tag, wheel_urls)` → fetches to `~/.agent-core-test/releases/vX.Y.Z/`.
   - `download_requirements(home / "releases" / release_tag, ...)` → same.
   - `ensure_venv(home / ".venv")` → creates / validates `~/.agent-core-test/.venv/`.
   - `install_requirements(home / ".venv", home / "releases" / release_tag / "requirements.txt")`.
   - `install_wheels(home / ".venv", wheels)`.
   - `write_stamp(home, installed_version=release_tag, release_tag=release_tag)`.
5. Final state: `~/.agent-core-test/.venv/` populated with vX.Y.Z wheels; `~/.agent-core-test/.daemon-install-stamp.json` records the version; release cache at `~/.agent-core-test/releases/vX.Y.Z/`.

**Same function calls, same structural arguments, only `home` differs. That is the keystone in concrete sequence form** — if the §Testing enforcer test fails, this is the sequence whose identity got broken.

**Flow 2: `daemon start --instance test`**

Resolve instance → resolve home → find venv at `home / ".venv" / "Scripts" / "python.exe"` (same lookup as `prod`, since test is wheel-installed) → read `home / "agent_core.yaml"` (port 8787) → spawn bus subprocess with `start_new_session=True` → write PID at `home / "daemon.pid"`. Distinct from `source`, which uses the workspace `.venv` via `find_workspace_root` instead.

**Flow 3: `daemon refresh --instance test --release vX.Y.Z`**

Compose of `stop --instance test` → `install --instance test --release vX.Y.Z` → `start --instance test`. Real install work each time (vs `source` which is bounce-only).

#### Coexistence semantics

All three instances can run simultaneously. Structural isolation per Phase 3: disjoint homes, ports (8789/8788/8787), PID files, SQLite DBs, install stamps, endpoint configs, logs. No shared lockfile / mutex / named pipe. A crash in any one instance cannot reach the others — the property that makes `test` safe to spin up during a real prod refresh window. Tearing down `test` (`daemon stop --instance test` + optionally `rm -rf ~/.agent-core-test/`) leaves zero residue affecting `prod` or `source`.

#### Env-var layering (unchanged from Phase 3)

- `AGENT_CORE_INSTANCE`: default instance when no `--instance` flag.
- `AGENT_CORE_HOME`: overrides `home_for(instance)` directly. Still useful for ad-hoc test-home variants beyond the standard `~/.agent-core-test/`.

#### Design calls baked into the flow

- **Install code is instance-agnostic by design.** `release.py` functions take a `home` Path; they do not switch on instance. Drift between prod-install and test-install requires actively breaking the abstraction.
- **No new bus-side or HTTP-side surface.** Test reuses every existing bus / endpoint / channel mechanism unchanged. New surface lives purely at the instance-resolution + home-path layer.

### Error Handling

Five surfaces, defended in layers using existing Phase 3 machinery + one new error class (the rename parse error).

**1. Rename-driven parse error: `--instance dev` no longer accepted** (hard cutover). CLI parser rejects with: `unknown value 'dev', expected one of: prod, source, test`. The single new error class this ticket introduces. Loud + recoverable: any caller hitting it gets a clear message + a clear fix (rename to `source`).

**2. `install --instance source` rejected** (existing Phase 3 semantics; rename updates the message text). Updates from "install on dev is not supported (dev runs editable from workspace)" to "install on source is not supported — source runs editable from the workspace `.venv`; use `daemon start --instance source` directly." Same shape, updated wording.

**3. `install-autostart --instance {source,test}` rejected** (PR #110's prod-only rejection list expands). Error: "autostart is prod-only — source runs editable from your workspace and test is for manual deploy validation. Run `install-autostart --instance prod` if you want autostart." Same shape as PR #110's current dev-rejection; rejection list expanded.

**4. Test install failures (inherits `prod`'s error surface).** Network failures during `download_wheels`, malformed release tag, missing wheels in the GH Release, insufficient disk, etc. All surface through the SAME `release.py` error paths as prod-install. No new error types; no new error messages. Test installs fail the same way prod installs would fail in identical conditions. This is the keystone applied to error handling: drift between prod-error-surface and test-error-surface would require actively breaking the install-code abstraction.

**5. Coexistence-edge errors (inherits Phase 3 entirely).**
- Port conflict on 8787: same surface as prod-on-8789 conflict.
- Missing venv on `start --instance test` after a clean home: clear "no venv found, run install first" error (same shape as prod).
- `AGENT_CORE_HOME` pointing at prod's home (user opts into the escape hatch and aims it badly): documented as user-responsibility hazard; no new safeguard added.
- Tear-down race (deleting `~/.agent-core-test/` while daemon runs): undefined behavior, same as prod; documented in `docs/setup/daemon.md`.

Two error-handling decisions baked in:

- **Test inherits `prod`'s error surface entirely.** No new exception types for test-specific failures. Drift would require actively breaking the install-code abstraction.
- **One new error CLASS total: the rename parse error.** Everything else is either inherited Phase 3 surface or a rewording of an existing Phase 3 error message. Net new error-handling code: minimal.

### Testing

#### Keystone enforcer test (the load-bearing one)

The single test that proves test-instance actually validates `prod`'s deploy path:

`test_install_code_path_identity_between_prod_and_test` in `packages/core/tests/test_daemon_release.py`.

Pattern:
1. Wrap each function in `release.py` (`resolve_version`, `list_release_wheels`, `download_wheels`, `download_requirements`, `ensure_venv`, `install_requirements`, `install_wheels`, `write_stamp`) with a call-capture decorator (via `monkeypatch.setattr`).
2. Run `daemon install --instance prod --release v0.2.0` against `tmp_path / "prod"` (via `AGENT_CORE_HOME` override). Capture the ordered list of `(function_name, args, kwargs)`.
3. Run `daemon install --instance test --release v0.2.0` against `tmp_path / "test"`. Capture the same.
4. Normalize the home-path component in each call's args/kwargs (replace `tmp_path/prod` and `tmp_path/test` with `<HOME>` placeholder).
5. Assert the two normalized call sequences are IDENTICAL.

Failure modes the test catches: any future change that adds a function call to one path but not the other; any future change that passes a structurally-different argument shape for prod vs test; any future refactor that accidentally splits the install code path into two parallel implementations. The keystone is enforced by code-equality, not by docstring promises.

#### Standard test categories (extending Phase 3's existing tests)

**`packages/core/tests/test_daemon_instance.py` (modify):** existing `dev` tests rename to `source` (3 cases each: enum value, `home_for`, `default_port`). Add same 3 cases for `TEST`. NEW: `test_resolve_instance_rejects_dev` — confirms the hard-cutover parse error.

**`packages/core/tests/test_daemon_cli.py` (modify):** rename `--instance dev` references to `--instance source`. NEW: `test_install_test_instance_succeeds_via_mock`, `test_install_source_instance_errors`, `test_unknown_instance_dev_parse_error` (the rename error path).

**`packages/core/tests/test_daemon_config_template.py` (modify):** rename existing dev tests to source. NEW: `test_build_default_config_for_test_instance` — scaffold has port 8787 + minimal stub endpoint.

**`packages/core/tests/test_dynamic_versioning.py` (modify):** rename any dev-related assertion to source.

**NEW `packages/core/tests/test_daemon_three_instance_coexistence.py`:** `test_three_instances_run_simultaneously_without_conflict` — spin up prod (mock port 8789), source (mock 8788), test (mock 8787); confirm all reach ready state; confirm PID files written to disjoint homes; confirm no port-bind error; tear down all three cleanly. May need the `integration` marker per Phase 3's windows-only integration job pattern.

#### PR-#110 ripple (notes for the rebase, NOT in Phase 3.5's PR)

`test_install_autostart_dev_instance_errors` renames to `test_install_autostart_source_instance_errors` + sibling `test_install_autostart_test_instance_errors`. Tracked as part of PR #110's rebase; not Phase 3.5's responsibility to commit but worth flagging so the rebase doesn't miss it.

#### Test count expectation

~15–20 new or modified tests in Phase 3.5's PR. ~3–4 modified in PR #110's rebase.

## Sequencing

Phase 3.5 lands first; PR #110 rebases on top. Reasoning: Phase 3.5 is a small targeted refactor; PR #110 is feature work; refactors land first to give feature PRs a clean base. PR #110's rebase touches ~5 lines (the `--instance dev` references in autostart code + tests + adding `test` to the prod-only rejection list). Trivial. Jeff's merge-pause stays in place either way until he's home to validate.

## Next-ticket triggers (deferred)

- **`--from-local` flag for `daemon install --instance test`.** Triggered when a symptom names itself for pre-release validation (e.g., catching bugs that only appear in built wheels — entry points, packaging metadata, data files — before they ship in a release). The args slot is reserved; the wiring lands when needed.
- **Long-running staging via autostart for `test`.** Triggered when someone wants test to always be running, possibly with scheduled refreshes tracking latest. Requires new design (two autostart entries, start ordering, boot conflicts).
- **Generalization beyond three instances.** Triggered when a fourth instance type names itself. Rule of three.

## Footnotes / tradeoffs

- **`AGENT_CORE_HOME` aimed at the wrong home.** The escape hatch can point at any path; a user setting `AGENT_CORE_HOME=~/.agent-core` while running `--instance test` will write test state into prod's home. Documented as user-responsibility; no new safeguard added because the escape hatch is explicitly opt-in.
- **Tear-down race.** `rm -rf ~/.agent-core-test/` while the test daemon is running leaves the daemon process holding handles to deleted files; eventually crashes. Same behavior as prod tear-down. Documented in `docs/setup/daemon.md`.
- **Loud-fail trade on the hard cutover.** `--instance dev` parse-error gives an immediate, clear failure with a known fix. Trade: any caller we missed in the inventory pass breaks immediately on first invocation rather than silently using a deprecated path. Recoverable via a one-line follow-up patch in the affected file. This is the better failure shape than silent compat-shim drift.
