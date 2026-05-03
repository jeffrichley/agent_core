# Requirements (`docs/requirements/`)

Specs and runbooks for **Pepper / agent-core** behavior. Not every file applies to every task — use the **reading order** below so you do not miss linked context without reading the entire directory blindly.

---

## Reading order (cutover work)

1. **[`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md)** — per-ticket flow, Superpowers skill mapping, **§3b already-implemented / partial work** guidance, dependency diagrams, per-ticket status table.
2. **The ticket spec you are working** — e.g. `pepper-cutover-02-handoff-observability.md` (source of truth for "done").
3. **Every path linked from that ticket** — `Related` / `Parent` frontmatter and any in-body `…md` / `…yaml` links. Those links are how each ticket says "read this before you implement."
4. **[`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md)** — epic context, gate criteria. At least skim the section for the ticket you are working.
5. **Optional discovery pass** — list this directory once when starting fresh, so new filenames are visible. Skip files not linked from the ticket and not in the **File index** below unless they are clearly relevant.

**Rule of thumb:** if a `.md` is not linked from your ticket and is not in the File index as "always relevant," treat it as **out of scope** until something points at it.

---

## When work might already be on `main`

Tickets can be ahead of, behind, or wrong-shape vs. their spec. Before any large diff, follow playbook **§3b**: map "Done looks like" to the repo, score each acceptance bullet **met / partial / missing** with evidence, then either close gaps or land a small tests + docs + status update if everything already satisfies the spec.

---

## Test playbooks

End-of-cutover verification lives in [`docs/cutover/test-playbooks/`](../cutover/test-playbooks/), one file per ticket. Verification is **deferred** — the playbooks are written as each ticket's implementation lands and run as a batch when the epic is complete.

---

## File index (this folder)

| File | Role |
|------|------|
| [`README.md`](README.md) | This index — reading order and discovery |
| [`pepper-cutover-agent-playbook.md`](pepper-cutover-agent-playbook.md) | Cutover process, status table, §3b |
| [`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md) | Epic parent + cutover gate |
| [`pepper-cutover-01-identity-fidelity.md`](pepper-cutover-01-identity-fidelity.md) … [`08`](pepper-cutover-08-notification-surface.md) | Numbered cutover tickets |
| [`pepper-handoff-daemon-contract.md`](pepper-handoff-daemon-contract.md) | Handoff daemon shape (pairs with #02) |
| [`pepper-requirements.md`](pepper-requirements.md) | Original hook / tool expectations |
| [`pepper-identity-injection-size-limit.md`](pepper-identity-injection-size-limit.md) | Identity size / truncation (#01 context) |
| [`pepper-handoff-writer-bugfix.md`](pepper-handoff-writer-bugfix.md) | Older handoff notes (#02 context) |
| [`pepper-email-cli.md`](pepper-email-cli.md) | Pepper email CLI (not cutover-core unless explicitly in scope) |
| [`pepper-ios-watch-vision.md`](pepper-ios-watch-vision.md) | iOS / watch vision (not cutover-core unless explicitly in scope) |

---

## Outside this directory (often linked from tickets)

- [`docs/cutover/test-playbooks/`](../cutover/test-playbooks/) — end-of-cutover verification entries
- [`docs/examples/pepper-agent-core.yaml`](../examples/pepper-agent-core.yaml) — example hook pipeline
- [`docs/ROADMAP.md`](../ROADMAP.md) — roadmap cross-links
