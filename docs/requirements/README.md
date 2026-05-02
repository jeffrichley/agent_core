# Requirements (`docs/requirements/`)

Specs and runbooks for **Pepper / agent-core** behavior. Not every file applies to every task — use the **reading order** below so you do not miss linked context without reading the entire directory blindly.

---

## Who should read this first

| If you are… | Start here |
|-------------|--------------|
| Vale, Locke, or Folio on a cutover ticket | **[Session prompt →](pepper-cutover-prompts.md#implementer-vale-locke-or-folio)** then [`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md) → your ticket’s `.md` → follow **Related** / **Parent** links inside that ticket |
| Cadence (PR / merge) | **[Session prompt →](pepper-cutover-prompts.md#cadence-pr--merge-agent)** then playbook + [`pepper-cutover-cadence-queue.md`](pepper-cutover-cadence-queue.md) (Cadence runbook) |

---

## Reading order (cutover implementers)

1. **[`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md)** — roster, assignment table, PR-only comms with Cadence, Superpowers policy, worktree rules, **§3b already-implemented / partial work** (evidence matrix before big diffs).
2. **Your assigned ticket** — e.g. `pepper-cutover-02-handoff-observability.md` (source of truth for “done”).
3. **Every path linked from that ticket** — open the **Related**, **Parent**, and in-body links (`…md`, `…yaml`). Those links are how we tell you which other requirement files matter for *this* ticket.
4. **[`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md)** — epic context and child table (at least skim the section for your `#`).
5. **Optional discovery pass** — list this directory (`ls`, file tree, or IDE search) **once** when you join the workstream or when Jeff adds new specs, so new filenames do not surprise you. You still **do not** need to read unrelated Pepper docs cover-to-cover unless a ticket points you there.

**Rule of thumb:** If a `.md` is not linked from your ticket and is not in the **File index** below as “always relevant,” treat it as **out of scope** until Jeff or Cadence points you at it.

---

## When work might already be on `main` (summary)

Tickets can be **ahead of** or **behind** the code. Before large implementations, follow playbook **§3b**: map **“Done looks like”** to the repo, post an evidence matrix (met / partial / missing) on the PR, then either close gaps only or land a small **tests + docs + status** PR if everything is already satisfied. Full detail stays in the playbook so it does not drift in two places.

---

## File index (this folder)

| File | Role |
|------|------|
| [`README.md`](README.md) | This index — reading order and discovery |
| [`pepper-cutover-prompts.md`](pepper-cutover-prompts.md) | Copy-paste **session prompts** for implementers and Cadence |
| [`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md) | Cutover process, assignments, templates, §3b |
| [`pepper-cutover-cadence-queue.md`](pepper-cutover-cadence-queue.md) | Cadence runbook (PR-only); legacy filename |
| [`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md) | Epic parent + cutover gate |
| [`pepper-cutover-01-identity-fidelity.md`](pepper-cutover-01-identity-fidelity.md) … [`08`](pepper-cutover-08-notification-surface.md) | Numbered cutover tickets |
| [`pepper-handoff-daemon-contract.md`](pepper-handoff-daemon-contract.md) | Handoff daemon shape (pairs with #02) |
| [`pepper-requirements.md`](pepper-requirements.md) | Original hook / tool expectations |
| [`pepper-identity-injection-size-limit.md`](pepper-identity-injection-size-limit.md) | Identity size / truncation (#01 context) |
| [`pepper-handoff-writer-bugfix.md`](pepper-handoff-writer-bugfix.md) | Older handoff notes (#02 context) |
| [`pepper-email-cli.md`](pepper-email-cli.md) | Pepper email CLI (not cutover-core unless assigned) |
| [`pepper-ios-watch-vision.md`](pepper-ios-watch-vision.md) | iOS / watch vision (not cutover-core unless assigned) |

---

## Outside this directory (often linked from tickets)

- [`docs/examples/pepper-agent-core.yaml`](../examples/pepper-agent-core.yaml) — example hook pipeline
- [`docs/ROADMAP.md`](../ROADMAP.md) — roadmap cross-links
