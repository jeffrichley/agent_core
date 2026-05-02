# Cadence queue — handoff signals for the PR agent

**PR agent:** **Cadence** (opens PRs, merge order, stacked bases, CI triage).  
**Implementers:** **Vale**, **Locke**, **Folio** (code + tests; do not self-merge to `main`).

This file is the **pollable inbox** on `main`: Cadence runs `git pull` and reads **Open signals**. Implementers append a row when they need Cadence (see below).

---

## How Cadence polls

Use any combination:

1. **Open PRs (primary):** branches matching `feat/cutover-*` (and related stacks):

   ```bash
   gh pr list --repo jeffrichley/agent_core --state open --json number,title,headRefName,url \
     --jq '.[] | select(.headRefName|test("cutover")) | "\(.number)\t\(.headRefName)\t\(.title)\t\(.url)"'
   ```

2. **This file:** after `git pull origin main`, read **Open signals** — newest rows first.

3. **Playbook assignment table** in [`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md): **Notes** column when it contains `PR #…` or a GitHub PR URL (Cadence fills that in when the PR exists).

---

## Open signals (append new rows at the top)

Implementer: when your branch is **pushed** and you need Cadence (open PR, retarget stack, merge after green CI, etc.), **add one row** in the same PR as your work (preferred: last commit before you hand off), or open a tiny docs-only PR that only appends here.

| ISO UTC | Agent | Cutover | Branch | Need from Cadence |
|---------|-------|---------|--------|-------------------|
| _(none yet)_ | | | | |

**Row contract:** `Need from Cadence` is one of: `open PR`, `merge when green`, `retarget stack to main`, `resolve conflict with …`, `draft ready for review`, etc.

---

## Resolved (Cadence moves rows here after handling)

| ISO UTC | Agent | Cutover | Branch / PR | Outcome |
|---------|-------|---------|-------------|---------|
| _(none yet)_ | | | | |
