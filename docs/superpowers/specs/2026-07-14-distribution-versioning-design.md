# Distribution & versioning — Design (Theme B, Cluster 1)

**Theme:** agent_core#265 (Theme B — portable install & lifecycle) · epic #262
**Date:** 2026-07-14
**Status:** approved design, pre-implementation

## Problem

Three coupled distribution defects (from the world-class eval):
- **Not published to PyPI** — clone-only; wheels go to GitHub Releases; the daemon self-installs from release artifacts. No `uvx`/`pipx`/`uv tool install` path for adopters. [P0]
- **cu130-GPU-only + hash-less deploy** — root `pyproject.toml` maps `torch → pytorch-cu130` index under the `cu130` extra, and `release.py` deploys with `--extra cu130 --no-hashes`, so *every* deployed daemon force-pulls CUDA-13 torch → **uninstallable on Apple Silicon / CPU Linux**, and hash-less → supply chain unverifiable. [P1]
- **`__version__` drift** — `packages/core/src/agent_core/__init__.py` hardcodes `__version__ = "0.1.0"` while dynamic versioning (git tags) says 0.7.0. [P2]

**Key grounding:** the core `agent-core` package is **already torch-free** (deps: claude-agent-sdk, pydantic, typer, fastmcp, apscheduler, sqlalchemy…); **only `agent-core-voice` pulls torch/madrigal**. So the code is already cleanly separated — GPU-decouple is a *deploy/packaging* change, not a refactor.

## Public PyPI set (locked with Jeff 2026-07-14)

- **Required core:** `agent-core` (bus+daemon+CLI, torch-free), `agent-core-busproxy` (being→bus MCP), `agent-core-channel` (MCP wake), `agent-core-credentials`.
- **Optional endpoints (via `agent-core[...]` extras):** `agent-core-voice` `[voice]`, `agent-core-discord` `[discord]`, `agent-core-inbound` `[inbound]`, `agent-core-notify` `[notify]`.
- **Bootstrap:** `agent-core-hatchery` — public, but **only after hardening** (see C1-3); joins the publish set in a follow-up wave, does NOT gate the pipeline.
- **Internal / NOT published (workspace-only):** `agent-core-briefs` (fleet-specific), `agent-core-webcam` (experimental).

## Design

### C1-a — GPU-decouple + reproducibility
- Base `agent-core` installs **torch-free** by default (already true at the package level). Deploy stops forcing `--extra cu130`.
- `agent-core[voice]` → depends on `agent-core-voice`, which declares **plain `torch`** (no index pin) → PyPI resolves CPU wheels on Apple Silicon, CUDA where available → **installs everywhere**.
- A specific CUDA build (cu130) is an **install-time** choice via uv index config (documented for GPU users; Jeff's deploy sets cu130). Published metadata never pins cu130 — inherent, since PyPI wheels can't dictate an alternate index.
- **Reproducibility:** the deployed daemon gets a **hashed** lock (drop `--no-hashes`).

### C1-b — PyPI publishing
- Publish the locked public set to PyPI via **trusted-publisher OIDC**, wired into `release.yml`. release-please already manages versions/tags.
- **Versioning: unified** — uv-dynamic-versioning (one git tag → all packages the same version) keeps the fleet coherent.
- Workspace `{ workspace = true }` sources resolve to real version constraints when published; the core's extras reference published siblings by version.
- **Adopter install:** `uv tool install agent-core` (torch-free core) / `uv tool install "agent-core[voice]"` / `uvx agent-core …`. The `daemon install` path shifts from GitHub-Release artifacts to PyPI resolution.

### C1-c — `__version__` drift
- Replace the hardcoded `__version__ = "0.1.0"` with `importlib.metadata.version("agent-core")` (single source of truth = the installed dist version).

## Ticket decomposition (4 tickets)

- **C1-1 · GPU-decouple + reproducible deploy** [P1] — stop forcing `--extra cu130` in `release.py`; `agent-core-voice` declares portable `torch`; deploy produces a hashed lock; document install-time cu130 for GPU users. *No dependency; the tent-pole.*
- **C1-2 · PyPI publish pipeline** [P0] — trusted-publisher OIDC + `release.yml` wiring; publish the locked public set (core required + optional-endpoint siblings); core extras reference published siblings; adopter install docs. ***`blocked_by` C1-1*** (don't publish the cu130-forced shape). Does NOT include hatchery.
- **C1-3 · Hatchery hardening** [P1] — fix #80 (config templates never rendered), #81 (gendered template assumptions), #82 (`.mcp.json` not rendered), and make it adopter-ready; then add `agent-core-hatchery` to the publish set (follow-up wave). *No dependency on the others; runs in parallel; hatchery's publish needs both this and C1-2's pipeline.*
- **C1-4 · `__version__` drift fix** [P2] — mechanical (`importlib.metadata`). *No dependency.*

## Non-goals / out of scope
- Cluster 2 (interpreter shim, being-side `.mcp.json` paths, `daemon doctor`/GC, `uv`-on-PATH) and Cluster 3 (lifecycle — already designed) — separate.
- Publishing `agent-core-briefs` / `agent-core-webcam` (kept internal).
- No collapse of the multi-package workspace into a single distributable.

## Testing
- **C1-1:** a base `uv sync` (no voice extra) resolves torch-free; `agent-core[voice]` resolves torch without an index pin; the deploy lock has hashes; (manual) base install succeeds on macOS/Apple Silicon.
- **C1-2:** dry-run publish (TestPyPI or `--dry-run`) for the public set; the core's extras resolve the published sibling versions; trusted-publisher OIDC configured; internal packages (briefs, webcam) are NOT published.
- **C1-3:** hatchery renders config/ + `.mcp.json` into a fresh vault; ungendered templates; a scaffolded being boots against the bus. (References the acceptance criteria on #80/#81/#82.)
- **C1-4:** `agent_core.__version__ == importlib.metadata.version("agent-core")`; matches the release tag.
