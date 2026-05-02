# Pepper cutover — Cadence runbook (PR comments only)

**Cadence** = PR / merge agent. **Vale**, **Locke**, **Folio** = implementers.

There are **no side channels** (no separate chat thread for cutover handoffs). The only supported surfaces are:

1. **GitHub PR comments** on the relevant `feat/cutover-*` PR (**default — use this**).
2. **A file in the repo** (only if PR discussion is impossible, e.g. no PR exists yet — rare; prefer opening a draft PR and commenting there).

Day-to-day comms for this workstream stay **in the PR**: implementers post the **handoff** template as a comment; Cadence posts **merge / fail / rework** using the templates in [`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md).

**Finding requirement docs:** see [`README.md`](README.md) (reading order + directory index).

---

## How Cadence polls

1. **Open PRs:** `gh pr list` filtered to `feat/cutover-*` (see playbook for an example `jq` filter).
2. **PR conversation:** read **comments and review threads** on those PRs — that is the inbox.
3. **Playbook** [`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md): **Notes** / **Status** when Cadence updates the ledger after merge (via a small follow-up PR to `main`, or combined with another doc PR Jeff approves).

Cadence does **not** maintain a separate queue table in this file.

---

## Implementers (Vale / Locke / Folio)

- When you need Cadence: open the PR (or draft), then post **one PR comment** using the **Implementer → Cadence handoff** block from the playbook (same text every time).
- **After you think a ticket is “done”:** before you start the next ticket, **re-open every open PR you still have** under `feat/cutover-*` and read **all** comments from Cadence (reviews, change requests, merge notes). Rework lives in the same PR until Cadence merges or explicitly hands it back.

---

## Cadence

- Reply on the PR: **approve / merge**, **request changes** (use the **Cadence → implementer rework** template from the playbook), or **comment** with stack / CI instructions.
- After merge: update playbook assignment **Status** / **Notes** when you touch the ledger (per playbook checklist).
