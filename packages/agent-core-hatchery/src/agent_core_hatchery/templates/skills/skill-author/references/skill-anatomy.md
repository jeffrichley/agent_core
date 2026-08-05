# Skill Anatomy

## Required

- `SKILL.md` with frontmatter (`name`, `description`, `when_to_use`) + body
- Directory `<vault>/.claude/skills/<name>/`

## Optional

- `references/` — markdown docs the skill needs to consult mid-execution
- `scripts/` — executable code (Python, shell, JS); set executable bit
- `assets/` — non-text assets (images, fixtures)

## Frontmatter fields beyond required

- `category` — one of: `daily`, `weekly`, `monthly`, `ad-hoc`, `meta`
- `voice_triggers` — optional list of speech-to-text aliases
- `proactive` — boolean; whether the agent should suggest invoking this
  unprompted when conditions match

## Naming conventions

- Kebab-case
- Verb-first when possible (`compose-brief`, not `brief-composer`)
- Avoid ambiguity with existing platform tools
