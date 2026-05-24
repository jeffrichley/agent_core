# agent-core-qa

Release-validation scenario runner for the agent_core daemon. **A tool, not a being.**

## What it is

Seven pytest scenarios that exercise a running Phase 3.5 test daemon (`agent-core daemon --instance test`) end-to-end. Each scenario maps to a "v0.3.0 cannot ship if broken" surface: daemon liveness, envelope round-trip, install identity (dynamic keystone), brief framework, scheduler, discord_send, voice synthesis.

## Tool-shaped, not being-shaped

This package is a test runner. It has:
- No `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, `HEARTBEAT.md`, diary, breadcrumbs, or lessons.
- No continuity of self — each pytest invocation is fresh.
- No ethical weight — `rm -rf packages/agent-core-qa/` is functionally identical to deleting any pytest suite.
- No registration on the bus as a peer; it's an HTTP client that exits when done.

Same shape as `pytest` or `playwright`. Procedural capability through playbooks (scenario functions); nothing for the tool to know about itself.

## Architectural inheritance: standing dynamic surface

From Phase 2.6's PR description, the bug-cadence observation: static artifact analysis caught Bugs 1-2; dynamic real install caught Bug 3. The next failure class — "does the daemon actually start after install" — was named as Phase 2.7 territory.

This package is the standing dynamic surface that catches that failure class (Scenario 1 as autouse fixture precondition) and every subsequent dynamic-only failure class on every release going forward. Phase 3.5 + Phase 2.6 + agent-core-qa is one continuous arc: build the sandbox, fix the install, prove the install still works release over release.

## Runbook

```bash
# v0.3.0 release-validation runbook
AGENT_CORE_HOME=~/.agent-core-test agent-core daemon install --instance test --release v0.3.0
agent-core daemon start --instance test
cd packages/agent-core-qa
uv run pytest tests/
# all 7 pass → safe to refresh prod
# any fail → fix the regression first
```

For an already-running test daemon, `agent-core daemon refresh --instance test --release vX.Y.Z` does install + restart in one command.

## Scenarios

1. `test_daemon_liveness` — precondition (autouse).
2. `test_envelope_send_receives_ack` — bus round-trip.
3. `test_install_identity_dynamic_keystone` — install code path identity at runtime (the load-bearing scenario for release validation).
4. `test_brief_framework_compose_and_submit` — brief framework smoke.
5. `test_scheduler_create_and_delete_roundtrip` — scheduler round-trip.
6. `test_discord_send_tool_routes_through_stub` — discord_send tool dispatch with stubbed discord endpoint.
7. `test_voice_synthesize_returns_audio_bytes` — voice synthesis smoke (no audio-quality validation).
