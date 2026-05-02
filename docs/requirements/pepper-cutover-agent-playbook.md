# Pepper cutover — agent playbook (multi-agent + PR owner)

**Purpose:** Tell any coding agent **how** to work on Pepper cutover tickets, **which specs exist**, and **how to record what it picked up**. Default **Assignee** names are filled below; Jeff (or **Cadence**) may reassign.

**People model (named roster):**

- **Vale**, **Locke**, **Folio** — implementers (code, tests, PR-ready branches). They do **not** decide global merge order and do **not** merge to `main` unless Jeff explicitly overrides this playbook.
- **Cadence** — PR / integration agent: **opening/updating PRs**, **merge sequencing**, **retargeting stacked PRs**, **conflict triage**, **CI**. Implementers hand off branches; Cadence lands them.

Assume **other implementers are touching the same repo**. Prefer small, reviewable diffs and communicate overlap early.

**This playbook is on `main`.** Implementers always **`git pull` on `main`** then branch for their ticket. The old `feat/docs-pepper-cutover-agent-playbook` branch was only the vehicle to land this file — do not treat it as a shared development line.

---

## Who picks the ticket? (Agents do not “just know”)

- **Default:** an implementer only starts work when **their name** is in **Assignee** for that row and they move **Status** to `Claimed` when they begin.
- **Exception:** Jeff (or **Cadence**) explicitly reassigns in chat — update the table to match.
- The **dependency diagrams** below are for **merge sequencing and risk**, not for guessing ownership. If **your name** is not on any row you intend to work, **stop and ask** Jeff (or Cadence) to assign before coding.

---

## Superpowers skills (**required** when the plugin is available)

**Policy:** If your environment has the **Superpowers** plugin, you **must** use it as the primary operating loop — not optional polish.

1. **Start of session / substantive work:** read and follow **`using-superpowers`** (Skill tool / skill read) before writing code or “exploring.”
2. **Multi-step or ambiguous work:** run **`writing-plans`** before large diffs.
3. **Behavior change or bugfix:** prefer **`test-driven-development`**; use **`systematic-debugging`** for failures before speculative fixes.
4. **Before claiming done or asking the PR agent to merge:** run **`verification-before-completion`** (evidence: commands + outcome).
5. **Large or risky change before merge:** **`requesting-code-review`** (or equivalent host review).
6. **Branch is green, need integration / merge guidance:** **`finishing-a-development-branch`**.

| When | Skill (typical slug / folder name) |
|------|-------------------------------------|
| Session start / any real implementation | `using-superpowers` |
| Multi-step / unclear scope | `writing-plans` |
| Behavior change or bugfix | `test-driven-development` + `systematic-debugging` |
| Before “done” / merge handoff | `verification-before-completion` |
| Pre-merge confidence | `requesting-code-review` |
| After CI green, integration choices | `finishing-a-development-branch` |

If Superpowers is **not** installed in your host, still mirror the **same discipline** (plan → test-first when appropriate → debug with evidence → verify before done).

---

## Non-negotiable workflow — one ticket, one worktree, one branch

Each implementer works **one active ticket at a time** from a **dedicated git worktree** so parallel agents do not stomp the same working tree.

### 1) Pick your ticket from the assignment table below

Only work on a row that lists **your roster name** (`Vale` / `Locke` / `Folio`) in **Assignee**. Keep **at most one** row in `Claimed` or `PR open` per implementer at a time unless Jeff says otherwise.

### 2) Create a worktree + branch from current `main`

Naming convention:

- **Branch:** `feat/cutover-NN-short-slug` (match the ticket number `NN`).
- **Worktree folder:** `.worktrees/cutover-NN-short-slug` under the repo root (keeps them grouped and out of the way).

**PowerShell (Windows) example** — run from the repo root `agent_core/` (not inside another worktree unless you mean to):

```powershell
cd E:\workspaces\ai\agents\agent_core
git fetch origin
git switch main
git pull origin main

$slug = "cutover-02-handoff-observability"   # change per ticket
git worktree add .worktrees\$slug -b feat/cutover-02-handoff-observability origin/main
cd .worktrees\$slug
```

Then install deps if needed (`uv sync` in `packages/core`, etc. — follow existing repo docs).

### 3) Implement against the ticket spec

- Read the **ticket spec** linked in the table (source of truth for “done”).
- Read **parent / related** docs linked from that ticket (especially the pre-cutover epic and daemon contract when touching handoff).

### 4) Commit and push; hand off to **Cadence**

- **Commit messages:** imperative mood, scoped prefix, explain *why* when non-obvious.
- **Push** your `feat/cutover-NN-...` branch to `origin`.
- **Do not merge** unless Jeff explicitly overrides this playbook.
- **Signal Cadence (required):** append a row to **[`pepper-cutover-cadence-queue.md`](pepper-cutover-cadence-queue.md)** under **Open signals** (same PR as your change is best). Set playbook **Status** to `PR open` and put the PR URL in **Notes** once Cadence has opened the PR (Cadence can fill that).
- Paste the **Handoff template** (below) to Cadence in chat if Jeff uses a side channel.

### 5) After your PR merges

- Mark the ticket row **Merged** (or clear Assignee) in the table below.
- Remove or archive the worktree when idle:

```powershell
cd E:\workspaces\ai\agents\agent_core
git worktree remove .worktrees\cutover-02-handoff-observability
git branch -d feat/cutover-02-handoff-observability   # optional local cleanup
```

---

## Coordination rules (keep three agents + one PR agent coherent)

1. **One ticket → one branch → one PR** unless two tickets are truly inseparable. If they depend on each other, use a **stack** (child branch based on parent branch) and tell the PR agent explicitly.
2. **Do not share branches** between implementers.
3. **Partition by area** when you can (Discord vs hooks vs daily pipeline) to reduce merge conflicts.
4. If two tickets **must** touch the same hot files, **serialize** (one assignee / one PR at a time) instead of parallelizing.
5. **PR agent owns merge order** for dependencies (e.g. notification surface vs handoff observability). Implementers should not “guess” landing order beyond what the specs say.
6. **Conflict policy:** whoever is on the **child** branch in a stack usually rebases onto the updated parent after the parent merges; PR agent coordinates if unclear.

---

## Dependency diagrams (merge / stack hints — **not** for self-assigning)

Use these with the **PR agent** to decide **landing order** or **stacked PR bases**. They do **not** replace the **Assignment table** for who works what.

### Pepper cutover specs (#01–#08 + epic)

```mermaid
flowchart TB
  EP["Epic: pre-cutover must-haves"]
  EP --> C01["01 Identity fidelity"]
  EP --> C02["02 Handoff observability"]
  EP --> C03["03 Discord verb parity"]
  EP --> C04["04 Daily JSONL pipeline"]
  EP --> C05["05 Skills discovery"]
  EP --> C06["06 Vault continuity"]
  EP --> C07["07 Hook fidelity"]
  EP --> C08["08 Notification surface"]
  DC["Handoff daemon contract"]
  DC --> C02
  C08 -.->|"partially gates done: visible ready signal"| C02
  C04 -.->|"summaries / JSONL feeds skills + context"| C05
  C07 -.->|"SessionStart identity wiring"| C01
  C07 -.->|"PreCompact / SessionEnd handoff wiring"| C02
```

- **Solid `EP -->`:** epic child ticket (all must close for cutover gate — see epic doc).
- **Solid `DC -->`:** daemon contract is the handoff implementation shape for **#02**.
- **Dashed `-.->`:** integration / sequencing coupling (land or scope the tail first when possible).

### GitHub issues (`jeffrichley/agent_core`)

Issues change; refresh with:

`gh issue list --repo jeffrichley/agent_core --state open`

Snapshot **2026-05-02** (edges = product / sequencing intuition, not GitHub “blocked by” metadata):

```mermaid
flowchart TB
  subgraph discord["Discord bridge"]
    i22["#22 OPEN partial-send / rate-limit"]
    i23["#23 OPEN ack + chunk-limit semantics"]
    i13["#13 OPEN typing TTL / placeholder"]
  end
  i23 --> i22
  i20["#20 CLOSED auto-split >2000"]
  i21["#21 CLOSED markdown-safe chunking"]
  i20 --> i22
  i21 --> i22
  subgraph bus["Bus"]
    i15["#15 OPEN heartbeats"]
    i16["#16 OPEN read-only bus tail"]
    i17["#17 OPEN DLQ / retry"]
    i18["#18 OPEN expires_at enforcement"]
    i19["#19 OPEN typed command envelopes"]
  end
  i18 -.-> i17
  subgraph wakes["Wakes / notify"]
    i14["#14 OPEN burst coalesce wakes"]
    i12["#12 CLOSED auto-ack routine"]
  end
  i14 -.->|"coordination note in specs"| i12
```

**Cutover ↔ GitHub (theme only):** Cutover **#03** aligns with **Discord** issues; **#04 / #08** align with **Bus** + **notify** work; **#02 / #08** align with **Wakes**. There is **no strict 1:1** between cutover doc numbers and GitHub issue numbers — use both tables plus Jeff’s assignment.

---

## Ticket index — all specs in this workstream

### Epic (parent)

| Id | Spec |
|----|------|
| Pre-cutover epic | [`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md) — child table + cutover gate |

### Cutover tickets (the numbered queue)

| # | Spec |
|---|------|
| 01 | [`pepper-cutover-01-identity-fidelity.md`](pepper-cutover-01-identity-fidelity.md) |
| 02 | [`pepper-cutover-02-handoff-observability.md`](pepper-cutover-02-handoff-observability.md) |
| 03 | [`pepper-cutover-03-discord-verb-parity.md`](pepper-cutover-03-discord-verb-parity.md) |
| 04 | [`pepper-cutover-04-daily-jsonl-pipeline.md`](pepper-cutover-04-daily-jsonl-pipeline.md) |
| 05 | [`pepper-cutover-05-skills-discovery.md`](pepper-cutover-05-skills-discovery.md) |
| 06 | [`pepper-cutover-06-vault-continuity.md`](pepper-cutover-06-vault-continuity.md) |
| 07 | [`pepper-cutover-07-hook-fidelity.md`](pepper-cutover-07-hook-fidelity.md) |
| 08 | [`pepper-cutover-08-notification-surface.md`](pepper-cutover-08-notification-surface.md) |

### Supporting / adjacent specs (read when relevant)

| Spec | When to read |
|------|----------------|
| [`pepper-handoff-daemon-contract.md`](pepper-handoff-daemon-contract.md) | Handoff bus, daemon writer, hook-minimal enqueue — **tight coupling to #02** |
| [`pepper-requirements.md`](pepper-requirements.md) | Original hook + tool expectations |
| [`pepper-identity-injection-size-limit.md`](pepper-identity-injection-size-limit.md) | Identity truncation / injection limits — **#01** |
| [`pepper-handoff-writer-bugfix.md`](pepper-handoff-writer-bugfix.md) | Predecessor notes for handoff — **#02** |
| [`docs/examples/pepper-agent-core.yaml`](../examples/pepper-agent-core.yaml) | Example pipeline wiring — **#07**, parts of **#01** |
| [`docs/ROADMAP.md`](../ROADMAP.md) | Discord / skills / pipeline roadmap references |
| [`pepper-cutover-cadence-queue.md`](pepper-cutover-cadence-queue.md) | **Cadence** polling + implementer handoff signals |

**Dependency hints (not a substitute for reading specs):** #08 partially gates #02 (“ready” must be visible). #04 relates to #05 (summaries → skills). #07 references #01/#02 for content vs firing.

---

## Assignment table — default owners (Jeff may reassign)

**Roster:** Vale, Locke, Folio (implementers) · **Cadence** (PR / merge).  
**Statuses:** `Unassigned` · `Claimed` · `PR open` · `Merged` · `Blocked`

| Ticket | Spec | Suggested branch | Assignee | Status | Notes |
|--------|------|------------------|----------|--------|-------|
| Pre-cutover epic | [`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md) | _(n/a — tracking doc)_ | **Cadence** | | Cadence keeps epic child statuses accurate |
| 01 | [`pepper-cutover-01-identity-fidelity.md`](pepper-cutover-01-identity-fidelity.md) | `feat/cutover-01-identity-fidelity` | **Vale** | Unassigned | |
| 02 | [`pepper-cutover-02-handoff-observability.md`](pepper-cutover-02-handoff-observability.md) | `feat/cutover-02-handoff-observability` | **Locke** | Unassigned | Pair with daemon row; coordinate **#08** with Folio |
| 03 | [`pepper-cutover-03-discord-verb-parity.md`](pepper-cutover-03-discord-verb-parity.md) | `feat/cutover-03-discord-verb-parity` | **Folio** | Unassigned | |
| 04 | [`pepper-cutover-04-daily-jsonl-pipeline.md`](pepper-cutover-04-daily-jsonl-pipeline.md) | `feat/cutover-04-daily-jsonl-pipeline` | **Vale** | Unassigned | |
| 05 | [`pepper-cutover-05-skills-discovery.md`](pepper-cutover-05-skills-discovery.md) | `feat/cutover-05-skills-discovery` | **Locke** | Unassigned | |
| 06 | [`pepper-cutover-06-vault-continuity.md`](pepper-cutover-06-vault-continuity.md) | `feat/cutover-06-vault-continuity` | **Folio** | Unassigned | |
| 07 | [`pepper-cutover-07-hook-fidelity.md`](pepper-cutover-07-hook-fidelity.md) | `feat/cutover-07-hook-fidelity` | **Vale** | Unassigned | Touches wiring for #01 / #02 |
| 08 | [`pepper-cutover-08-notification-surface.md`](pepper-cutover-08-notification-surface.md) | `feat/cutover-08-notification-surface` | **Folio** | Unassigned | Partially gates **#02** “done” path |
| Daemon contract | [`pepper-handoff-daemon-contract.md`](pepper-handoff-daemon-contract.md) | _(same branch as #02 unless split)_ | **Locke** | Unassigned | Same workstream as **#02** |

**Rules:** Each implementer keeps **one** row in `Claimed` or `PR open` at a time unless Jeff expands WIP. **Cadence** polls **[`pepper-cutover-cadence-queue.md`](pepper-cutover-cadence-queue.md)** and open `feat/cutover-*` PRs (see that file for `gh` examples).

---

## Cadence — PR agent checklist (**Cadence**)

1. Poll **[`pepper-cutover-cadence-queue.md`](pepper-cutover-cadence-queue.md)** (`git pull`) and **`gh pr list`** for `feat/cutover-*` (see queue doc for a filter example).
2. Confirm each PR has: ticket id, what changed, how to verify, and declared dependencies.
3. Choose **merge vs stack** for dependent branches; retarget GitHub PR bases as needed.
4. Land **parent-first** in a stack; ping implementer for rebase after each merge.
5. After merge: move the implementer’s signal row to **Resolved** in the queue file; set playbook **Status** to `Merged` and clear or rotate **Assignee** per Jeff.
6. Keep the **epic table** in [`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md) accurate when a child truly ships (per that doc’s intent).

---

## Handoff template (paste into PR description or chat to **Cadence**)

```text
To: Cadence
From: Vale | Locke | Folio
Ticket: Cutover #NN
Branch: feat/cutover-NN-slug
Worktree: .worktrees/cutover-NN-slug
Depends on: (none | branch name / PR link)
Risk: (low / medium / high)
Verified: (commands you ran, e.g. uv run pytest packages/core/tests/...)
Cadence: (open PR | merge when green | retarget stack | other)
Follow-ups: (optional)
```

This file lives next to the specs: [`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md).
