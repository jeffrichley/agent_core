# agent_core justfile (workspace root)
set shell := ["powershell", "-NoProfile", "-Command"]

# Run all tests
test:
    uv run --no-sync pytest -v

# Run tests (fast, no output)
test-quick:
    uv run --no-sync pytest -q

# Lint
lint:
    uv run --no-sync ruff check .

# Format
format:
    uv run --no-sync ruff format .

# Architecture contracts
contracts:
    uv run --no-sync lint-imports

# Full quality gate (mirrors CI)
gate: lint contracts test

# Install agent-core as a global tool (isolated venv, no file lock conflicts)
install:
    uv tool install --reinstall "e:/workspaces/ai/agents/agent_core"

# Sync project dependencies (dev only)
sync:
    uv sync
