# Cutover #05: Skills Survive the Cutover

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** High — skills are my muscle memory. Without them I lose hands.
**Status:** Open
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- `docs/ROADMAP.md` sub-project F (skills consolidation — currently "not started")
- Current user-scope skills at `C:\Users\jeffr\.claude\skills\`

---

## What

My user-scope skills (current set: `war/`, `pepper-design/`, plus whatever is added between now and cutover) must remain:

- **Discoverable** by SKILL.md frontmatter contract.
- **Invokable** by their slash-command names where `user-invocable: true`.
- **Filtered correctly** when `disable-model-invocation: true` is set, so I do not auto-fire them. The WAR skill specifically must not auto-invoke; it runs on Friday cron or explicit ask.
- **Override-correct** — user-scope wins over project-scope on name conflicts, matching current Claude Code behavior. Otherwise the project copies in `.pepper/.claude/skills/` would shadow my upgraded user-scope versions.

I do not care where skills live on disk in the new substrate. I care that they are discoverable and that their frontmatter contracts are honored.

## Why

Skills are how I keep complex multi-phase work from re-burning context every time:

- The **WAR skill** is how I produce the Friday report Jeff confirmed he loves the format of. Three phases of deterministic + synthesis work that I would have to redo by hand every Friday otherwise.
- The **pepper-design skill** is how I produce design-system-consistent artifacts.
- Future skills (the ones I have not built yet) need a contract to land into.

Shipping agent-core without a skills story is shipping me without my hands. Sub-project F is marked "not started" in ROADMAP — that gap is what this is asking to close.

Even more important than the *current* skills: I need to be able to add new skills on the new substrate without learning a different contract. If the new system's skill format diverges from `~/.claude/skills/<name>/SKILL.md`, document the migration explicitly.

## Done looks like

1. All current user-scope skills work end-to-end on the new substrate.
2. Slash commands (e.g., `/war`) resolve to the right skill.
3. Frontmatter flags honored: `disable-model-invocation`, `user-invocable`, `allowed-tools`, `argument-hint`.
4. User-scope wins over project-scope on name collision.
5. Documented path for adding new skills, matching or improving on the current `~/.claude/skills/<name>/SKILL.md` shape.
6. Specifically for WAR: invoking `/war` (or equivalent) runs the three-phase workflow (gather → synthesize → render) with config still loading from `~/.claude/skills/war/config/war_config.json` or its documented successor.
