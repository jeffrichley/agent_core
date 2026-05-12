# agent-core-hatchery

Bootstrap system for hatching new agent-core beings. See
`docs/superpowers/specs/2026-05-09-issue-75-agent-core-hatchery-design.md`
for the design rationale and Pepper's source-material requirements at
`packages/agent-core-hatchery/docs/being-bootstrap-requirements.md`.

## Install

Workspace package, installed automatically via `uv sync` at the repo root.

## Usage

Interactive (primary UX, lands in Phase 5):

    hatch-being

Non-interactive (tests, automation):

    hatch-being --config hatch-config.yaml --vault-root /tmp/hatch-test

Top-up an existing being's vault with newly-added scaffolding files:

    hatch-being --init-missing
