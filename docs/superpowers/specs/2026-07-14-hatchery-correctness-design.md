# Hatchery correctness (Theme C, Cluster β) — design

**Date:** 2026-07-14
**Epic:** #262. **Theme:** C — Multi-tenancy & hatchery (#266), **Cluster β**.
**Status:** approved (Jeff, 2026-07-14). Ready to slice into tickets.

## Reframe (load-bearing)

The hatchery is **not** a rebuild target. `packages/agent-core-hatchery` is already a mature,
transactional package: a pydantic `HatchConfig` contract (`extra="forbid"`), `StrictUndefined`
jinja rendering, a `Hatcher` orchestrator with full write-tracking + rollback, a
`DaemonConfigWriter` that emits `endpoints.d/<being>.yaml` + `jobs.d/<being>.yaml` fragments, and
13 test files. These are strengths to preserve.

Cluster β closes the four correctness gaps that stand between "hatch succeeded" and a being that is
**live, wired, and secure** without manual finishing. Two of the four are **not** gated on the
Theme B PyPI chain and ship in parallel; two ride behind it.

## Current state (ground truth, 2026-07-14)

- **No `.mcp.json` is generated anywhere.** `hatcher.py` renders only `agent_core.yaml`,
  `.claude/settings.json`, and `CLAUDE.md`. The one file that wires a being's MCP sidecars to the
  bus is left to a hand-copied doc snippet. No `.mcp.json` generator exists in the repo today.
- **Secrets are written plaintext.** `channels/discord.py` writes the Discord bot token to
  `~/.agent-core/discord-<being>.env` as `TOKEN=<literal>`; the `env_file.chmod(0o600)` is guarded
  by `if os.name != "nt"`, so on Windows the file lands **world-readable**. No keyring usage exists
  anywhere in the repo.
- **No hatch→run handoff.** The report records `daemon_check = "skipped"`. Fragments are written
  but the daemon is never reloaded or verified, so a "successful" hatch is not live until a manual
  daemon bounce + a human `cd ~/.<being> && claude`.
- **Hatchery reuses none of core's config schemas.** The daemon fragments are raw jinja → YAML; the
  hatchery's own pydantic models (`HatchConfig` et al.) are hatchery-only. Generator and consumer
  can drift silently.

## Decisions

**D1 — `.mcp.json` generation reuses C2-2's generator (#316), and the hatcher builds the being's
venv.** The hatcher invokes the canonical `.mcp.json` generator (M2, built in Theme B C2-2 #316) as
a config-tree step, writing `~/.<being>/.mcp.json` with the stable `~/.<being>/.venv` interpreter
path, all three sidecars, and the correct `--agent`/`--daemon-url`. Because that interpreter path
must *exist* for the file to work, the hatcher also builds the being's slim sidecar venv via C2-1's
builder (#315, `agent-core venv build <being>`). Both writes are tracked for transactional rollback
like every other hatch step. Closes the `[P1][S]` hand-copied-snippet gap by construction.

**D2 — Portable owner-only secret perms now; keyring migration → Theme D (Jeff's call).** Build a
cross-platform `set_owner_only(path)` helper **in core** — Unix `chmod 0600`; Windows a real
owner-only ACL (`icacls`/DPAPI), replacing the current silent no-op. The hatchery's token write
consumes it so `discord-<being>.env` is actually protected on **every** OS today. The keyring/DPAPI
*migration* — moving this token **and** Theme D's two `[P0]` secret items (vault master password,
subprocess-inherited secrets) off plaintext-on-disk — is explicitly **Theme D's** ticket, and it
reuses this same `set_owner_only` primitive. No dependency on undesigned work; no throwaway keyring;
mirrors the Cluster α tight-scope discipline. Covers the correctness half of the `[P1][M]` secrets
item.

**D3 — Reuse core's daemon-config schema at generation time (validate-after-render).** The hatcher
validates its rendered `endpoints.d/<being>.yaml` against Cluster α's pydantic daemon-config schema
(#319) **before** writing it — a fragment that would not parse/validate fails the hatch
transactionally (rollback) instead of silently landing something the daemon later rejects. Chosen
over a full construct-from-model rewrite: the jinja templates carry comments and structure worth
keeping as the authoring surface, and a validation gate closes the generator/consumer-drift gap with
far less churn. Makes generation correct-by-construction. Covers the `[P2][M]` schema-reuse item.

**D4 — Real hatch→run handoff (reload + verify live).** Replace `daemon_check = "skipped"`: after
fragments are written, the hatcher reloads the daemon and health-probes the new being's endpoint
against the bus HTTP host, turning the report's daemon-check into a real green/red that **fails
loudly** if the being is not live. Explicitly **not** auto-spawning a Claude Code session — the being
is woken via the wake channel; auto-run is brittle and out of scope. Covers the `[P2][S]`
no-handoff item.

## Ticket slate

| Ticket | P | Dep | Covers |
|---|---|---|---|
| **Cβ-1 · Portable owner-only secret perms** (`set_owner_only` in `core` + hatchery consumes it) | P1 | none (foundation; also hands Theme D its perms primitive) | D2; correctness half of the `[P1][M]` secrets item |
| **Cβ-2 · Reuse core daemon-config schema at generation** (validate rendered fragment against #319) | P2 | blocked_by #319 | D3; the `[P2][M]` schema-reuse item |
| **Cβ-3 · Hatch→run: venv build + `.mcp.json` generation + reload/verify live** | P1 | blocked_by #315 **and** #316 | D1, D4; the `[P1][S]` `.mcp.json` item **and** the `[P2][S]` no-handoff item |

## Dependencies & sequencing

- **Cβ-1** has **no dependency** — pure `core` helper + a one-line hatchery consumer. Ships
  immediately, in parallel with everything. It additionally unblocks Theme D's `[P0][S]`
  vault-password-perms fix (same helper).
- **Cβ-2 `blocked_by #319`** — needs Cluster α's daemon-config schema to validate against. **Not**
  PyPI-gated, so it unblocks the moment #319 lands.
- **Cβ-3 `blocked_by #315 + #316`** — needs the venv builder (C2-1) so the interpreter path resolves
  and the `.mcp.json` generator (C2-2) to write the file. Both sit behind the Theme B
  #309→#310→#315→#316 chain, so Cβ-3 rides the PyPI tent-pole.
- The dependency-graph reconciler (#524) holds each ticket until its blocker(s) complete.

## Out of scope (named, deferred)

- **Secrets → OS keyring/DPAPI migration** (this token + Theme D's two `[P0]` secret items) —
  **Theme D** (#267), which owns the security primitive and reuses Cβ-1's `set_owner_only`.
- **Auto-running the being** (spawning a Claude Code session from hatch) — out of scope; the being
  is woken via the wake channel.
- **Constructing daemon fragments from the pydantic model** (vs. validate-after-render) — deferred;
  D3's validation gate closes the drift gap without the rewrite.
- **`jobs.d/<being>.yaml` schema validation** — #319's schema covers the endpoints fragment shape;
  scheduler-job-fragment validation is a separate shape, not in this cluster.
