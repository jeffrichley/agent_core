# Theme F Track A — agent-core PyPI Launch + Adopter Docs

**Epic:** #262 · **Theme:** F #269 (Quality, testing & documentation) · **Track:** A (adopter-facing).
**Status:** Design approved 2026-07-16. Track B (testing/CI tech-debt) is a separate later spec.

## 1. Context & driver

Two external people want to run agent-core themselves. Rather than hand them the source
or a private wheel feed, we are doing a **full public launch of agent-core on PyPI** — the
real adopter story. Theme F's three docs-P0s (getting-started, de-Wren-ify, architecture
overview) plus the packaging work needed to make `uv add agent-core-*` real.

This splits into two sub-projects with a hard dependency: **A1 (packaging & publish)** must
land before **A2 (adopter docs)**, because the getting-started guide documents the real
install command that A1 creates.

## 2. Goals / non-goals

**Goals**
- A new person can `uv add agent-core-<endpoint>` from PyPI, hatch a being, run the daemon,
  and connect an endpoint — following public docs that make no assumption the reader is Wren.
- All 12 packages published to PyPI at a synchronized version, via automated CI.
- A release gate proves the *published* packages actually install-and-run (not just the
  in-repo workspace).

**Non-goals (deferred to Track B)**
- mypy coverage expansion, god-module splits, `audit.py` de-duplication, slow-test marking,
  the broader CI-gate wiring beyond the PyPI round-trip.
- Publishing `qwen-tts` (vendored third-party dep, not ours).

## 3. Current state (grounded 2026-07-16)

- **12 workspace packages** under `packages/`: `core/` → dist name **`agent-core`**, plus 11
  **`agent-core-*`** (briefs, busproxy, channel, credentials, discord, hatchery, inbound,
  notify, qa, voice, webcam). A 13th workspace member `qwen-tts` is vendored — excluded.
- **Internal deps use `{ workspace = true }`** (`[tool.uv.sources]` in root `pyproject.toml`).
  These resolve locally but are **not PyPI-installable** — they must become real version pins.
- **Release automation already exists**: `release-please.yml` + `release.yml` — currently
  targeting the private GitHub-Release feed.
- **All PyPI names verified available** (2026-07-16): `agent-core` + every `agent-core-*` → 404.
- **GPU index dependency**: root declares `[[tool.uv.index]] name = "pytorch-cu130"`; packages
  like `agent-core-voice`/`agent-core-webcam` pull torch from it. Adopters installing from PyPI
  need this handled (see Risks).

## 4. A1 — Packaging & PyPI publish

### A1.1 Claim names
Register `agent-core` + all 11 `agent-core-*` on PyPI ahead of / at first publish (all free).
Do it via the first Trusted-Publishing release, not manual placeholder uploads, so the first
real artifact owns the name.

### A1.2 Publish-clean dependency declarations
Replace each package's internal `{ workspace = true }` resolution with real, pinned
`[project] dependencies` (e.g. `agent-core-channel` declares `agent-core>=0.2,<0.3`). Keep the
`[tool.uv.sources]` workspace block for local dev (uv prefers the workspace locally), but ensure
the **built** wheel/sdist carries version constraints so a PyPI install resolves the whole graph
from PyPI. Verify by building each package and inspecting the wheel METADATA `Requires-Dist`.

### A1.3 Synchronized version train
All 12 publish at one lockstep version (**v0.2.0**, matching the `build-v0.2.0` line). Drive the
bump + changelog through the **existing** release-please setup (configure it for a single
grouped version across the workspace rather than independent per-package versions).

### A1.4 Publish CI (PyPI Trusted Publishing)
Extend the existing `release.yml` to publish wheels+sdists to **PyPI via Trusted Publishing
(OIDC)** — no long-lived PyPI tokens stored (consistent with Theme D's secrets posture). Build
all 12 with `uv build` per package; upload on a release-please-tagged release. Keep the private
GitHub-Release feed in parallel for now; retire it in a follow-up once PyPI is the source of truth.

### A1.5 Release gate — real-install round-trip
Wire `agent-core-qa` (the 7-scenario dynamic validator, currently CI-orphaned) to run against a
**fresh install of the published packages from PyPI** (or TestPyPI in a pre-release job): create
a clean venv, `uv add` the packages, hatch a being, start the daemon, connect an endpoint, assert
the round-trip. This gate is what proves the *published* artifacts work, not just the monorepo.
(Full CI-integration of agent-core-qa beyond this round-trip is Track B.)

## 5. A2 — Adopter docs (depends on A1)

### A2.1 Getting-started — P0
End-to-end for a new person: `uv add agent-core-<endpoint>` → hatch a being (hatchery) → run the
daemon → connect an endpoint → send/receive a first message. Real, copy-pasteable commands using
the PyPI packages. Replaces the 19-line README as the primary entry.

### A2.2 Architecture overview — P0
One document stating the **bus + daemon + sidecar + pluggable-endpoint** model, currently only
inferable from ~50 dated spec files + a stale ROADMAP. Diagram + the core nouns (bus, daemon,
endpoint, being, envelope) and how they compose.

### A2.3 De-Wren-ify — P0
Parameterize the being identity out of the docs and defaults surface: `~/.wren` → `~/.<being>`,
drop hardcoded `wrenrichley`/foreman-private-feed, so `daemon.md`, the inbound README, etc.
describe "a being" generically. Audit for `wren`/`pepper`/`jeff`-specific strings in adopter-path
docs and config samples.

### A2.4 Hatch-your-own-being + CONTRIBUTING + package READMEs — P1
"Hatch your own being" walkthrough, a CONTRIBUTING guide, an "add an endpoint" reference, and a
README for each package that lacks one (incl. `core` and `busproxy`); document bus config keys
outside the sample file.

## 6. Sequencing & dependencies

```
A1.2 deps-clean ──▶ A1.3 version train ──▶ A1.4 publish CI ──▶ A1.5 release gate ──▶ A2.* docs
                                              │
A1.1 name claims ─────────────────────────────┘ (claimed at first publish)
```
A2 docs are authored against A1's real install command. A2.2 (architecture) and A2.3
(de-Wren-ify) have no hard A1 dependency and could start in parallel, but publish before A2.1.

## 7. Ticket slicing (under #269)

Target ~8–10 tickets:
- **A1-1 [P0]** deps-clean: workspace refs → pinned `Requires-Dist`, verified via built wheels.
- **A1-2 [P0]** release-please → single synchronized version train across the 12 packages.
- **A1-3 [P0]** `release.yml` → PyPI Trusted Publishing (OIDC), all 12 built + uploaded.
- **A1-4 [P1]** release gate: agent-core-qa round-trip vs a real (Test)PyPI install.
- **A2-1 [P0]** getting-started (clone-free `uv add` → hatch → run → connect).
- **A2-2 [P0]** architecture overview (one doc + diagram).
- **A2-3 [P0]** de-Wren-ify adopter-path docs + config samples.
- **A2-4 [P1]** hatch-your-own-being walkthrough + CONTRIBUTING.
- **A2-5 [P1]** per-package READMEs + "add an endpoint" reference + bus config keys.

Dep-wire: A1-1 → A1-2 → A1-3 → A1-4; A2-1 blocked_by A1-3; A2-4/A2-5 after A2-1.

## 8. Risks & open concerns

- **Workspace→PyPI resolution.** The biggest correctness risk: a wheel that still carries a
  workspace-only ref (or an unpinned dep) install-fails from PyPI. Mitigation: build + inspect
  every wheel's `Requires-Dist`; the A1.5 round-trip catches escapes.
- **Torch / GPU index.** `agent-core-voice`/`-webcam` depend on torch via the `pytorch-cu130`
  custom index. A plain `uv add agent-core-voice` from PyPI won't know that index. Options:
  document the extra `--index`/`[[tool.uv.index]]` step for GPU endpoints, or make torch an
  optional extra so the base install stays index-free. Decide during A1-1.
- **First-publish irreversibility.** Once a name+version is on PyPI it can't be reused. Rehearse
  on **TestPyPI** first; only promote to real PyPI when the round-trip is green.
- **Secret-free publish.** OIDC Trusted Publishing must be configured per-package on PyPI
  (publisher = the GitHub repo + workflow) before the first release can push.
