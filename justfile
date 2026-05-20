# Justfile — single quality-command surface for agent_core workspace.
#
# Keep this as the source of truth for local + CI check commands so
# developers and agents run the same gates in the same order.

set windows-shell := ["cmd.exe", "/c"]

# Workspace package scopes
core-src := "packages/core/src"
core-tests := "packages/core/tests"
channel-src := "packages/agent-core-channel/src"
channel-tests := "packages/agent-core-channel/tests"
credentials-tests := "packages/credentials/tests"
discord-tests := "packages/agent-core-discord/tests"

default:
    @just --list

# Composite gate (recommended before push)
check: lint typecheck contracts test

# Developer convenience: apply lint auto-fixes + formatter
fix:
    uv run --no-sync ruff check --fix packages/core packages/agent-core-channel
    uv run --no-sync ruff format packages/core packages/agent-core-channel

# Lint
lint:
    uv run --no-sync ruff check packages/core packages/agent-core-channel

lint-all:
    uv run --no-sync ruff check .

# Formatting
format:
    uv run --no-sync ruff format packages/core packages/agent-core-channel

format-check:
    uv run --no-sync ruff format --check packages/core packages/agent-core-channel

# Type checking (uses [tool.mypy] in pyproject.toml)
typecheck:
    uv run --no-sync mypy

# Architecture contracts
contracts:
    uv run --no-sync lint-imports

# Tests
test:
    uv run --no-sync pytest -q

test-fast:
    uv run --no-sync pytest {{core-tests}} {{channel-tests}} -q

test-core:
    uv run --no-sync pytest {{core-tests}} -q

test-channel:
    uv run --no-sync pytest {{channel-tests}} -q

# Setup
sync:
    uv sync --dev

# Install this clone's git hooks (.githooks/) — run once per clone/worktree
install-hooks:
    uv run --no-sync python -m agent_core.githooks

# Cut a release: build the aggregated CHANGELOG from fragments + a local
# annotated tag. Does NOT push — push the tag explicitly when ready.
release VERSION:
    uv run --no-sync towncrier build --yes --version {{VERSION}}
    git add CHANGELOG.md changelog.d
    git commit -m "docs(changelog): release v{{VERSION}}"
    git tag -a "v{{VERSION}}" -m "Release v{{VERSION}}"
    @echo "Tagged v{{VERSION}} locally (changelog committed). Push when ready: git push origin v{{VERSION}}"
