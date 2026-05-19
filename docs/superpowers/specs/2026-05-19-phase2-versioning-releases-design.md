# Phase 2 — Versioning & Releases: Design

**Status:** approved in brainstorm 2026-05-19, ready for implementation plan.

**Relationship to the maturity spec:** Refines the `Phase 2 — Versioning
& releases` section of
`docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md`. Where
they differ, **this document wins**. It also **corrects a false premise**
in the umbrella spec: that spec says "towncrier (already configured
per-package)" — verified false. There is **no `[tool.towncrier]` config
anywhere** (root or any member); `towncrier` is only a dev dependency.
News *fragments* exist (towncrier `<issue>.<type>.md` convention) but only
**4 of 10** members have a `changelog.d/` (core, agent-core-discord,
credentials, notify). Phase 2 therefore *sets up* towncrier, it does not
merely "use" it. Builds on merged Phase 0 (`[tool.uv] cache-keys`) and
Phase 1 (CI gate + `phase1-main-gate` ruleset). The Phase 2 PR must pass
that ruleset.

---

## 1. Goal

Two decoupled pillars (per §3 of the umbrella spec, versioning is
independent of the Defect-A correctness fix):

1. **VCS-derived versioning** — every build's wheel metadata carries a
   real, git-derived PEP 440 version so "what version is running" is a
   true signal, surfaced by `daemon status`.
2. **Releases** — a single aggregated, towncrier-driven `CHANGELOG.md`
   under one lockstep `vX.Y.Z` tag series; Phase 2 cuts the inaugural
   `v0.1.0`.

Out of scope (per umbrella spec): PyPI publishing, SemVer release
automation (release-please/commitizen), Conventional-Commits
enforcement.

## 2. VCS-derived versioning

### 2.1 Per-member `pyproject.toml` switch (all 10 members)

Example `core` (currently at the Phase-0 state: static
`version = "0.1.0"`, `[build-system] requires = ["hatchling"]`,
`[tool.uv] cache-keys`). Final shape:

```toml
[build-system]
requires = ["hatchling", "uv-dynamic-versioning"]
build-backend = "hatchling.build"

[project]
name = "agent-core"
dynamic = ["version"]            # replaces: version = "0.1.0"
# ...existing fields/dependencies unchanged...

[tool.hatch.version]
source = "uv-dynamic-versioning"

[tool.uv-dynamic-versioning]
fallback-version = "0.0.0"       # used only when .git absent (sdist)

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core"]    # unchanged (per member)

[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]   # Phase 0 — unchanged
```

Each member: drop static `version`, add `dynamic = ["version"]`, add
`uv-dynamic-versioning` to `build-system.requires`, add
`[tool.hatch.version] source = "uv-dynamic-versioning"` and
`[tool.uv-dynamic-versioning] fallback-version = "0.0.0"`. The Phase 0
`[tool.uv] cache-keys` block and the existing
`[tool.hatch.build.targets.wheel]` are left intact.

### 2.2 Tool & config

- **`uv-dynamic-versioning`** (hatchling plugin, dunamai-backed,
  uv-workspace-native — chosen over `hatch-vcs`/`setuptools-scm` per the
  umbrella spec's considered-alternatives).
- **Exact `[tool.uv-dynamic-versioning]` keys** (`pattern`, `style`,
  `bump`, `vcs`, `latest-tag`, etc.) are **verified via the context7 MCP
  at plan time** — not guessed. Required behavior, fixed here:
  - PEP 440 output.
  - **Tag pattern matches only `^v\d+\.\d+\.\d+`** so the repo's two
    `pepper-cutover-*` tags are ignored (verified: only those 2 tags
    exist, both non-`v`).
  - Exactly on a `vX.Y.Z` tagged commit → version is `X.Y.Z`.
  - Between tags → a PEP 440 dev/post form with the **git sha embedded**
    (e.g. `0.1.0.postN.devM+g<sha>`) — the "what's running" signal.
  - `.git` absent (sdist/tarball only) → `fallback-version` (`0.0.0`),
    no crash. The daemon path always builds from a checkout (has
    `.git`).

### 2.3 `uv.lock` (research-verified, no thrash)

Dynamic-version workspace members omit the `version` field in `uv.lock`,
so source-only commits do not thrash the lockfile and the daemon's
`uv sync --frozen` stays valid. A **one-time `uv lock`** is run after the
metadata switch and committed; the Phase 1 CI `uv sync --locked` fails
fast if it was forgotten.

## 3. Releases & towncrier

### 3.1 Changelog model — single aggregated root changelog

- **One `[tool.towncrier]` config** added to the root `pyproject.toml`,
  producing a single top-level **`CHANGELOG.md`**. This matches the
  lockstep reality (one `vX.Y.Z` tag, one daemon deploy for all 10
  packages); 10 per-package changelogs would be ceremony.
- **Fragments stay per-package**: `packages/*/changelog.d/` holds
  `<issue>.<type>.md` fragments; towncrier is configured to collect from
  every member's `changelog.d/`. Contributors keep writing fragments
  next to the code they changed.
- **Backfill**: add `changelog.d/.gitkeep` to the 6 members lacking the
  dir (agent-core-channel, agent-core-briefs, agent-core-webcam,
  agent-core-hatchery, agent-core-voice, agent-core-busproxy). The 4
  that already have fragments (core, agent-core-discord, credentials,
  notify) are untouched.
- **Fragment types**: the keepachangelog set — `added`, `changed`,
  `deprecated`, `removed`, `fixed`, `security` — which covers the
  existing `.added/.changed/.fixed` fragments in the tree.

### 3.2 `just release VERSION` recipe

Mirrors the Phase 1 `just install-hooks` ergonomic pattern. `just
release 0.1.0`:

1. `towncrier build --yes --version 0.1.0` (consumes + deletes
   fragments, writes/updates root `CHANGELOG.md`).
2. Creates a **local annotated tag** `v0.1.0`.

It deliberately **does not push** — pushing the tag is a separate,
explicit, confirmed step (consistent with the project's
"confirm shared-state actions / respect the gate" preference). There is
**no version-bump commit** — the tag is the version input.

### 3.3 Inaugural `v0.1.0` & sequencing

- The towncrier config, the generated/initial `CHANGELOG.md`, the
  consumed fragments' deletions, the pyproject switch, the `uv lock`,
  the `just release` recipe, the `daemon status` change, and the tests
  are all part of the **Phase 2 PR**, which must pass `phase1-main-gate`
  (the 3 green CI checks) like any change.
- **Sequencing (critical):** the annotated `v0.1.0` tag is created on
  the **`main` merge commit AFTER the Phase 2 PR lands** — never on the
  feature branch — so the tag points at real `main` history and
  `uv-dynamic-versioning`'s `git describe` math is correct. Pushing the
  tag is done with **explicit user confirmation** (shared-state).
- The first `towncrier build` **absorbs the existing accumulated
  fragments** (~13 across the 4 members) into the first `CHANGELOG.md`.
  This is intended and confirmed; it is effectively irreversible
  (fragments deleted, recorded in `CHANGELOG.md` + git history).

## 4. `daemon status` version surfacing

`agent-core daemon status` (in
`packages/core/src/agent_core/daemon/cli.py`) additionally prints the
installed version, read from the **daemon venv's** installed wheel
metadata via `importlib.metadata.version("agent-core")` resolved against
that venv (the same venv `status` already introspects for the stamp /
`installed_sha`):

```
installed sha: 5c92e3a
installed version: 0.1.0
```

- Read-only, best-effort: if metadata is unresolvable (daemon venv
  absent / pre-install), print `installed version: unknown` and do
  **not** fail `status` — same defensive posture as the existing
  stamp/lock-drift checks.
- No install-stamp schema change: the version lives in the built
  wheel's metadata (that is the §3 point — the wheel is the source of
  truth). `installed_sha` (commit identity) + `installed version`
  (human VCS-derived version) together describe what is deployed.

## 5. Testing strategy

All fast (Phase 1 `check` gate); none needs the slow/integration lane
unless a real `uv build` exceeds 5s (then marked `slow`, like the Phase
0 install regression):

- **Version-format:** build a member wheel from a tagged temp-repo
  commit → metadata version is exactly `X.Y.Z`; from an untagged commit
  → matches the PEP 440 dev/post shape with sha embedded.
- **`fallback-version`:** build with no `.git` → version is
  `fallback-version`, not a crash.
- **`uv.lock` no-thrash:** dynamic members carry no `version` in
  `uv.lock`; a source-only commit leaves `uv.lock` byte-unchanged.
- **`daemon status`:** with a daemon venv, `status` prints an
  `installed version:` line; without one, prints `unknown` and exits 0.
- **towncrier:** `towncrier build --draft` over fixture fragments yields
  the expected aggregated sections; a **guard test** asserts every
  `packages/*` has a `changelog.d/` (a new member cannot silently drop
  out of the changelog — same spirit as the Phase 0 cache-keys guard).
- **Acceptance:** the Phase 2 PR is gated by `phase1-main-gate`. After
  merge, cutting `v0.1.0` and confirming a fresh `daemon refresh`
  reports `installed version: 0.1.0` is the end-to-end proof.

## 6. Risks → mitigations

| Risk | Mitigation |
|---|---|
| Forgot `uv lock` after the switch | Phase 1 CI `uv sync --locked` fails fast |
| Tag created on feature branch → wrong version math | Spec mandates tagging the post-merge `main` commit only |
| `pepper-cutover-*` tags picked up by the version tool | Tag `pattern` restricted to `^v\d+\.\d+\.\d+` (only 2 non-`v` tags exist) |
| Exact tool keys guessed wrong | `[tool.uv-dynamic-versioning]` keys verified via context7 at plan time |
| A future member silently missing from the changelog | Guard test asserts every `packages/*` has `changelog.d/` |
| Fragment-backlog absorption is irreversible | Explicitly confirmed with Jeff; recorded in `CHANGELOG.md` + git history |
| Tag push is shared-state | Pushed only with explicit user confirmation, post-merge |

## 7. Rollout (CI-gated, mirrors Phase 0/1)

1. Phase 2 PR (pyproject switch + `uv lock` + root `[tool.towncrier]` +
   6 `changelog.d/.gitkeep` + `just release` recipe + `daemon status`
   change + tests + the inaugural `CHANGELOG.md`) → passes
   `phase1-main-gate` → merged via PR (not local-push; respect the
   gate).
2. **After merge:** on the `main` merge commit, `just release 0.1.0`
   (build `CHANGELOG.md` from fragments + local annotated `v0.1.0`
   tag) → push the tag **with explicit confirmation** → verify a fresh
   `daemon refresh` reports `installed version: 0.1.0`.
3. Phases 3 (dev/prod instance-parameterization) and 4 (Windows
   auto-start) remain independent follow-ups; 4 depends on 3.

## 8. One-time setup (recorded so it is not lost)

- One-time `uv lock` after the dynamic-version metadata switch
  (committed in the Phase 2 PR).
- Post-merge inaugural release: `just release 0.1.0`, then push `v0.1.0`
  with explicit confirmation.
