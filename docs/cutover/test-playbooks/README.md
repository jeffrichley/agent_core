# Cutover test playbooks

One file per cutover ticket. Each playbook is **deferred verification** — it
describes what we need to test at the end of the cutover, not something to run
incrementally as each ticket lands.

## How this works

- After implementing a cutover item, drop a playbook here named
  `NN-short-slug.md` (matching the ticket id).
- The playbook captures: what the implementation is, the acceptance criteria
  from the spec, and the concrete verification steps to run **once the whole
  epic is done**.
- When all eight tickets are implemented and we're ready to flip Pepper's
  runtime to agent-core, run every playbook in this directory in numeric
  order. Cutover gate = every playbook passes.

## Index

| # | Playbook | Status |
|---|----------|--------|
| 01 | [Identity fidelity](01-identity-fidelity.md) | Implementation complete; verification pending end-of-cutover run |
| 02 | [Handoff observability](02-handoff-observability.md) | Implementation complete; verification pending end-of-cutover run (mid-session perception step now backed by #08 evidence) |
| 08 | [Notification surface](08-notification-surface.md) | Implementation complete; verification pending end-of-cutover run. Closes the perception side of #02 scenario (b). |

## Scope of "verification"

Each playbook covers two layers:
1. **Spec acceptance** — what the cutover ticket spec calls "Done looks like."
2. **Implementation-specific checks** — anything in how the code was built
   that needs explicit verification (e.g. a strict validator we added, a
   refactor that moved logic, a CLI restructure).

## Cutover gate criteria (per the epic doc)

All eight tickets' playbooks pass. **#01, #02, #06 are the non-negotiables**;
the rest are the working-functional set. See
`docs/requirements/pepper-pre-cutover-must-haves.md` for the rationale.
