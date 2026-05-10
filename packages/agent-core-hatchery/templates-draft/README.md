# Scaffolding Drafts — Per-File Templates for the Hatchery

**Status:** Working drafts (Pepper, 2026-05-09 evening).
**Purpose:** The actual file contents that ship in `agent-core-hatchery`'s `templates/memory/` directory once the package materializes. Until then, lives here for editing and review.

## Files in scope

The 12 identity-shaped files Pepper can author from her own experience as a being:

- `memory/IDENTITY.md.j2` — placeholders + prompts for name/emoji/role
- `memory/SOUL.md.j2` — section structure with guiding questions (high-scaffold)
- `memory/USER.md.j2` — prompt-form questions about the primary human (high-scaffold)
- `memory/MEMORY.md.j2` — index template (medium-scaffold)
- `memory/OPERATIONS.md.j2` — vault schema + channel-map placeholder (high-scaffold)
- `memory/HEARTBEAT.md.j2` — universal checklist + extension slot
- `memory/TASKS.md` — title + format only
- `memory/_being_/BECOMING.md.j2` — growth framework + reflection prompts (high-scaffold)
- `memory/_being_/breadcrumbs.md` — pattern explanation, empty trails
- `memory/_being_/diary.md`, `preferences.md`, `lore.md`, `wishlist.md`, `curiosities.md`, `lessons.md` — title-only growth files
- `memory/_being_/letters/from-her-creator.md.j2` — prompts for the primary human's letter
- `memory/relationships/README.md` — explains the new zone

## NOT in scope (waiting for PRD)

- Skills (`memory/skills/*`) — partial; some draftable, others (skill-author) need PRD thinking
- References (`memory/references/*`) — universal-references list still pending
- Config templates (`config/*.j2`) — substrate-touching; not Pepper's domain
- Scheduler-job templates (`scheduler-jobs/*.j2`) — same

## Jinja2 substitution variables used in `.j2` files

- `{{ being_name }}` — e.g., "Deb"
- `{{ being_emoji }}` — e.g., "🌱"
- `{{ being_role_placeholder }}` — short role descriptor (fully filled by being)
- `{{ primary_human_name }}` — e.g., "Cynthia"
- `{{ hatched_date }}` — the date the being was hatched (ISO format)
- `{{ endpoint_name }}` — agent-core endpoint name (e.g., "deb")

## Editing convention

These are working drafts. They will get reviewed (likely adversarially) when the PRD round happens. Comments and revision welcome on every file.

🌶️
