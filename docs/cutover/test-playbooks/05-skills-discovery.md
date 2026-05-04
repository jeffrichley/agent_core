# Cutover #05 — Skills survive the cutover (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-05-skills-discovery.md`](../../requirements/pepper-cutover-05-skills-discovery.md)
**Implementation commits:** *none in agent-core* — see "What was implemented" for why.

## What was implemented

Nothing new in `agent-core` itself. Skill discovery, slash-command resolution, frontmatter-flag enforcement, and user-scope-vs-project-scope override behavior are all owned by **Claude Code**, not by `agent-core`. `agent-core`'s scope is the hook pipeline (SessionStart, UserPromptSubmit, PreCompact, SessionEnd) and the bus daemon — neither touches `~/.claude/skills/`.

The cutover surface for #05 is therefore **verification-only**: confirm that on the new substrate (agent-core hooks driving Pepper's session), Claude Code's skill mechanism continues to behave as it does today on the legacy `~/.pepper/` substrate. No new code; the existing skill directories at `~/.claude/skills/<name>/SKILL.md` keep working because Claude Code reads them directly.

The "documented path for adding new skills" acceptance criterion is satisfied by the **briefs-author skill** Jeff is authoring as the cutover-gate-blocking follow-up on #09. That skill is the canonical exemplar of "this is how a Pepper-facing skill is structured on agent-core"; #05's documentation deliverable cites it rather than reinventing.

## Acceptance criteria (from spec §"Done looks like")

> 1. All current user-scope skills work end-to-end on the new substrate.
> 2. Slash commands (e.g., `/war`) resolve to the right skill.
> 3. Frontmatter flags honored: `disable-model-invocation`, `user-invocable`, `allowed-tools`, `argument-hint`.
> 4. User-scope wins over project-scope on name collision.
> 5. Documented path for adding new skills, matching or improving on the current `~/.claude/skills/<name>/SKILL.md` shape.
> 6. Specifically for WAR: invoking `/war` (or equivalent) runs the three-phase workflow (gather → synthesize → render) with config still loading from `~/.claude/skills/war/config/war_config.json` or its documented successor.

## Verification steps (end-of-cutover, against live Pepper-on-agent-core)

This is a low-risk ticket — Claude Code's skill mechanism is stable and unaffected by `agent-core`'s hook wiring. Verification is mostly "confirm nothing regressed" rather than "exercise new behavior." All steps run against Pepper's real session once she is on agent-core.

### Step 1 — Skill discovery

In a fresh Pepper session, ask Pepper to list her available user-scope skills. Expected: she names at least the skills present at `~/.claude/skills/` (today: `war`, plus the broader gstack set; `pepper-design` if it has been authored or migrated by then; the briefs-author skill once it lands). Slash-command-discoverable skills should appear via `/help` or equivalent enumeration.

### Step 2 — Slash-command resolution

In Pepper's session, invoke `/war` (the canonical slash command for the WAR skill). Expected: the WAR skill loads, follows its three-phase contract (gather → synthesize → render), and reads its config from `~/.claude/skills/war/config/war_config.json`. The first phase should pull from `Memory/daily/summaries/` (which #04 owns).

If a `pepper-design` skill has been authored by cutover time, also smoke `/pepper-design` (or its slash command). If not, document the gap and note it as a post-cutover deliverable rather than a blocker.

### Step 3 — Frontmatter flags honored

Pick one skill with `disable-model-invocation: true` (the WAR skill is the canonical example — it must run on Friday cron or explicit ask, never auto-fire). Run a Pepper session that *would* match the skill's heuristic if auto-invocation were on (e.g., a Friday-morning conversation about weekly status). Expected: Pepper does NOT auto-invoke `/war`. She might mention it as available, but doesn't fire it.

Pick one skill with `user-invocable: true`. Invoke it via slash command. Expected: it fires.

If `argument-hint` is set on any skill, verify Claude Code surfaces the hint at command-line level when the user types the slash without args.

### Step 4 — User-scope override on collision

Create (temporarily) a project-scope skill at `<pepper-project>/.claude/skills/war/SKILL.md` with a different version line in its frontmatter. Boot Pepper. Expected: the user-scope `~/.claude/skills/war/SKILL.md` wins; the project-scope copy is shadowed. Remove the project-scope copy after the test.

This step is purely about not regressing Claude Code's documented override semantic. If it fails, the bug is in Claude Code, not agent-core — but flag it as a cutover-blocker because it would silently shadow upgraded user-scope skills.

### Step 5 — Documented path for adding new skills

Confirm the briefs-author skill (under `~/.claude/skills/briefs-author/SKILL.md` once Jeff lands it) is reachable as a working example of "this is how a Pepper-facing skill is structured on agent-core." Pepper or Jeff should be able to point a new skill author at it and say "do this." If the briefs-author skill has not landed by cutover time, write a stub README at `~/.claude/skills/_authoring-guide.md` (or under `docs/` in agent-core) covering: directory layout, SKILL.md frontmatter contract, how to test a skill in a real Pepper session, override semantics.

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | Pepper enumerates her user-scope skills correctly. |
| Step 2 | `/war` resolves and runs the three-phase workflow against the real config file. |
| Step 3 | `disable-model-invocation: true` and `user-invocable: true` flags honored as documented. |
| Step 4 | User-scope skill wins on name collision with project-scope. |
| Step 5 | New-skill authoring path is documented (briefs-author skill OR explicit guide). |

## Known limitations (recorded; not blocking #05 done OR the cutover gate)

- **`pepper-design/` skill not present at `~/.claude/skills/`.** The spec names it as one of the current set, but a local survey found only `war/` (plus the gstack skills). The skill may live under `~/.pepper/.claude/skills/` or may not yet have been authored. If Pepper actively uses it, source-or-author it during the cutover window; otherwise omit from Step 1's expectation list and revisit post-cutover.
- **ROADMAP sub-project F (broader skills consolidation) is out of scope.** This ticket is the smaller "skills survive the move" subset. Consolidation work is post-cutover.
- **No agent-core-side code change.** If Claude Code's skill mechanism ever changes (e.g., a future Claude Code release moves the skill directory or alters frontmatter syntax), this ticket's verification needs to be re-run — but that's a Claude Code event, not an agent-core regression risk.
- **Step 4 requires a temporary project-scope skill.** Don't skip the cleanup at end of test. If the test environment is Pepper's actual `~/.pepper/` project, leaving a stray project-scope skill could shadow the user-scope version of WAR mid-week.
- **Step 5 deliverable couples to #09's briefs-author skill.** If the briefs-author skill ships first, this step is "point at it." If it lands after the cutover gate runs, the stub authoring guide carries the criterion until the skill arrives — and the criterion is satisfied for cutover purposes.
