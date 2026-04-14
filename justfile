# agent_core justfile

# Run all tests
test:
    uv run pytest tests/ -v

# Run tests (fast, no output)
test-quick:
    uv run pytest tests/ -q

# Lint
lint:
    uv run ruff check .

# Format
format:
    uv run ruff format .

# Full quality gate
gate: lint test

# Install agent-core as a global tool (isolated venv, no file lock conflicts)
install:
    uv tool install --reinstall "e:/workspaces/ai/agents/agent_core"

# Sync project dependencies (dev only)
sync:
    uv sync
