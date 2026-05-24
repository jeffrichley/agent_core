# Phase 2.6 — End-to-end install validation (Design)

> **Status:** Drafted 2026-05-23. Pending spec-review approval.
>
> **Issue:** to be filed by Pepper after spec sign-off; this doc precedes the GitHub issue.
>
> **Scope:** Fix the two bugs Phase 3.5's test instance surfaced on its first install attempt against `v0.2.0`. Both bugs live in the release pipeline (the export step in `release.yml` and the install step in `release.py`); both have been dormant in v0.2.0's already-shipped artifacts because the daemon-side install path has never been exercised on a real install. Phase 2.6 is the missing piece of Phase 2.5 — the validation step that proves the deploy path actually works end-to-end. No bus / endpoint / channel changes; the keystone (`release.py` structure) is preserved modulo a fallback path for Fix 1.

## Problem

Phase 3.5's test instance landed and was exercised against the real `v0.2.0` GitHub Release. The install failed at dependency resolution. Investigation surfaced two distinct bugs in the release pipeline:

### Bug 1 — Missing CUDA index for torch+cu130 resolution

`release.py:175-185` `install_requirements`:

```python
def install_requirements(req_path: Path, *, venv_python: Path) -> None:
    """Install pinned dependencies from a requirements.txt into the daemon venv.

    Resolves the PyTorch cu130 index URL embedded in the requirements file.
    """
    cmd = [
        "uv", "pip", "install",
        "--python", str(venv_python),
        "--requirement", str(req_path),
    ]
    subprocess.run(cmd, check=True)
```

The docstring CLAIMS "Resolves the PyTorch cu130 index URL embedded in the requirements file." The implementation does not. There is also no `--extra-index-url` directive embedded in the generated `requirements.txt`, no `[tool.uv.index]` config in `pyproject.toml`, and no command-line index flag on the `uv pip install` call. Intent diverged from implementation.

Concrete failure: `uv pip install -r requirements.txt` cannot find `torch==2.12.0+cu130` because the CUDA index (`https://download.pytorch.org/whl/cu130`) isn't reachable from any config layer uv consults. Resolution fails. Install fails.

The version itself is fine — `torch==2.12.0` exists on the cu130 index (verified by direct HTTP fetch). The bug is exclusively the missing index reference.

### Bug 2 — Workspace-relative editable paths in `requirements.txt`

Even with the index fix added (dry-run validated), install hits a second failure. The generated `requirements.txt` contains lines like:

```
-e ./packages/agent-core-briefs
-e ./packages/agent-core-busproxy
-e ./packages/agent-core-channel
-e ./packages/agent-core-discord
-e ./packages/agent-core-hatchery
-e ./packages/agent-core-voice
-e ./packages/agent-core-webcam
-e ./packages/core
-e ./packages/credentials
-e ./packages/notify
./packages/agent-core-voice/vendor/Qwen3-TTS
```

These are editable installs of workspace members, with paths relative to the install cwd. On the daemon side, the workspace doesn't exist — the daemon just gets wheels via the separate `install_wheels` step. The relative paths point at directories that don't resolve.

The `uv export` command in `release.yml` emits these workspace-member lines by default. The export should be configured to exclude them so the `requirements.txt` carries only third-party dependencies, with the agent_core packages delivered exclusively via the wheel-install path.

### Concrete failure mode (2026-05-23)

Test instance run via `daemon install --instance test --release v0.2.0` from a Phase 3.5 editable install. First subprocess call (`ensure_venv`) succeeded. Second call (`install_requirements`) failed at uv resolution with the torch+cu130 error. Prod was 100% untouched: PID 39956 alive throughout, install stamp unchanged, Discord shard resumed mid-test (continuous traffic). Phase 3.5's isolation property held; Phase 2.5's install path failed.

If Jeff had run `daemon refresh --instance prod --release v0.2.0` directly (the path he was about to take before the test instance existed), his prod daemon would have hit the same failure — leaving prod in a partially-broken state (venv created with no packages, install stamp possibly written depending on refresh's failure semantics).

### Why this dormant bug ships in v0.2.0

Jeff's prod daemon currently runs v0.1.0, installed 2026-05-20 from commit `156d3c8` — predating Phase 2.5's release-artifact deploy model. The new install path has therefore never been exercised on a real daemon. Both bugs lay dormant in v0.2.0's artifacts; they would have surfaced the first time anyone refreshed against v0.2.0+.

This ticket is Phase 2.5's missing validation step landing late.

## Out of scope

- **Refactoring `release.py`'s install function structure.** The function shape is correct; only specific call args / config need adjustment.
- **A new `--from-local` CLI flag** for `daemon install --instance test`. Reserved as Phase 3.5's deferred-flag slot. The end-to-end validation here uses uv directly against locally-built wheels, not via a CLI flag.
- **v0.2.0 artifact remediation.** The released artifacts on GitHub are immutable. After this fix lands, `daemon install --release v0.2.0` will still fail; the fix only takes effect for v0.3.0+ releases. Document in release notes; no programmatic guard against installing v0.2.0.
- **A unit test that asserts `uv` actually resolves the cu130 index** (network-dependent, version-flaky, tangled with infra). The "resolution actually succeeds" check belongs to the manual end-to-end validation step, not unit tests.
- **Cross-adapter audit for similar "intent diverged from implementation" docstring/code mismatches.** Out of scope for this ticket; a separate cleanup if a second instance ever surfaces (rule of three).

## Design

### Architecture

One ticket addresses two bugs in the release pipeline. The two fixes live in different files (`pyproject.toml` for the CUDA index; `release.yml` for the workspace export) but share a single validation surface (the Phase 3.5 test instance install). No `release.py` changes if `pyproject.toml` config propagates to `uv pip install` (the expected case); a fallback path exists for `release.py` if pyproject config doesn't reach the install step in the current uv version.

The keystone (`release.py` non-change) is preserved in the expected path. The fallback path touches `release.py` minimally (one-line addition of the `--extra-index-url` flag) and does not break the prod/test code-path identity asserted by Phase 3.5's `test_install_code_path_identity_between_prod_and_test`.

### Components

**Fix 1 — `pyproject.toml` (root): add the cu130 PyTorch index.**

Add a `[tool.uv]` section binding the `cu130` extra to PyTorch's hosted index. The expected uv schema (verify against the uv version on the workspace; recent versions have slight differences):

```toml
[[tool.uv.index]]
name = "pytorch-cu130"
url = "https://download.pytorch.org/whl/cu130"
explicit = true

[tool.uv.sources]
torch = [
  { index = "pytorch-cu130", marker = "extra == 'cu130'" },
]
```

Effect: both `uv export ... --extra cu130` (in `release.yml`) and `uv pip install -r requirements.txt` (in `release.py`) pick up the index URL from the pyproject config. Single source of truth; no code change to `release.py` needed.

**Fallback for Fix 1** (if pyproject config does not propagate to `uv pip install` cleanly in the current uv version): add `--extra-index-url=https://download.pytorch.org/whl/cu130` to `release.py:install_requirements`'s uv command. This is the implementer's call based on what actually works; report back if reaching for it.

**Fix 2 — `.github/workflows/release.yml`: exclude workspace members from requirements export.**

The current export step:

```yaml
uv export --frozen --no-dev --extra cu130 --no-hashes --format requirements-txt
```

Add a flag to exclude workspace members from emit. The expected flag is `--no-emit-workspace`; verify against the uv version. The corrected step:

```yaml
uv export --frozen --no-dev --extra cu130 --no-hashes --no-emit-workspace --format requirements-txt
```

Effect: the generated `requirements.txt` contains only third-party dependencies. The agent_core workspace packages continue to ship exclusively via `install_wheels` (the existing wheel-install path), unchanged.

**Fallback ordering for Fix 2** (per spec-review):
1. The uv `--no-emit-workspace` flag (or its current equivalent name).
2. A pyproject-level workspace config that excludes members from export.
3. A grep post-process step in `release.yml` that strips `-e ./packages/...` lines.

Prefer in that order. (3) is fragile (depends on uv's editable-line emit format staying stable across uv versions). Do not ship (3) as the chosen path; if reaching for it, escalate.

**Component 3: Tests in `packages/core/tests/test_daemon_release.py` (extend).**

Two new tests, both unit-shape, both about command/output SHAPE — not about resolution outcome (network/version-dependent and belongs to manual end-to-end validation).

- **Bug 1 test:** assert that the uv command passed to subprocess by `install_requirements` either includes `--extra-index-url=https://download.pytorch.org/whl/cu130` (if the fallback path was taken) OR that the pyproject's `[tool.uv.index]` config is present and well-formed (if the primary path was taken). Mock subprocess to capture the call; assert on its shape.
- **Bug 2 test:** run `uv export --no-emit-workspace ...` against a small fixture workspace (a tiny pyproject + a `packages/` dir with one stub member) and assert the output `requirements.txt` contains NO `-e ./packages/...` lines. Deterministic; no network.

**Component 4: Manual end-to-end validation (PR-description evidence, not a test).**

Stand up the Phase 3.5 test instance against a built-locally wheel set + the corrected `requirements.txt`. Show the install succeeds end-to-end. Document the run in the PR description as evidence the fix actually works.

Once v0.3.0 cuts with the fix landed, `daemon install --instance test --release v0.3.0` becomes the standing demo. That second-run validation IS the test instance proving its value a second time.

### Data Flow

Same shape as today; corrected edge content.

- `release.yml`'s `uv export` step now emits a `requirements.txt` that contains only third-party deps (workspace members excluded).
- `release.py`'s `install_requirements` step now reaches the cu130 index (via pyproject config, fallback via the `--extra-index-url` flag) and so resolves `torch==2.12.0+cu130` successfully.
- `release.py`'s `install_wheels` step is unchanged; the agent_core packages continue to install from the downloaded wheels.

Phase 3.5's keystone (`test_install_code_path_identity_between_prod_and_test`) continues to hold: prod and test still invoke the same `release.py` functions with same structural arguments modulo the home path. The fallback path for Fix 1 (if taken) modifies the uv command shape symmetrically for both prod and test calls.

### Error Handling

Inherits the existing release-install error surface. No new exception types. Both fixes flow through the same `subprocess.run(cmd, check=True)` path, so any future install failure surfaces the same way it does today (non-zero exit → caller decides recovery).

The behavior change is that previously-silent dependency-resolution failures now succeed; the post-fix path doesn't introduce new failure modes, only closes existing ones.

### Testing

Covered in §Components 3 + 4 above. Unit-test count expectation: ~2 new tests. Manual end-to-end validation captured in PR description.

## Sequencing

Phase 2.6 lands as a standalone ticket. Order relative to other open PRs:

- **PR #119** (AI Cliché Detector — unrelated to release pipeline) — independent; can merge in any order.
- **PR #120** (Phase 3.5 — three-instance daemon) — Phase 2.6 depends on the test-instance surface Phase 3.5 ships. If both are merged, Phase 2.6 can use first-class `--instance test`; if only Phase 2.6 merges first, the test instance validation falls back to the env-var stopgap (`AGENT_CORE_HOME` override). Strong preference: Phase 3.5 first, Phase 2.6 second.
- **PR #110** (Phase 4 windows autostart) — independent of Phase 2.6.

Recommended order: PR #120 (Phase 3.5) → PR #110 (Phase 4) → Phase 2.6 → cut v0.3.0 → daemon refresh on Jeff's box.

Jeff's merge-pause stays in place throughout; this ticket's PR sits at the release gate alongside the others.

## Next-ticket triggers (deferred)

- **`--from-local` flag for `daemon install --instance test`.** Reserved from Phase 3.5. Useful for pre-release validation without going through the full GH-release cycle. Triggered when validation workflows want a one-command path instead of hand-running `uv` directly against built wheels.
- **Cross-adapter audit for docstring/code drift.** Bug 1's failure mode (docstring claims behavior the implementation doesn't deliver) is the kind of thing that could repeat in other modules. Triggered if a second instance surfaces.

## Footnotes / tradeoffs

- **v0.2.0 artifacts remain broken after this fix.** The fix only takes effect for releases cut AFTER it lands. Document in release notes; no programmatic guard. Risk: someone manually runs `daemon refresh --release v0.2.0` post-fix and hits the same failures. Mitigated by the small population of users (currently: Jeff) and the daemon-refresh wrapper documentation.
- **uv version drift.** Both fixes depend on uv flag names and pyproject schema. uv is pre-1.0 and schema changes between releases. Implementer should pin or verify the uv version against the workspace's lockfile and update flag names if upstream has renamed them.
- **Fix 2 grep-fallback fragility** (per spec-review). Last-resort only; do not ship as chosen path; escalate before adopting.
