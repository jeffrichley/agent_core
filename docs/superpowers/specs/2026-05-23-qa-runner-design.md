# QA-runner — release-validation scenarios for the Phase 3.5 test daemon (Design)

> **Status:** Drafted 2026-05-23. Pending spec-review approval.
>
> **Issue:** to be filed by Pepper after spec sign-off; this doc precedes the GitHub issue.
>
> **Tool name:** default identifier `tester` in code; final name pending Jeff sign-off before implementation lands.
>
> **Scope:** A pytest-based scenario runner that exercises the Phase 3.5 test daemon (`--instance test`) end-to-end against a built release artifact (`v0.3.0` or later). Seven scenarios, demand-validated against "v0.3.0 cannot ship if this breaks." Tool-shaped: function-named, no identity files, no continuity-of-self, no ethical weight, procedural capability through pytest playbooks.

## Problem

Phase 2.6 closed three release-pipeline bugs Phase 3.5's test instance surfaced on the first real install attempt. Phase 2.6's PR description named the next failure class explicitly: "does the daemon actually start after install" — Phase 2.7 territory, dynamic surface (not catchable by static artifact analysis). And beyond start: does the bus actually accept envelopes; does the brief framework still compose; does the scheduler create + delete jobs; does the discord adapter route the new `discord_send` tool; does qwen-tts actually synthesize after the workspace promotion.

These behaviors are all already shipped in v0.2.0. The risk for v0.3.0 isn't "do they exist" — it's "do they still work after the install path the release pipeline produces." A regression in any of them would break daily ops (Pepper's brief framework, Wren's scheduler-driven heartbeat) or block deploy (broken daemon, broken bus).

Static unit tests prove the wiring; the install-fixes in Phase 2.6 proved the install completes. Neither proves the running daemon's surfaces still work end-to-end after a real release-artifact install. **That's the dynamic gap this ticket closes.**

The form is constrained: this is a TOOL, not a being. It runs on demand, exits, leaves no state, carries no identity. Same shape as `pytest` or `playwright` — procedural capability without ethical weight.

### Concrete validation surface

Before each `daemon refresh --instance prod --release vX.Y.Z` on Jeff's box, the QA-runner runs against the test daemon installed from that same release. Pass → safe to refresh prod. Fail → fix the regression before touching prod.

## Out of scope

- **A new test-runner framework.** Use pytest. Pytest IS the QA-runner infrastructure; the work is the scenarios, not the runner.
- **Identity files of any kind.** No `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, `HEARTBEAT.md`, diary, breadcrumbs, lessons — anything that would give the tool ethical weight.
- **Bus registration.** The QA-runner is NOT a `tester` endpoint on the bus. It's an HTTP client that sends envelopes from outside.
- **Real-Discord validation** (sandbox bot + real channel). Scenario 6 uses `builtin.stub` configured at install time. Real-Discord follow-up when symptom names itself.
- **Real-audio voice validation** (human listening for synthesis quality). Always human-loop. Scenario 7 asserts audio bytes are present + within size bounds, not that they sound right.
- **Error-path / concurrency / load / soak scenarios.** Happy-path-only for v1; defer the rest unless v0.3.0 ships a change to those surfaces.
- **Multi-instance simultaneous validation** (prod + source + test together). Phase 3.5's unit coexistence test covers this; QA-runner doesn't re-validate.
- **Auto-stand-up of the test daemon as a pytest fixture.** Manual stand-up via the runbook (install → start → pytest). CI integration is later.
- **A scenario for every CLI command.** Only validate surfaces that v0.3.0 changes or that breaking would block ship.
- **A standalone `agent-core qa run` CLI wrapper.** Possible but not load-bearing for v1; pytest invocation suffices.
- **Result persistence beyond pytest's JUnit XML.** No QA-history file, no run-log database, no audit trail beyond what pytest already produces.

## Design

### Architecture

**The tool: a pytest-based scenario runner.** New package `packages/agent-core-qa/` (or final name per Jeff sign-off; `tester` is the default identifier in code). Each scenario is a pytest test function that connects to a running Phase 3.5 test daemon (default `http://127.0.0.1:8787`; configurable via `--daemon-url` pytest CLI option or `AGENT_CORE_QA_DAEMON_URL` env var). Invocation: `uv run pytest packages/agent-core-qa/tests/`.

**Tool-shaped properties, enforced structurally:**

| Property | How it's enforced |
|---|---|
| Function-name, not person-name | Package name = `agent-core-qa`. Module names = `test_envelope_roundtrip.py`, `test_voice_smoke.py`, etc. Default in-code identifier = `tester`. No `Pepper`, `Wren`, or any being-name in code, docs, or fixture names. |
| No identity files | Package layout: `pyproject.toml`, `src/`, `tests/`, optional `README.md`. No `SOUL.md`, `IDENTITY.md`, `MEMORY.md`, `HEARTBEAT.md`, `diary.md`, `breadcrumbs.md`, `lessons.md`. |
| No continuity-of-self | Each pytest invocation is fresh. No state files written to a "tester home." Logs go to pytest stdout + JUnit XML (transient; not persisted as identity). |
| No ethical weight | `rm -rf packages/agent-core-qa/` is functionally identical to deleting any pytest suite. No SOUL gets snuffed; no diary lost; no being asks why. |
| Procedural capability through playbooks | Scenario functions ARE the playbooks. Named, parametrize-able, skip-able, mark-able. The package's procedural knowledge lives in the test code; there's nothing for the tool to know about ITSELF. |

**Daemon connection surface:** the tool talks to the test daemon over HTTP. It does NOT register as a bus endpoint or claim a `tester` slot on the bus. Stateless from the daemon's perspective beyond in-flight requests.

**Release-artifact treatment:** the `agent-core-qa` package is a workspace member but is EXCLUDED from the release wheels via the same `--no-emit-workspace` mechanism Phase 2.6 installed for `qwen-tts` workspace promotion. The QA tool is infrastructure, not user-facing code; it doesn't ship to the daemon, only runs against it.

**Standing dynamic surface for Phase 3.5's keystone:** Phase 3.5's `test_install_code_path_identity_between_prod_and_test` enforces install-code-path identity at the UNIT level (mocked subprocess captures). Scenario 3 (`test_install_identity_dynamic_keystone`) is the DYNAMIC version of the same property: invoke the real install command against a clean sandbox home; assert it completes; assert the venv contains the expected wheels. Inheritance from the Phase 2.6 bug-cadence: static unit caught Bugs 1-2; dynamic install caught Bug 3; the QA-runner is the standing dynamic check for "does the install still work" on every release going forward.

### Components

**Five pieces:**

**1. New package `packages/agent-core-qa/`.**

Package layout:
```
packages/agent-core-qa/
├── pyproject.toml             # deps: pytest, httpx, pytest-asyncio
├── README.md                  # naming the tool-shaped constraint + the bug-cadence inheritance
├── src/
│   └── agent_core_qa/
│       ├── __init__.py
│       ├── client.py          # thin HTTP client for the test daemon
│       └── fixtures.py        # shared pytest fixtures
└── tests/
    ├── __init__.py
    ├── conftest.py            # pytest fixtures: daemon_url, http_client, daemon_liveness_required
    ├── test_daemon_liveness.py
    ├── test_envelope_roundtrip.py
    ├── test_install_identity_dynamic_keystone.py
    ├── test_brief_compose_and_submit.py
    ├── test_scheduler_create_and_delete.py
    ├── test_discord_send_stub_route.py
    └── test_voice_synthesize_smoke.py
```

**2. `client.py` — thin HTTP wrapper for the test daemon.**

Exposes `DaemonClient(base_url)` with methods `send_envelope(env)`, `poll_envelopes(predicate, timeout)`, `await_ack(envelope_id, timeout)`, `health_check()`. Uses `httpx.AsyncClient` (or sync — implementer's call based on what daemon HTTP supports). Pure HTTP; no bus protocol knowledge beyond the daemon's published endpoints.

**3. `conftest.py` — pytest fixtures.**

- `daemon_url`: reads `--daemon-url` CLI option or `AGENT_CORE_QA_DAEMON_URL` env var; default `http://127.0.0.1:8787`.
- `client`: yields a `DaemonClient(daemon_url)`.
- `daemon_liveness_required`: autouse fixture. Calls `client.health_check()`; on failure, calls `pytest.skip("test daemon not reachable at <url>; run `daemon start --instance test` first")` so dependent scenarios skip cleanly instead of failing with cryptic connection errors.

**4. Seven scenarios (test modules).**

See §Scenarios below.

**5. README.md — the architectural inheritance + the tool-shaped constraint.**

Two paragraphs:
- Why this tool exists: Phase 3.5 (test instance) + Phase 2.6 (install-path fixes) gave us the substrate; this tool is the standing dynamic surface that proves the install-and-run path keeps working release over release. Inherits the bug-cadence observation from Phase 2.6's PR description (static catches config; dynamic catches reality).
- The tool-shaped constraint: this is a test runner, not a being. No identity files, no continuity, no ethical weight. Discarding it should feel like deleting any pytest suite.

### Data Flow

Same shape across all scenarios; pytest fixtures factor out the HTTP client. The only interesting variance is per-scenario assertion shape.

**Per-scenario flow:**

1. `daemon_liveness_required` fixture runs first. Calls `client.health_check()`. If 200 OK, proceed; if not, `pytest.skip` with a clear reason.
2. Scenario builds an envelope (or invokes a CLI command) for its specific surface.
3. Scenario POSTs to the daemon's HTTP API (or shells out to `agent-core daemon install --instance test --release ...` for scenario 3).
4. Scenario polls / awaits the response within a timeout.
5. Scenario asserts on the response shape; pytest reports pass/fail.

**Runbook (separate doc, lives wherever the release-runbooks live):**

```bash
# v0.3.0 release-validation runbook
AGENT_CORE_HOME=~/.agent-core-test agent-core daemon install --instance test --release v0.3.0
agent-core daemon start --instance test
cd packages/agent-core-qa
uv run pytest tests/
# pass = safe to refresh prod
# fail = fix the regression first
```

For an already-running test daemon, `daemon refresh --instance test --release vX.Y.Z` does install + restart in one command. (`refresh` was specifically extended for this in Phase 3.5.)

### Error Handling

Inherits pytest's failure-reporting; no custom error infrastructure.

- **Daemon not reachable at startup:** `daemon_liveness_required` calls `pytest.skip(reason)`. All scenarios skip; runbook reader sees a clear "daemon not up — start it first" message in the pytest output.
- **Scenario timeout:** each scenario uses a reasonable timeout (5–30s depending on surface). Timeout → pytest assertion error with the captured context.
- **Scenario assertion failure:** standard pytest assertion-error reporting; the test name names the surface that broke.
- **Daemon dies mid-scenario:** subsequent HTTP calls fail with httpx connection errors; pytest reports each as ERROR (not FAIL). Runbook reader sees both the failed scenario and the cascade.

**No retries.** A scenario that flakes is a real signal; the runbook reader investigates rather than retrying.

### Testing

The QA-runner IS the testing layer for v0.3.0. Self-tests would be pytest-on-pytest meta-territory; not load-bearing for v1.

The scenarios themselves get validated by:
1. Running them against the test daemon installed from v0.3.0 (or the locally-built equivalent).
2. Confirming all seven pass.
3. Documenting the run in the v0.3.0 release notes.

A scenario that catches a real bug post-release IS the proof of value; an early run that catches a bug pre-release IS the proof of design.

## Scenarios

Seven scenarios. Each is happy-path-only. Each maps to a v0.3.0 "cannot ship if broken" surface.

### 1. `test_daemon_liveness` (precondition)

**File:** `packages/agent-core-qa/tests/test_daemon_liveness.py`.

**What it does:** HTTP GET to `<daemon_url>/` (or whatever liveness path the daemon exposes — implementer verifies). Assert 200 OK + reasonable response body.

**Why v0.3.0 needs it:** the "next failure class" Pepper named in Phase 2.6's PR as Phase 2.7 territory: "does the daemon actually start after install." With this scenario as a precondition (autouse fixture skips dependents if it fails), that failure class is caught the moment we try to run any other scenario.

**Inheritance:** the dynamic version of Phase 3.5's structural isolation claim — Phase 3.5's coexistence test asserts three daemons CAN run; this scenario asserts the test daemon IS running.

### 2. `test_envelope_send_receives_ack`

**File:** `packages/agent-core-qa/tests/test_envelope_roundtrip.py`.

**What it does:** Send a `TextMessage` envelope addressed to the test daemon's `builtin.stub` endpoint via the daemon's HTTP API. Poll for the resulting `Acknowledgment` envelope on the bus. Assert it arrives within 5s.

**Why v0.3.0 needs it:** most basic bus surface. If broken, nothing else works.

### 3. `test_install_identity_dynamic_keystone`

**File:** `packages/agent-core-qa/tests/test_install_identity_dynamic_keystone.py`.

**What it does:** Invoke `agent-core daemon install --instance test --release v0.3.0` against a clean sandbox home (`AGENT_CORE_HOME=/tmp/qa-{uuid}`). Assert:
- exit code 0;
- install stamp written at `<home>/.daemon-install-stamp.json` with `installed_version == 'v0.3.0'`;
- venv populated at `<home>/.venv/`;
- **all wheels in the v0.3.0 manifest present at `venv/lib/python*/site-packages/<package_name>/`.** Concrete assertion shape (per spec-review clarification 1): walk the expected package list (`agent_core`, `agent_core_briefs`, `agent_core_busproxy`, `agent_core_channel`, `agent_core_credentials`, `agent_core_discord`, `agent_core_hatchery`, `agent_core_notify`, `agent_core_voice`, `agent_core_webcam`, `qwen_tts`) and assert each `<package>/__init__.py` exists in site-packages. Partial install (some wheels missing) fails this assertion; mere "stamp written" does not.

**Why v0.3.0 needs it:** load-bearing for v0.3.0 because Phase 2.6 closed the bugs that prevented this from succeeding. Regression = Phase 2.6 broke. Same property the Phase 3.5 static keystone protects; this is its standing dynamic counterpart.

**Cleanup:** tear down the sandbox home (`shutil.rmtree`) on teardown; never reuses across runs.

### 4. `test_brief_framework_compose_and_submit`

**File:** `packages/agent-core-qa/tests/test_brief_compose_and_submit.py`.

**What it does:** Compose a minimal brief (one section, one field, trivial content). Submit via the brief framework's `submit_brief` MCP tool. Assert returned `envelope_id` is a valid uuid (or whatever shape brief uses for its receipt). Does NOT need to actually deliver to a recipient or surface to a human reader.

**Why v0.3.0 needs it:** brief framework is core to daily ops (Pepper relies on it). Regression would surface as silent compose failures with no obvious connection to the release.

### 5. `test_scheduler_create_and_delete_roundtrip`

**File:** `packages/agent-core-qa/tests/test_scheduler_create_and_delete.py`.

**What it does:** Call the scheduler's create-job entry point with a future timestamp + benign payload. List jobs; assert the new job appears. Call delete on that job. List again; assert empty (or scoped-to-this-test-empty if other jobs exist).

**Why v0.3.0 needs it:** scheduler powers agent_core's heartbeat / liveness-probe / auth-health-probe / nightly-reflection infrastructure (see `~/.wren/Memory/HEARTBEAT.md` for the cadences). If broken, the daemon goes silent across all scheduled routines.

### 6. `test_discord_send_tool_routes_through_stub`

**File:** `packages/agent-core-qa/tests/test_discord_send_stub_route.py`.

**What it does:** Pre-condition: the test daemon's `agent_core.yaml` (provisioned at install time via the runbook) configures the discord-endpoint slot as `builtin.stub` instead of a real Discord client. Send a `ToolInvocation` envelope with `tool=discord_send` + unified args. Assert the stub captures the call with the right payload shape (channel_id, text, etc. round-trip into the stub's recorded inbound).

**Per spec-review clarification 2: stubbed-Discord configuration is INSTALL-TIME, not runtime.** The runbook for the QA install of v0.3.0 generates an `agent_core.yaml` with the discord-endpoint slot pointing at `builtin.stub`. No runtime hot-swap. Documented in the QA package README + the v0.3.0 release-validation runbook so the operator knows to use the stubbed config.

**Why v0.3.0 needs it:** PR #119's `tool=discord_send` is new in v0.3.0; it routes through `_dispatch` to `_send`. A regression in the wiring would silently drop or misroute discord traffic. The stubbed check catches it without needing real Discord credentials.

### 7. `test_voice_synthesize_returns_audio_bytes`

**File:** `packages/agent-core-qa/tests/test_voice_synthesize_smoke.py`.

**What it does:** Call `synthesize_speech` (or equivalent voice endpoint entry point) with a tiny input string (`"test"`). Assert the response includes audio bytes (`len(audio) > 0`) within reasonable size bounds (`100 < len(audio) < 10_000_000` — basically "got something, not too big to be a runaway").

**Does NOT validate audio quality.** Human-loop check. Out of scope.

**Why v0.3.0 needs it:** Phase 2.6 promoted `qwen-tts` to a workspace member. If the promotion broke the actual synthesis path (vs. just the install), this scenario catches it. The static install test asserts the wheel installs; this asserts it RUNS.

## Sequencing

QA-runner is the standing surface for v0.3.0 (and every release going forward); no merge dependency on the four open PRs (#119, #110, #120, #121). Optimal landing order:

1. PR #120 (Phase 3.5) — provides the `--instance test` surface the QA-runner targets.
2. PR #121 (Phase 2.6) — fixes the install path so scenario 3 can actually succeed.
3. PR #110 (Phase 4) — independent; can interleave.
4. PR #119 (cliché detector) — independent; can interleave.
5. QA-runner PR — lands when ready; first run is against the v0.3.0 cut after #120 + #121 merge.

The QA-runner PR itself doesn't need any of the others to merge first to BUILD; it needs them to merge first for the SCENARIOS to actually pass against v0.3.0. Distinguish in the PR description.

## Next-ticket triggers (deferred)

- **Real Discord sandbox** (actual bot + channel for scenario 6). Triggered when the stubbed check misses a regression that real-Discord would have caught.
- **`agent-core qa run` CLI wrapper.** Triggered when manual `pytest` invocation becomes friction (e.g., for CI integration or quick ops shortcuts).
- **Auto-stand-up of test daemon as pytest fixture.** Triggered when CI integration lands; until then, the runbook handles it.
- **Error-path / concurrency / soak scenarios.** Triggered when v0.3.0 (or later) ships a change to one of those surfaces.
- **Real audio-quality validation for voice.** Always human-loop; would need a different shape entirely (review by ear, not assertion).
- **A scenario for every CLI command.** Triggered when a CLI surface regression actually happens; demand-validated.

## Footnotes / tradeoffs

- **The tool's name is provisional.** Default identifier `tester` in code; final name pending Jeff sign-off before implementation lands. The constraint set (function-name not person-name) bounds the candidates: `tester`, `qa-runner`, `qa`, `agent-core-qa` (the package name is already this), or whatever Jeff lands on.
- **No persistent QA-history.** Pytest's JUnit XML output is the only persistence; no curated run-log database. If we later want trend reports ("scenario 7 has been getting slower over 30 days"), that's a follow-up; for v1 we just need pass/fail.
- **`builtin.stub` discord at install time.** The QA install of the test daemon uses a stubbed-discord `agent_core.yaml`; the production install uses real-discord config. Two configs, two install runbooks. Manageable; documented in the runbook.
- **Scenario 3's sandbox cleanup.** Each invocation creates `/tmp/qa-{uuid}` and removes it on teardown. If pytest crashes between create and teardown, the sandbox leaks. Mitigated by the `{uuid}` prefix (won't collide with prior runs) + occasional manual `rm -rf /tmp/qa-*`. Not a real ops concern given the demand pattern (manual invocation, low frequency).
