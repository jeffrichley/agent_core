# Pepper cutover — playbook

**Purpose:** how to work each Pepper cutover ticket, where the specs live, and how the per-ticket state is tracked.

**Mode of work:** solo coding agent + adversarial subagent reviews + direct commits to `main`. The earlier multi-implementer roster (Vale / Locke / Folio) and the Cadence PR agent are gone — that flow is no longer used.

**Index of this directory:** [`README.md`](README.md). **Test playbooks (deferred verification):** [`docs/cutover/test-playbooks/`](../cutover/test-playbooks/).

---

## Per-ticket flow

For every cutover ticket:

1. **Read the spec.** Open the ticket's `pepper-cutover-NN-…md` first. Then open every doc linked from its **Related** / **Parent** frontmatter and any in-body `…md` / `…yaml` links. The links are the project's way of saying "read this before you implement."
2. **Map spec to repo (§3b below).** Some behavior may already be on `main`. Before writing code, list each acceptance bullet and mark **met / partial / missing** with evidence. Only implement the gaps.
3. **Implement.** One ticket at a time. Branching is optional for solo — direct edits on `main` are acceptable; use a feature branch if the change is large enough that a wrong commit would be costly to revert.
4. **Adversarial review.** Dispatch the `superpowers:code-reviewer` subagent with full context (what changed, what to attack, files to read). Apply must-fix and should-fix findings. Re-run tests + lint.
5. **Commit to `main`** with a focused message. One concern per commit (per repo `CLAUDE.md`).
6. **Write the test playbook entry** at `docs/cutover/test-playbooks/NN-slug.md`. This is **deferred verification** — describes what to verify at the end-of-cutover gate, not something to run incrementally.
7. **Update the per-ticket status table** at the bottom of this playbook.

---

## Superpowers skills (use the plugin where it fits)

The Superpowers skills cover the disciplines this work expects. Use them by name:

| When | Skill |
|------|-------|
| Multi-step / unclear scope before code | `superpowers:writing-plans` |
| Behavior change or bugfix | `superpowers:test-driven-development` + `superpowers:systematic-debugging` |
| Before claiming done | `superpowers:verification-before-completion` |
| Adversarial review of a finished slice | `superpowers:code-reviewer` (subagent) |
| Branch is green, deciding integration shape | `superpowers:finishing-a-development-branch` |

If Superpowers is not available in the host, mirror the same discipline (plan → test-first when appropriate → debug with evidence → verify before done → adversarial review).

---

## §3b — When work might already be on `main`

Cutover items can be ahead of, behind, or wrong-shape vs. their spec. Do this before any large diff:

1. **Map spec to repo.** Search packages and `docs/examples/` for the tools, events, and filenames the ticket names. Skim recent `main` history if helpful.
2. **Score acceptance literally.** For each "Done looks like" bullet, mark **met / partial / missing** with evidence (command output, file path, test name).
3. **Choose a path:**

| Situation | Action |
|-----------|--------|
| All acceptance criteria met on `main` | Small commit that adds or tightens **tests**, **docs**, or **status** so "done" is auditable. Then write the test playbook entry and update the status table. |
| Partially met | Implement only the gaps. Keep the gap list alive in the commit message until closed. Avoid scope creep. |
| Wrong shape | Prefer one commit that aligns behavior to the spec. If the spec itself is what needs to change, do that as a separate doc commit first. Do not silently redefine "done" in code only. |

Status moves to **Implementation complete** only when the spec and reality match — not when adjacent code lands.

---

## Dependency diagrams (sequencing hints)

Use these to choose the next ticket, not to skip prerequisites.

### Pepper cutover specs (#01–#09 + epic)

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
  EP --> C09["09 Brief framework"]
  DC["Handoff daemon contract"]
  DC --> C02
  C08 -.->|"partially gates done: visible ready signal"| C02
  C04 -.->|"summaries / JSONL feeds skills + context"| C05
  C07 -.->|"SessionStart identity wiring"| C01
  C07 -.->|"PreCompact / SessionEnd handoff wiring"| C02
```

- Solid `EP -->`: epic child (all must close for the cutover gate).
- Solid `DC -->`: daemon contract is the handoff implementation shape for **#02**.
- Dashed `-.->`: integration / sequencing coupling.

### Related GitHub issues

The bus / Discord / wakes issues sit alongside the cutover specs but are **not** part of the cutover gate. Pull a fresh snapshot when needed:

```bash
gh issue list --repo jeffrichley/agent_core --state open
```

Snapshot **2026-05-02** (edges = product / sequencing intuition):

```mermaid
flowchart TB
  subgraph discord["Discord bridge"]
    i23["#23 OPEN ack + chunk-limit semantics"]
    i13["#13 OPEN typing TTL / placeholder"]
  end
  subgraph bus["Bus"]
    i15["#15 OPEN heartbeats"]
    i16["#16 OPEN read-only bus tail"]
    i17["#17 OPEN DLQ / retry"]
    i18["#18 OPEN expires_at enforcement"]
    i19["#19 OPEN typed command envelopes"]
  end
  subgraph wakes["Wakes / notify"]
    i14["#14 OPEN burst coalesce wakes"]
  end
  i18 -.-> i17
```

Cutover **#03** aligns thematically with the Discord issues; **#04 / #08** with bus + notify; **#02 / #08** with wakes. There is no strict 1:1 mapping.

---

## Spec index

### Epic (parent)

| Id | Spec |
|----|------|
| Pre-cutover epic | [`pepper-pre-cutover-must-haves.md`](pepper-pre-cutover-must-haves.md) |

### Cutover tickets

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
| 09 | [Brief framework v1 design](../superpowers/specs/2026-05-04-brief-framework-design.md) |

### Supporting / adjacent specs

| Spec | When to read |
|------|----------------|
| [`pepper-handoff-daemon-contract.md`](pepper-handoff-daemon-contract.md) | Handoff bus, daemon writer, hook-minimal enqueue — tight coupling to **#02** |
| [`pepper-requirements.md`](pepper-requirements.md) | Original hook + tool expectations |
| [`pepper-identity-injection-size-limit.md`](pepper-identity-injection-size-limit.md) | Identity truncation / injection limits — **#01** |
| [`pepper-handoff-writer-bugfix.md`](pepper-handoff-writer-bugfix.md) | Predecessor notes for handoff — **#02** |
| [`docs/examples/pepper-agent-core.yaml`](../examples/pepper-agent-core.yaml) | Example pipeline wiring — **#07**, parts of **#01** |
| [`docs/ROADMAP.md`](../ROADMAP.md) | Discord / skills / pipeline roadmap references |
| [`README.md`](README.md) | Directory index + reading order |

**Sequencing hints (not a substitute for reading specs):** #08 partially gates #02 (the "ready" signal must be visible). #04 relates to #05 (summaries → skills). #07 references #01/#02 for content vs firing.

---

## Per-ticket status

Update this table whenever a ticket moves. Status values: **Not started · In progress · Implementation complete · Verified**. "Verified" means the test playbook entry passed end-to-end on a real Pepper environment.

| # | Ticket | Status | Notes |
|---|--------|--------|-------|
| 01 | [Identity fidelity](pepper-cutover-01-identity-fidelity.md) | **Implementation complete** | Originally PR #29 (`7269dba`); corrected by `5c287f8` (thin IdentityInjector + new HandoffInjector). Test playbook: [`01-identity-fidelity.md`](../cutover/test-playbooks/01-identity-fidelity.md). Verification deferred to end-of-cutover. |
| 02 | [Handoff observability](pepper-cutover-02-handoff-observability.md) | **Implementation complete** | Landed in `028ddcb` (placeholders for cross-session pending + failed; basename guard already in `5c287f8`). Daemon-side `HandoffReady`/`HandoffFailed` publication already shipped pre-#02. Test playbook: [`02-handoff-observability.md`](../cutover/test-playbooks/02-handoff-observability.md). Mid-session perception of bus events is **#08**. |
| 03 | [Discord verb parity](pepper-cutover-03-discord-verb-parity.md) | **Implementation complete** | Stranded PR #31 cherry-picked as `ac3cbd0` (briefing.py canonical embed builder) + `d97fc8e` (dispatch + tests + fakes). Adversarial review surfaced four Important items; all four landed in `954b589` (`_parse_iso_datetime` trailing-Z only, channel-type guard for stage/voice events, +10 validation/coverage tests). 13 Pepper-facing Discord verbs reachable via `_dispatch` (alias map covers `send_discord_message` / `edit_message` / `add_reaction` / `fetch_messages`). Test playbook: [`03-discord-verb-parity.md`](../cutover/test-playbooks/03-discord-verb-parity.md). PR #31 should be closed as superseded. |
| 04 | [Daily JSONL pipeline](pepper-cutover-04-daily-jsonl-pipeline.md) | **Implementation complete** | Single bus-owned daily JSONL at `~/.agent-core/bus/raw/<date>.jsonl` written by `builtin.daily_raw_jsonl` on `pre_publish`. New `agent_core.bus_log` library exposes `iter_envelopes` + `iter_for_agent` (filter + project via pluggy-registered projectors). Three call surfaces: CLI `agent-core bus-log show --agent <name>`, MCP tool `show_my_day` on `ClaudeCodeMCPEndpoint` (auto-scoped to `self.name`), and direct library import for Pepper's reflection job. Default projectors: `TextMessage` (with scheduler-heartbeat skip), `HandoffReady`, `HandoffFailed`, `Acknowledgment` (skip), plus a fallback projector for unregistered event types. Test playbook: [`04-daily-jsonl-pipeline.md`](../cutover/test-playbooks/04-daily-jsonl-pipeline.md). |
| 05 | [Skills discovery](pepper-cutover-05-skills-discovery.md) | Not started | |
| 06 | [Vault continuity](pepper-cutover-06-vault-continuity.md) | **Implementation complete** | PR #32 cherry-picked as `1e66ac5` (`agent-core vault plan-dry-run` CLI + tests + initial runbook). Adversarial review caught two detector bugs (false-positive on URL routes like `/internal/handoff-jobs`; false-negative on `~`-relative paths) plus runbook factual drift on auto-memory derivation — all fixed. Runbook now leads with `autoMemoryDirectory` (the Claude Code-supported override) as the canonical mitigation. **Operator file moves deferred to the cutover window with Pepper offline** — concurrent writes during a copy would lose memories. Test playbook: [`06-vault-continuity.md`](../cutover/test-playbooks/06-vault-continuity.md). PR #32 should be closed as "superseded by 1e66ac5". |
| 07 | [Hook fidelity](pepper-cutover-07-hook-fidelity.md) | **Implementation complete** | §3b found one production gap: example `pepper-agent-core.yaml` didn't register TimeInjector on UserPromptSubmit. Fixed: added `UserPromptSubmit:` block with `track_session: true`. New `TestTimeInjectorTrackSession` tests lock in per-turn deltas; new `test_pepper_example_yaml.py` is the wiring tripwire that catches the spec's documented regression mode (someone deletes the registration). Test playbook: [`07-hook-fidelity.md`](../cutover/test-playbooks/07-hook-fidelity.md). |
| 08 | [Notification surface](pepper-cutover-08-notification-surface.md) | **Implementation complete** | No new framework code: existing `deliver()` + `_notify_mail_arrived` path is already kind-agnostic. Locked in by 3 new tests in `test_notify_mail_arrived.py` (deliver-kind-agnostic for `Event`; `_envelope_to_dict` round-trips full `EventPayload`; mixed `TextMessage` + `Event` traffic surfaces uniformly) plus surface-mapping doc [`docs/cutover/notification-surfaces.md`](../cutover/notification-surfaces.md). Test playbook: [`08-notification-surface.md`](../cutover/test-playbooks/08-notification-surface.md). Closes the perception side of #02 scenario (b). |
| 09 | [Brief framework v1](../superpowers/specs/2026-05-04-brief-framework-design.md) | **Implementation complete (framework); 2 follow-ups gate Verified** | New package `agent-core-briefs` formalises the deterministic-LLM-deterministic seam (gather → wake → compose → submit) for Pepper's daily artifacts. Async-concurrent gather engine + filesystem-loaded fetchers (`filesystem_read`, `cli`) + filesystem-loaded destinations (`discord_embed`, `markdown_file`) + playbook MD/YAML parser + 7-tool agent surface (`compose_brief`, `list_sections`, `get_section_spec`, `validate_section`, `compress_sections`, `add_extension_section`, `submit_brief`) + atomic submit handler + audit log at `~/.agent-core/briefs/audit.jsonl`. `SchedulerEndpoint` extended (T8) to fire `Event` envelopes alongside `TextMessage`. CLI subapp `agent-core briefs` (compose / fetchers list / fetchers test) for operator debugging. Pluggy plugin (`register_endpoint_types` + `register_cli_subapps`). Pepper example yaml gains `briefs.orchestrator` endpoint with `${agent_root}` substitution + tripwire test (`TestPepperExampleYamlBriefs`). End-to-end integration test (`test_e2e_morning_brief.py`) drives the full chain in-process; the T17 e2e surfaced and fixed a real bug (`a852284`) in `get_section_spec` for conditional sections. **Two cutover-gate-blocking follow-ups remain:** cross-endpoint MCP tool mounting (so Pepper's session has the briefs tools available — currently in flight) and a Pepper-facing briefs-author skill (Jeff is authoring). Without both, Pepper cannot compose a brief on the new substrate even though the framework code shipped. See [`09-brief-framework.md`](../cutover/test-playbooks/09-brief-framework.md) §"Cutover-gate-blocking follow-ups". |

**Cutover gate** (per the epic): every row reaches **Verified**. **#01, #02, #06 are non-negotiable**; the others are the working-functional set. **#09 was added post-epic** and joins the working-functional set.

---

## Stranded GitHub PRs

Three PRs were opened by removed agents and never merged. They will not be rebased by their original authors. For each, the choice is: cherry-pick the salvageable parts onto a fresh local branch, or close the PR and reimplement against the spec.

| PR | Ticket | State at last check | Action |
|----|--------|---------------------|--------|
| [#30](https://github.com/jeffrichley/agent_core/pull/30) | Cutover #02 | Open, draft, conflicting | **Superseded by `028ddcb`** — placeholder text was lifted nearly verbatim. PR can be closed. |
| [#31](https://github.com/jeffrichley/agent_core/pull/31) | Cutover #03 | Open, draft, mergeable but evidence matrix marks `send_briefing` partial and real-guild smoke missing | **Cherry-picked as `ac3cbd0` + `d97fc8e`**, then hardened (`954b589`). PR can be closed. |
| [#32](https://github.com/jeffrichley/agent_core/pull/32) | Cutover #06 | Open, draft, mergeable | **Cherry-picked as `1e66ac5`**, then hardened (detector + runbook fixes from adversarial review). PR can be closed. |
