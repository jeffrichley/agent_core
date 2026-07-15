# Interpreter / venv resolution — design

**Date:** 2026-07-14
**Epic:** #262 (get-to-world-class). **Theme:** B — Portable install & lifecycle (#265), **Cluster 2**.
**Status:** approved (Jeff, 2026-07-14). Ready to slice into tickets.

## Problem

Three coupled Theme B line-items, all about *how a being's MCP sidecars find their Python interpreter, and how upgrades don't strand them*:

1. **[P0]** Each being's `.mcp.json` hardcodes a **version-stamped, user/OS-specific** interpreter —
   `C:\Users\jeffr\.agent-core\.venv-v0.7.0\Scripts\python.exe`, ×3 sidecars ×2 beings. A venv
   bump (`0.7.0`→`0.8.0`) renames the directory and **strands every being**; the path also embeds
   username+OS so it isn't portable to a second machine or account.
2. **[P1]** Daemon + all beings' sidecars share **ONE mutable venv** (`.venv-v0.7.0`) — no per-being
   pinning; upgrades are all-or-nothing across tenants; the in-place-upgrade `.pyd`-lock hazard from
   the 2026-07-13 incident.
3. **[P1]** Install/upgrade depends on a **bare `uv` on PATH** of whatever launches it — a headless
   service account without `uv` gets a raw `FileNotFoundError`.
4. **[P2]** No `daemon doctor`/GC to detect/remove superseded venvs or stale launchers; upgrades
   accrete dead trees (the 3-venv state).

## Root-cause insight

The version-stamp in the directory name (`.venv-v0.7.0`) is an **artifact of centralization**: the
venv lived in one shared location (`~/.agent-core/`), so versioning was how versions were told apart
there and how atomic swaps were done. **Once each being owns its venv inside its own home, the version
comes out of the directory name** — the path becomes permanently stable and per-being pinning is free.

## Decisions

**D1 — Per-being pinned venv (not a shared venv).** Each being gets its own interpreter; the resolver
maps being→venv. Wren can run 0.8.0 while Pepper stays 0.7.0. Closes item 1 **and** item 2 (all-or-
nothing upgrade + `.pyd`-lock).

**D2 — Stable per-being path, versioned swap target, atomic junction.**

```
~/.wren/.venv                              ← STABLE path, never changes across upgrades
      └─→ junction(Win) / symlink(POSIX) → ~/.wren/.agent-core/venvs/0.7.0/   ← real venv
~/.wren/.mcp.json   command = C:\Users\jeffr\.wren\.venv\Scripts\python.exe  -m agent_core_busproxy …
```

`.mcp.json` points at the stable `~/.<being>/.venv/Scripts/python.exe` (absolute, resolved at
generation time — Claude Code does not reliably expand `~`/env vars in an MCP `command`). It never
changes across upgrades. A Windows directory junction needs no admin; POSIX uses a symlink.

**D3 — Upgrade builds alongside, verifies, then atomically repoints.** Never destroy the working venv
until the replacement is proven. Build `~/.<being>/.agent-core/venvs/<new>/` while the current one is
live (no downtime for the slow install), verify the sidecars import, then repoint the junction
(instant). A failed build leaves the being fully working on the old venv. GC prunes superseded
versions later.

**D4 — Slim, sidecar-only venv, installed from PyPI.** A being's venv contains **only** the MCP
sidecar runtime — `agent-core-busproxy`, `agent-core-channel`, `agent-core-notify` and their light
deps (fastmcp, httpx, typer, anyio). **No torch / voice / GPU** — endpoints run in the *daemon*, not
in the being sidecar; the sidecars are thin proxies to the daemon's HTTP endpoint. Installed from
**PyPI** (depends on Cluster 1 C1-2 #310 publish pipeline), so a being venv is small and upgrades are
fast.

**D5 — `uv` resolved to an absolute path.** The builder resolves `uv` once (via a known install
location / `shutil.which` at build time, recorded), never assumes it is on the launching context's
PATH, and emits a clear actionable error if it cannot be found. Closes item 3.

**D6 — The daemon uses the same layout.** The daemon is shared infra with its **own** stable venv
(`~/.agent-core/.venv` → versioned) built by the same command. Cluster 2 owns the daemon's venv
*build*; Cluster 3 (portable supervisor / service definition) owns *how the daemon service is
launched* and consumes this path.

## Mechanisms (what this cluster builds)

### M1 — Venv builder / upgrader (tent-pole)
`agent-core venv build|upgrade <being|daemon>`:
- resolve `uv` to an absolute path (D5);
- create `~/.<target>/.agent-core/venvs/<version>/` and `uv pip install` the slim sidecar set from
  PyPI (D4);
- verify the sidecars import (`python -c "import agent_core_busproxy, agent_core_channel, agent_core_notify"`);
- atomically create/repoint the `~/.<target>/.venv` junction/symlink (D2, D3).

### M2 — `.mcp.json` canonical generator
Today `.mcp.json` is hand-written — which is *why* it drifted to a versioned absolute path. A
generator writes the canonical shape (stable `~/.<being>/.venv` interpreter path, all three sidecars,
correct `--agent`/`--daemon-url`) and a repair path for a drifted file. Regenerates Wren's and
Pepper's now (the migration). **Theme C's hatchery reuses this exact generator** — closing its
"never generates `.mcp.json`" gap by construction.

### M3 — `daemon doctor` / GC
`agent-core daemon doctor [--fix]`. Detects and (with `--fix`) prunes:
- superseded versioned venvs under each `~/.<being>/.agent-core/venvs/` (keep current + N-1 for
  rollback);
- broken junctions/symlinks (target missing);
- orphaned partial build dirs from a failed upgrade;
- drifted `.mcp.json` (re-runs M2's repair);
- the dead central corpses (`~/.agent-core/.venv-v0.7.0`, `~/.agent-core/.venv` 0.6.1).
Report-only by default; `--fix` acts.

## Ticket slate

| Ticket | P | Dep | Covers |
|---|---|---|---|
| **C2-1 · Per-being venv builder + uv resolution** | P0 | **blocked_by #310** (PyPI publish) | D1–D6, M1; the P1 bare-`uv` item |
| **C2-2 · `.mcp.json` canonical generator + regen Wren/Pepper** | P0 | blocked_by C2-1 | M2; the headline version-stamped-`.mcp.json` P0; migrates the two live beings |
| **C2-3 · `daemon doctor` / GC** | P2 | blocked_by C2-1 | M3; the doctor/GC P2; cleans the old shared-venv corpses |

## Dependencies & sequencing

- **C2-1 `blocked_by #310`** — the slim install pulls the sidecar packages from PyPI, so the publish
  pipeline must land first (Jeff, 2026-07-14). #310 is itself `blocked_by #309`, so the whole cluster
  sequences behind the Cluster 1 tent-pole.
- **C2-2, C2-3 `blocked_by C2-1`** — both operate on the layout C2-1 defines.
- The dependency-graph reconciler (#524) holds each ticket until its blocker is completed.

## Out of scope (named, deferred)

- The **daemon service definition** (how the daemon is launched/supervised) — Cluster 3 (#304–306).
- **Multi-tenant config/port isolation** and the **hatchery `.mcp.json` generation** wiring — Theme C
  (hatchery reuses M2's generator, but the hatch→run handoff is Theme C's ticket).
- Remaining mechanical Theme B items (macOS CI, published-docstring path leak) — foreman-able
  independently.
