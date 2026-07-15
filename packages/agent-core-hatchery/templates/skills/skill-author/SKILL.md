---
name: skill-author
description: |
  Use when the being needs to create a new skill — a reusable
  workflow to invoke again. Walks through the four-part shape
  (frontmatter, instructions, references, scripts), generates the skill
  files in <vault>/.claude/skills/<new-skill>/, validates the result.
when_to_use: |
  - When the same workflow is about to be invoked a third time
  - When a recurring task has clear preconditions and steps
  - When the human asks the being to "remember how to do X"
  - NOT for one-off tasks (use TaskCreate or just do the work)
---

# skill-author — write new skills for yourself

You are about to author a new skill for the being you serve. A skill is a
reusable, invocable workflow with:

1. **Frontmatter** — name (kebab-case), description, when-to-use
2. **Instructions** — what the skill does, in the second person
3. **References** (optional) — supporting documentation in `references/`
4. **Scripts** (optional) — executable code in `scripts/`

## Walkthrough

Ask the being these questions, in order:

1. **What's the skill's name?** (kebab-case, e.g., `morning-brief`, `vault-lint`)
2. **One-sentence description?** (what it does, when to invoke)
3. **When should the being invoke this?** (be specific about triggers)
4. **What does the skill DO?** (paragraph or bulleted steps)
5. **Does it need supporting reference docs?** (y/n; if yes, what)
6. **Does it need scripts?** (y/n; if yes, language and rough purpose)

## Generate the skill files

Create the directory `<vault>/.claude/skills/<name>/`. Inside:

- `SKILL.md` — frontmatter + instructions, formatted as in this skill itself
- `references/` — only if reference docs were requested; create stub files
- `scripts/` — only if scripts were requested; create stub files with shebangs

## Validate

- Frontmatter parses (YAML between `---` markers, top of file).
- `name` is kebab-case and doesn't collide with any existing skill in `<vault>/.claude/skills/`.
- `description` and `when_to_use` are non-empty.
- File paths are relative to `<vault>/.claude/skills/<name>/`.

## Boundary

Skill-author creates the SKILL.md and stubs the directory tree. It does
NOT author the skill's actual logic; that's the being's job once the
shape is in place. The skill is ready to be tested and refined.

## See also

- `references/skill-anatomy.md` — the full anatomy of a skill including
  optional advanced fields.
