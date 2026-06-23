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

# Tests (full suite, coverage + 85% gate — matches CI)
test:
    uv run --no-sync pytest -q

# Fast inner-loop run: full suite, NO coverage instrumentation.
# Branch-coverage doubles wall time (~230s -> ~110s) and adds no signal
# while iterating. The 85% gate still runs in `just check` / CI.
test-fast:
    uv run --no-sync pytest --no-cov -q

# Scoped runs for when you're only touching one package. These skip
# coverage too — running a subset under the whole-repo 85% gate would
# always fail the gate (the other packages' source goes unexercised).
test-core:
    uv run --no-sync pytest {{core-tests}} --no-cov -q

test-channel:
    uv run --no-sync pytest {{channel-tests}} --no-cov -q

# Setup
sync:
    uv sync --dev

# Install this clone's git hooks (.githooks/) — run once per clone/worktree
install-hooks:
    uv run --no-sync python -m agent_core.githooks

# Releases are managed by release-please (Phase 2.5).
# See docs/setup/releases.md for the new flow:
# conventional PR title → squash-merge → bot opens release PR →
# merge release PR → tag + GH Release → release.yml uploads wheels →
# agent-core daemon refresh.
