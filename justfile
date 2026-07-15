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

# Robot / pre-push gate (also what CI runs): lint + types + contracts +
# deterministic-coverage tests (whole-repo 85% floor) + patch coverage (80% of
# changed lines). Both coverage checks run HERE so a worker catches under-tested
# code BEFORE it pushes — not after, in CI. Coverage runs single-threaded (see
# `test`) so the number can't flake. Used by .githooks/pre-push.
check: lint typecheck contracts test patch-cov

# Fast iteration gate (humans): the same checks but tests run parallel and
# WITHOUT coverage, so it's quick. It does NOT enforce the coverage floors —
# run `check` (or just push; the pre-push hook runs `check`) before a PR.
check-fast: lint typecheck contracts test-fast

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

# Patch-coverage gate: >=80% of the lines THIS branch changed must be covered.
# Catches under-tested new code — the gap the whole-repo floor misses, since a
# few weak lines barely move a big project's total. Reads coverage.xml produced
# by `test`; compares against origin/main.
patch-cov:
    uv run --no-sync diff-cover coverage.xml --compare-branch=origin/main --fail-under=80

# Full-suite tests WITH coverage, run SINGLE-THREADED (-n 0) so the coverage
# number is deterministic. Coverage under parallel xdist flakes: a worker that
# hiccups on a real-I/O test drops the % below the floor even when every test
# passes; single-process removes that race. Enforces the whole-repo 85% floor
# (--cov-fail-under, pyproject addopts). Slower than parallel — use `test-fast`
# for quick iteration.
test:
    uv run --no-sync pytest -n 0 -q

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
