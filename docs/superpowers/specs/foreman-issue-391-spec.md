# Spec: configure release-please for single synchronized version train across all 12 packages (issue #391)

## Goal

Extend `release-please-config.json` and `.release-please-manifest.json` to include all 12
installable workspace packages alongside the existing root `"."` entry so that one
release-please run produces a single grouped PR, per-package changelogs, and a shared version
train. Addresses issue #391 under Track A of the PyPI launch plan
(`docs/superpowers/specs/2026-07-16-theme-f-track-a-pypi-launch-design.md`).

---

## Acceptance criteria

- `release-please-config.json` has explicit entries for all 12 packages (listed below) alongside
  the existing `"."` root entry.
- Each of the 12 package entries specifies `release-type: simple`, its `component` name (the
  PyPI distribution name), `changelog-path: CHANGELOG.md` (relative to the package dir), and
  `version-file: VERSION`.
- Each of the 12 package entries sets `skip-github-release: true` so that only the root `"."`
  entry creates the canonical GitHub Release and `v<N>` git tag.
- `.release-please-manifest.json` lists all 12 package paths at version `"0.7.0"` (the current
  repo version, matching the root `"."` entry already present).
- A `VERSION` file containing `0.7.0` exists in each of the 12 package directories. These are
  release-please tracking files; `uv-dynamic-versioning` reads the git tag instead and ignores
  them.
- The existing root settings (`separate-pull-requests: false`, `bump-minor-pre-major: true`,
  `bump-patch-for-minor-pre-major: false`, `include-v-in-tag: true`) are preserved unchanged.
- `qwen-tts` (`packages/agent-core-voice/vendor/Qwen3-TTS`) is **not** added to the config.
- No changes to `.github/workflows/release-please.yml` or `release.yml`.

---

## Approach

No GoF pattern fits this change. It is configuration plumbing. The chosen design follows
Google's "make the right thing easy" principle: by encoding all 12 packages in the release-please
config, the tooling prevents ad-hoc per-package version drift without requiring any runtime
enforcement logic.

**Why `uv-dynamic-versioning` is the real lockstep guarantee.**
Each of the 12 packages declares `dynamic = ["version"]` and derives its version at build time
from the most recent git tag matching `^v(?P<version>.*)$` (the `uv-dynamic-versioning` /
`hatch-vcs` default). The root `"."` entry in release-please is the sole source that creates
root-format tags (`v0.8.0`). Sub-package component tags (`agent-core-briefs-v0.8.0`) do NOT
match the default tag pattern and are therefore invisible to `uv-dynamic-versioning`. Result:
all 12 packages always build at the version encoded in the root git tag, regardless of whether
the manifest entries have minor drift between packages.

**Why keep the root `"."` entry.**
Removing `"."` would mean no `v<N>` root tags, which would break `uv-dynamic-versioning` for
every package. The root entry must remain and is the sole entry that creates the GitHub Release
(`skip-github-release` is NOT set on `"."`).

**Why `release-type: simple` (not `python`) for sub-packages.**
The packages use `dynamic = ["version"]` — there is no static `version = "..."` field in their
`pyproject.toml` for release-please's `python` strategy to update. The `simple` strategy manages
an explicit `VERSION` file (configured via `version-file: VERSION`), which release-please creates
and updates, leaving the `pyproject.toml` untouched. `uv-dynamic-versioning` ignores `VERSION`
files; it reads git tags. The `VERSION` files are purely for release-please bookkeeping.

**Why `skip-github-release: true` on sub-packages.**
Each package entry in release-please would otherwise create a GitHub Release object for its
component tag (e.g., `agent-core-briefs-v0.8.0`). That would produce 13 GitHub Releases per
version instead of one. Setting `skip-github-release: true` suppresses Release objects for the
12 sub-packages while still creating their component git tags (for changelog provenance) and
still creating the canonical `v<N>` GitHub Release from the root `"."` entry.

**Directory → PyPI distribution name mapping (all 12):**

| Directory | Distribution name |
|---|---|
| `packages/core` | `agent-core` |
| `packages/agent-core-briefs` | `agent-core-briefs` |
| `packages/agent-core-busproxy` | `agent-core-busproxy` |
| `packages/agent-core-channel` | `agent-core-channel` |
| `packages/credentials` | `agent-core-credentials` |
| `packages/agent-core-discord` | `agent-core-discord` |
| `packages/agent-core-hatchery` | `agent-core-hatchery` |
| `packages/agent-core-inbound` | `agent-core-inbound` |
| `packages/notify` | `agent-core-notify` |
| `packages/agent-core-qa` | `agent-core-qa` |
| `packages/agent-core-voice` | `agent-core-voice` |
| `packages/agent-core-webcam` | `agent-core-webcam` |

---

## Sub-requests (topologically sorted)

1. **Update `release-please-config.json`** — add 12 package entries inside the existing
   `"packages"` object. Each entry:

   ```json
   "packages/core": {
     "release-type": "simple",
     "component": "agent-core",
     "changelog-path": "CHANGELOG.md",
     "version-file": "VERSION",
     "skip-github-release": true
   }
   ```

   Repeat for all 12 using the directory → name table above. Root `"."` entry is unchanged.

2. **Update `.release-please-manifest.json`** — add the 12 package paths at version `"0.7.0"`:

   ```json
   {
     ".": "0.7.0",
     "packages/core": "0.7.0",
     "packages/agent-core-briefs": "0.7.0",
     "packages/agent-core-busproxy": "0.7.0",
     "packages/agent-core-channel": "0.7.0",
     "packages/credentials": "0.7.0",
     "packages/agent-core-discord": "0.7.0",
     "packages/agent-core-hatchery": "0.7.0",
     "packages/agent-core-inbound": "0.7.0",
     "packages/notify": "0.7.0",
     "packages/agent-core-qa": "0.7.0",
     "packages/agent-core-voice": "0.7.0",
     "packages/agent-core-webcam": "0.7.0"
   }
   ```

3. **Create `VERSION` files** — in each of the 12 package directories, create a file named
   `VERSION` containing the single line `0.7.0`. These are release-please tracking files only;
   `uv-dynamic-versioning` ignores them.

   Files to create:
   - `packages/core/VERSION`
   - `packages/agent-core-briefs/VERSION`
   - `packages/agent-core-busproxy/VERSION`
   - `packages/agent-core-channel/VERSION`
   - `packages/credentials/VERSION`
   - `packages/agent-core-discord/VERSION`
   - `packages/agent-core-hatchery/VERSION`
   - `packages/agent-core-inbound/VERSION`
   - `packages/notify/VERSION`
   - `packages/agent-core-qa/VERSION`
   - `packages/agent-core-voice/VERSION`
   - `packages/agent-core-webcam/VERSION`

4. **Verify the JSON is valid** — run `python3 -m json.tool release-please-config.json` and
   `python3 -m json.tool .release-please-manifest.json` to confirm no syntax errors.

---

## File-level changes

| File | Change |
|---|---|
| `release-please-config.json` | **Modify** — add 12 package entries to the `packages` object; root `"."` entry unchanged |
| `.release-please-manifest.json` | **Modify** — add 12 package path entries at `"0.7.0"` |
| `packages/core/VERSION` | **Create** — `0.7.0` (release-please tracking file) |
| `packages/agent-core-briefs/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-busproxy/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-channel/VERSION` | **Create** — `0.7.0` |
| `packages/credentials/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-discord/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-hatchery/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-inbound/VERSION` | **Create** — `0.7.0` |
| `packages/notify/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-qa/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-voice/VERSION` | **Create** — `0.7.0` |
| `packages/agent-core-webcam/VERSION` | **Create** — `0.7.0` |

---

## Alternatives considered

1. **Keep only root `"."` and rely entirely on `uv-dynamic-versioning` for lockstep** — the
   simplest option and already satisfies the lockstep version requirement (all packages build
   at the same git tag version). Ruled out: the root CHANGELOG.md already captures all commits,
   but the release PR says nothing about which packages are included; there are no per-package
   changelogs; the config gives no evidence that the 12 packages are treated as a unit. The
   explicit per-package entries make the release surface auditable and generate per-package
   changelogs useful for PyPI release notes.

2. **Use `release-type: python` for sub-packages** — release-please's `python` strategy edits
   `version = "..."` inside `pyproject.toml`. Ruled out: all 12 packages declare
   `dynamic = ["version"]`; there is no `version = "..."` field for release-please to update.
   The `python` strategy would either error or silently no-op on these files, leaving the
   manifest as the only tracked state (same as `simple` without a VERSION file, but less explicit).

3. **Add `include-component-in-tag: false` for all sub-packages** — would suppress the
   `<component>-v0.8.0` per-package tags and produce only `v0.8.0` root tags. Ruled out:
   multiple packages attempting to create the same `v0.8.0` tag in one release would produce
   a conflict or race; release-please does not deduplicate tag creation across packages.

4. **Derive per-package changelogs via a post-release script** — run a script that filters the
   root CHANGELOG.md by commit scope and appends to per-package `CHANGELOG.md` files. Ruled out:
   YAGNI; release-please already generates per-package changelogs natively from commits in each
   package directory; adding a script introduces a maintenance burden without benefit.

---

## Open questions

None. The approach is grounded in the existing config file shapes and the `uv-dynamic-versioning`
behaviour verified in the repo.

---

## Out of scope

- Changes to `.github/workflows/release-please.yml` or `release.yml` (those are A1-3 scope, already merged in `foreman/impl-392`).
- A1-1 (workspace `{ workspace = true }` refs → real version pins): prerequisite ticket.
- A1-4 (agent-core-qa round-trip release gate): follow-on ticket.
- Publishing `qwen-tts` or `agent-core-webcam` policy decisions: not changed here.
- Migrating the root changelog to towncrier: the per-package `CHANGELOG.md` stubs already have
  towncrier markers but the root uses release-please format; harmonising them is a separate concern.
