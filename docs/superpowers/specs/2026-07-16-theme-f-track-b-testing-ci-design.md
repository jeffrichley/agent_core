# Theme F Track B — Testing / CI tech-debt: design

**Epic:** #262 · **Theme:** #269 (Theme F — Quality, testing & documentation) · **Track:** B
**Companion:** Track A (#389 spec; tickets #390–398) covered the adopter-facing docs + PyPI launch. Track B is the testing/CI tech-debt slice.
**Date:** 2026-07-16 · **Author:** Wren

## Goal

Pay down the "Testing / CI & tech-debt" bucket of the [world-class eval](https://github.com/jeffrichley/agent_core/blob/main/docs/world-class-eval-2026-07-13.md) to a uniform, Google-standard bar: hermetic release-gating that runs in CI, `mypy --strict` across all 12 packages, no god-modules, no copy-pasted audit code, no silent exception swallows.

## Current state (verified against `main`, 2026-07-16)

The eval is dated 2026-07-13; the following was re-verified against current `main` before this spec. Deltas from the eval are called out.

- **Flaky tests (shrank from the eval).** `kill_tree()` exists at `packages/core/src/agent_core/daemon/supervisor.py:41-57` and **is** used by the daemon `stop` command (`daemon/cli.py:188`). The eval's "helper exists but nobody calls it" finding is **resolved**. Residual debt: two real-subprocess tests are **not** `slow`-marked and run in the default `-n auto` lane during `just check`:
  - `packages/core/tests/test_daemon_cli.py:71` — `test_start_writes_pid_file_and_stop_kills` (spawns a real daemon subprocess; real `time.sleep` polling).
  - `packages/core/tests/test_daemon_supervisor.py:55` — `test_kill_tree_terminates_subprocess` (real `subprocess.Popen`).
- **Pytest config.** Root `pyproject.toml` `[tool.pytest.ini_options]`: `addopts` includes `-m 'not slow' --cov=packages --cov-branch --cov-fail-under=85 -n auto --dist=loadscope`; `slow` marker **is** defined; `timeout = 60`. `testpaths` = 8 dirs (core, credentials, discord, channel, inbound, webcam, voice, busproxy) — **briefs, hatchery, qa, notify are NOT collected**. `just test` = `uv run --no-sync pytest -q` (the eval's `-n 0` claim is **stale**).
- **Release gate.** `packages/agent-core-qa/` exists; referenced by **no** workflow; not in `testpaths`. `conftest.py:45-71` autouse fixture `daemon_liveness_required` skips every scenario unless `http://127.0.0.1:8787` is already live. 7 scenarios: liveness, envelope round-trip, install-identity keystone (does a **second real install**, torch 5–10 min cold), brief compose+submit, scheduler CRUD, discord send-stub route, voice synthesize smoke.
- **notify.** `packages/notify/` = `mcp_server.py` (314 ln) + 1-line `__init__.py`. No tests dir, not in `testpaths`, not in `[tool.mypy] files`.
- **mypy.** `[tool.mypy] files = ["packages/core/src", "packages/agent-core-channel/src"]` — 2 of 12. The 12: agent-core-briefs, agent-core-busproxy, agent-core-channel, agent-core-discord, agent-core-hatchery, agent-core-inbound, agent-core-qa, agent-core-voice, agent-core-webcam, core, credentials, notify.
- **God-modules.** `packages/agent-core-discord/src/agent_core_discord/endpoint.py` = **2152 ln**, untyped, thin tests. `packages/core/src/agent_core/endpoints/claude_code_mcp.py` = **1153 ln**; `if self._handle is None:` not-started guard repeated **7×** (4 `raise RuntimeError`, 3 `return {"status":"error",...}`).
- **audit.py.** Four files: briefs (125 ln), voice (79), webcam (75), inbound (67). **briefs/voice/webcam near-identical** (`AuditEvent` frozen dataclass + `AuditLog` with async `write` → `asyncio.to_thread(_append_line)` swallowing all exceptions + identical `_append_line`/`_serialize`). **inbound diverges** (sync, injectable clock, `record_allow`/`record_deny`). No shared base in `core`.
- **Minor.** ~24 `except …: pass` swallows (8 in discord `endpoint.py`; 2 in `supervisor.py` are legitimate `psutil.NoSuchProcess` guards). The eval's "no-op stub test" is **stale** (not found). The eval's "untested email-send path" is **stale** (now mock-covered); the genuine gap is the real `get_client()` factory in `email/client.py:24`, always mocked.

## CI inventory

- **`ci.yml`** — PR + push-to-main + dispatch. `check` job (ubuntu+windows matrix): `just check` = lint + typecheck + contracts + full pytest (85% cov gate); Codecov on Linux; `diff-cover --fail-under=80` on PRs. `slow-tests-windows` job (**windows-only**): `pytest packages/core/tests packages/agent-core-busproxy/tests -m slow --no-cov`.
- **`release.yml`** — on Release `published`: builds wheels, exports pinned `requirements.txt`, uploads to the release. **Runs no tests.**
- `release-please.yml`, `pr-title-lint.yml`, `renovate.yml`.

## Decisions

- **D1 — Full bucket in one track.** All 7 eval items land in Track B, including both god-module splits and mypy-across-all-12. (Rejected: P1-only or defer-the-splits — Jeff chose comprehensive.)
- **D2 — Discord split follows legacy-refactor discipline: characterize → move → type.** Coverage is too thin for the type-checker alone to be a safety net (mypy is blind to behavioral change — reordered awaits, error-shape changes, routing-key drift). Write golden-master characterization tests pinning current observable behavior first; carve into modules behind a **byte-identical public class surface** as move-only commits (separate from any logic change); then `mypy --strict` per carved module. The characterization suite becomes discord's permanent regression suite (closing the "discord untested" line item too). (Rejected: split-first-then-type — inadequate net; type-first-monolith — unreviewable annotation diff over code about to move.)
- **D3 — Release gate: two gates, one hermetic harness.** Shift-left + hermeticity. Build a session-scoped fixture that **auto-starts a source-installed daemon** (replacing the skip-unless-hand-started autouse). Run the fast qa scenarios **per-PR in `ci.yml`**. The heavy install-keystone (scenario 3, real torch install) stays in a release/nightly lane, not per-PR. Track A **#394** keeps the **published-install** variant at release time, reusing the same fixture. (Rejected: published-only — misses PR-time breakage; source-only — never validates the shipped artifact.)
- **D4 — mypy: uniform `--strict` across all 12, no permanent second tier.** Currently `files` = core + channel. `claude_code_mcp.py` lives **inside** `packages/core/src`, so it is **already** under core's `--strict` scope (its debt is length + the 7× guard, not typing). The genuinely untyped packages are the other 10. Type the small/mid ones `--strict` directly; type **discord through its split** (per module) so no relaxed discord bar is ever committed. The relaxed-then-ratchet approaches calcify; this reaches the strict end-state directly. mypy already runs in `just check`, so it can't regress once each package is in `files`.
- **D5 — audit hoist scope.** Hoist a generic `JsonlAuditLog` base into `core` (owns `write`/`_append_line`/`_serialize` + the swallow policy, parameterized on an event→dict serializer). briefs/voice/webcam subclass it. **inbound stays separate** (different design); it may adopt `_append_line` only. Char-test the append path so the refactor is behavior-preserving.
- **D6 — Slow-lane must cover Linux.** Marking the two subprocess tests `slow` removes them from the default lane; the existing slow-tests job is Windows-only, so without a Linux slow lane these daemon tests (process-tree behavior differs by OS) would run **nowhere** on Linux. B1 adds a Linux slow lane.
- **D7 — Swallow policy.** Replace unlogged `except: pass` with a logged warning at the appropriate level. Keep genuinely intentional guards (the 2 `psutil.NoSuchProcess` reap guards in `supervisor.py`) — but comment them so intent is explicit. Discord's 8 swallows are handled inside B6 (the file is being rewritten); B8 covers the rest.

## Ticket slice

Eight tickets. B1–B3 are P1 stabilizers (independent, ship first); B4–B8 are P2 structural.

### B1 · [P1] Kill the pre-push flake + Linux slow lane
Mark `test_start_writes_pid_file_and_stop_kills` (`test_daemon_cli.py:71`) and `test_kill_tree_terminates_subprocess` (`test_daemon_supervisor.py:55`) `@pytest.mark.slow`. Add a Linux slow-tests CI job (or make `slow-tests` a matrix over ubuntu+windows) covering `packages/core/tests packages/agent-core-busproxy/tests -m slow`.
**Acceptance:** `just check` no longer collects real-subprocess tests; the slow lane runs them on **both** OSes; no stranded daemon processes after a CI run.

### B2 · [P1] agent-core-notify: tests + mypy
Create `packages/notify/tests/`; add to `testpaths`; write tests for `mcp_server.py` (314 ln) to the package coverage bar; add `packages/notify/src` to `[tool.mypy] files` at `--strict` and fix annotations.
**Acceptance:** notify collected in CI and counted toward coverage; `mypy --strict` clean for notify.

### B3 · [P1] Release-gate in CI (hermetic)
Replace the skip-unless-hand-started autouse with a **session-scoped fixture that auto-starts a source-installed daemon** (`agent-core daemon start --instance test`, health-poll, teardown kills the tree). Add a `ci.yml` job (`release-gate`) that runs the **fast 6** scenarios per-PR against that daemon. Keep the install-keystone (scenario 3) out of the per-PR lane — run it in a nightly/`workflow_dispatch` (and Track A #394's release lane). Fixture lives where both B3 and #394 can import it.
**Acceptance:** a PR runs the 6 fast qa scenarios with **no** hand-started daemon; a broken daemon/round-trip path fails the PR; no process leak on teardown.

### B4 · [P2] audit.py → `JsonlAuditLog` in core
Add `JsonlAuditLog` (+ any shared `AuditEvent` protocol) to `packages/core`. Refactor briefs/voice/webcam `audit.py` to subclass it, keeping each domain's event fields. Leave inbound separate. Characterization test the append/serialize path before and after.
**Acceptance:** one base in `core`; briefs/voice/webcam subclass with no behavior change; existing audit tests green; `--strict` clean.

### B5 · [P2] mypy the 10 small/mid packages at `--strict`
Add the untyped small/mid packages (briefs, busproxy, hatchery, inbound, voice, webcam, qa, credentials — notify lands via B2) to `[tool.mypy] files` at `--strict`; fix annotations. Sequence after B4 to avoid re-annotating churned audit code. (`claude_code_mcp` is already in core's scope — not part of this ticket.)
**Acceptance:** 11 of 12 packages `mypy --strict` clean in CI (only discord remains, landing via B6).

### B6 · [P2·L] Split discord `endpoint.py` (2152 ln)
Per D2: (1) characterization tests pinning current behavior — tool-routing table, lifecycle transitions, the not-started error shapes, audit emissions; (2) move-only carve into cohesive modules (candidate seams: gateway/lifecycle, tool-routing, command handlers, audit) behind an **identical** public `DiscordEndpoint` surface; (3) `mypy --strict` per module + add `packages/agent-core-discord/src` to `[tool.mypy] files`; (4) fold in discord's 8 `except:pass` swallows (D7). Commits separate move from change.
**Acceptance:** no carved module materially oversized (target ≤ ~500 ln); public surface unchanged (downstream imports untouched); characterization suite + `--strict` green; discord in mypy.

### B7 · [P2] Split `claude_code_mcp.py` (1153 ln) + collapse the 7× guard
Introduce `_require_handle()` (raising form) and a small guard/decorator for the tool-dict-returning form to collapse the 7 repeated not-started guards. Carve into cohesive modules behind the identical public surface. Already under core's `--strict` scope, so keep it green through the split (no new mypy `files` entry).
**Acceptance:** the 7× guard is a single helper (raising) + one guard (dict form); module split with unchanged public surface; core `--strict` stays clean.

### B8 · [P2·S] Log the swallows + email factory test
Replace the remaining ~14 unlogged `except: pass` (non-discord) with logged warnings; annotate the 2 legit `psutil` guards. Add a test for the real `get_client()` factory in `email/client.py`.
**Acceptance:** no unlogged bare-`pass` swallow outside a commented, justified guard; `get_client()` has a direct test.

## Dependencies & sequencing

- **Roots (no deps):** B1, B2, B3, B4, B7, B8.
- **B5** after **B4** (avoid churning audit annotations twice).
- **"All 12 `--strict`" milestone** = B5 (→ 11) ∧ B6 (→ discord = 12). `claude_code_mcp`/B7 is already in core's scope, not on the critical path for the milestone.
- **B6** absorbs discord's swallows, so **B8** is non-discord only.
- P1 stabilizers (B1/B2/B3) ship first; the two big splits (B6/B7) are independent and can run in parallel worktrees.

## Priority & foreman posture

All tickets are P1/P2 (the Theme F P0s were the Track A docs). Per standing directive: file with `world-class-eval`, sub-issue under #269, wire `blocked_by`, and **hold for Jeff's greenlight trigger** — do **not** apply `foreman:plan`.

## Global constraints (for the implementation plan)

- Python 3.13; `uv` workspace; ruff (google docstrings) + `mypy --strict`; pytest.
- Canon gates unchanged: 85% coverage floor, `diff-cover --fail-under=80` on PRs, contracts, lint, typecheck all in `just check`.
- Refactors are **behavior-preserving**: public surfaces (class names, method signatures, envelope/tool shapes) stay identical; move-only commits separated from logic commits.
- agent_core commits use **no** `Co-Authored-By` trailer (repo convention).
- Bare worktree-host: work in `.worktrees/`; fresh worktree needs `uv sync --dev`; pre-push hook runs `just check`.
