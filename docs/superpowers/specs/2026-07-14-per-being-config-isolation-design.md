# Per-being config isolation (Theme C, Cluster α) — design

**Date:** 2026-07-14
**Epic:** #262. **Theme:** C — Multi-tenancy & hatchery (#266), **Cluster α**.
**Status:** approved (Jeff, 2026-07-14). Ready to slice into tickets.

## Reframe (load-bearing)

Theme C's `[P0][L]` reads as "split the monolithic daemon." But two prior decisions shrink it
dramatically:

1. **One bus, not one-per-being** (Jeff, Cluster 3): the architecture is a single user-level bus
   service hosting all beings. Multi-tenancy here is **per-being CONFIG + FAILURE isolation within
   that one bus**, NOT process-per-being.
2. **Runtime failure isolation already exists** (Theme A): the `EndpointSupervisor` + degraded boot
   (#273, merged) already stop one endpoint's runtime exception from taking down the others.

So Cluster α is the **config-side** of isolation: make each being's config independently declared and
independently validated/quarantined, so a bad *config* entry can't crash boot for every tenant — the
mirror of Theme A's runtime degraded-boot.

## Current state (ground truth, 2026-07-14)

- Conf.d merge primitive **already exists** in `bus/runner.py:47-64`: `endpoints.d/*.yaml` fragments
  contribute their `endpoints:` list to the merged set; fragments may not override
  `bus`/`http`/`bus_hooks`/`mcp_audit`.
- But config parsing is **raw `dict.get()`** throughout `runner.py`; the `validate_config` hookspec
  (called at `runner.py:66`) is a **no-op**. A typo'd param crashes boot for all tenants.
- **Mixed reality:** `endpoints.d/wren.yaml` holds Wren's 4 endpoints; **Pepper's 4 endpoints are
  inline in the monolith** (`pepper`, `briefs.pepper`, `discord-pepper`, `webcam-pepper`). System
  endpoints (`scheduler`, `handoff-jobs`, `stub`) + test ones are also inline.
- A **bad fragment currently raises `BusBootError`** → kills boot for everyone (`runner.py:59-63`).
- **Drift debris** sits in `endpoints.d/`: `wren.yaml.bak-20260702-with-voice`,
  `testbeing.yaml.cleanup-2026-05-10`, plus `agent_core-cputest.yaml` monolith copy.

## Decisions

**D1 — Config boundary (Jeff's call):** beings → per-being `endpoints.d/<being>.yaml`; the monolith
keeps infra (`bus`/`http`/`bus_hooks`/`mcp_audit`) **and** system/shared endpoints (`scheduler`,
`handoff-jobs`, `stub`, test ones). Only being-scoped endpoints move to fragments. (`inbound`, a
system endpoint currently living in `wren.yaml`, is left as-is — a minor known wart, not worth
churning under this decision.)

**D2 — Pydantic daemon-config schema, in `core`.** Replace the raw `dict.get()` parsing with a
validated pydantic model (`extra="forbid"`), and make the `validate_config` hookspec real. Lives in
`core` and is importable by the hatchery so generation is correct-by-construction (sets up Cluster β,
kills the "hatchery reuses no core schemas" `[P1]`).

**D3 — Degraded / quarantine fragment load.** Each fragment and each endpoint entry is validated
against the schema; a bad *entry* is logged and skipped, the rest boot; a syntactically-broken YAML
fragment quarantines just *that fragment*. This is the config-side mirror of Theme A's degraded boot
(which handles endpoint *start* failures; this handles *parse/validate* failures). Closes the
`[P0][L]` "a bad entry drops every being."

**D4 — Config hygiene folds into `daemon doctor` (#317, Cluster C2-3).** Detect/prune the
`.bak`/`.pre-*`/`.cleanup` debris, record the venv path in the install stamp, and flag config drift
— added to the existing `daemon doctor` command rather than a new one (one doctor for venv **and**
config hygiene).

## Ticket slate

| Ticket | P | Dep | Covers |
|---|---|---|---|
| **Cα-1 · Pydantic daemon-config schema + real `validate_config`** | P1 | none (foundation) | D2; the config-validation `[P1]`; shared schema for Cluster β |
| **Cα-2 · Per-being fragment isolation + degraded load + migrate Pepper** | P0 | blocked_by Cα-1 | D1, D3; the `[P0][L]` monolith/isolation + mixed-mechanism `[P1]` |
| **Cα-3 · Config hygiene/drift → extend `daemon doctor`** | P2 | blocked_by Cα-1 (extends #317) | D4; the drift-debris `[P2]` |

## Dependencies & sequencing

- **Cα-1** is a pure-`core` schema refactor — **no external dependency**, so this whole cluster can
  proceed independently of the Cluster 1/2 PyPI chain (#309/#310). Parallelizable fleet work.
- **Cα-2, Cα-3** `blocked_by Cα-1` (both operate on the schema/fragment shape it defines).
- Cα-3 extends the `daemon doctor` from C2-3 (#317).

## Out of scope (named, deferred)

- **Hatchery correctness** — generate `.mcp.json` (reuse #316), hatch→run handoff, secrets→keyring,
  reuse core schemas at generation time — Cluster **β** (next Theme C cluster; depends on Cα-1's
  schema + #316's generator).
- The `[P0]` **scheduled-task-drift** item — folds into Theme B **Cluster 3** (#306, the Windows
  headless service; same `AgentCoreDaemon` task).
- **Per-being process isolation** — explicitly NOT pursued (one-bus decision).
