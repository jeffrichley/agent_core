# Spec: wire `format-check` into the `check` gate and clear format drift (issue #433)

## Goal

Add the existing `format-check` recipe to the `check` recipe in `justfile` so that `ruff format --check` is enforced by the CI gate and the pre-push hook on every push and PR. Widen `format-check`'s package list to match `lint`'s. Clear all pre-existing format drift across the workspace in the same PR so the gate passes immediately on merge. Addresses issue #433.

---

## Acceptance criteria

- `just check` includes `format-check` as one of its steps (verified by reading `justfile:24`).
- The `format-check` recipe covers `packages/core packages/agent-core-channel packages/agent-core-hatchery` (matching `lint`'s scope at `justfile:38`).
- `just format-check` exits 0 on the resulting branch (no drift remains in the gated scope).
- `just check` exits 0 on the resulting branch (gate does not regress test/type/lint results).
- The two confirmed-drifted files are format-clean: `packages/agent-core-discord/tests/test_endpoint_inbound.py` and `packages/agent-core-discord/tests/test_access_reload.py` (these are outside the gate scope but clearing them prevents confusion and future breakage if the scope widens).
- No changes to `.github/workflows/ci.yml` — CI already calls `just check`, so wiring `format-check` into `check` is sufficient for CI enforcement.

---

## Approach

No GoF pattern fits. This is configuration plumbing. The "make the right thing easy" principle (Google's engineering canon) applies: compliance should be automatic, not manual. Right now a contributor can push unformatted code, CI stays green, and nobody notices — the gate must own the check.

**Why format sweep first, gate wiring second.** The issue confirmed that `origin/main` already fails `ruff format --check` today. If the justfile change is committed before clearing that drift, the gate immediately fails on main, blocking every subsequent CI run until a human manually fixes it. The correct sequence is: commit the format sweep in one pass (a formatting-only commit), then wire the gate. The Worker must preserve this order within the PR (earlier commit = format sweep, later commit = justfile change).

**Why widen `format-check` to match `lint`.** `lint` already covers `packages/agent-core-hatchery`; leaving `format-check` narrower creates a gap where hatchery code can drift in formatting without the gate catching it. Widening to match `lint` closes that gap consistently.

**Format sweep scope.** Run `ruff format .` from the workspace root. `pyproject.toml`'s `[tool.ruff] extend-exclude` already excludes `.venv`, `.uv-cache-local`, `.tmp`, and `packages/agent-core-voice/vendor` — ruff respects those automatically. The sweep thus covers all workspace packages except the vendored Qwen3-TTS directory, which is correct.

**Ruff version consistency.** The lockfile (`uv.lock`) already pins ruff to `0.15.10` — the version used to verify the drift in the issue. CI installs from the lockfile (`uv sync --locked --all-packages`), so CI's format output is already deterministic. The spec does NOT require changing the `ruff>=0.14` specifier in `pyproject.toml`; the lockfile is the operative pin for determinism.

**No CI workflow changes needed.** `.github/workflows/ci.yml:35` runs `just check`. Wiring `format-check` into `check` automatically flows into CI. The pre-push hook (`.githooks/pre-push`) also delegates entirely to `just check`; it too requires no changes.

---

## Sub-requests (topologically sorted)

1. **Format sweep** — from the workspace root, run:

   ```bash
   uv run --no-sync ruff format .
   ```

   This uses the lockfile-pinned ruff (0.15.10) and respects `extend-exclude` from `pyproject.toml`. Commit the resulting diff as a standalone formatting-only commit. Expect changes in at minimum:
   - `packages/agent-core-discord/tests/test_endpoint_inbound.py` (blank-line after `FakeRawPollVote` docstring, argument-wrapping on `FakeRawMessageDelete.__init__` / `FakeRawMessageUpdate.__init__`)
   - `packages/agent-core-discord/tests/test_access_reload.py` (similar drift)

   There may be additional files. Commit all of them.

2. **Widen `format-check` recipe** — in `justfile`, change line 48 from:

   ```
   format-check:
       uv run --no-sync ruff format --check packages/core packages/agent-core-channel
   ```

   to:

   ```
   format-check:
       uv run --no-sync ruff format --check packages/core packages/agent-core-channel packages/agent-core-hatchery
   ```

3. **Wire `format-check` into `check`** — in `justfile`, change line 24 from:

   ```
   check: lint typecheck contracts test patch-cov
   ```

   to:

   ```
   check: lint format-check typecheck contracts test patch-cov
   ```

   Placement between `lint` and `typecheck` keeps all static-analysis checks before the heavier test+coverage steps, consistent with the existing ordering rationale in the file header comment.

4. **Verify** — run `just format-check` and confirm exit 0. Then run `just check` end-to-end and confirm the gate is green. (The Worker may run these in the Docker environment if the toolchain is available; otherwise the PR description should call out that CI will serve as the verification run.)

---

## File-level changes

| File | Change |
|---|---|
| `justfile` line 24 | **Modify** — add `format-check` to `check` recipe dependencies |
| `justfile` line 48 | **Modify** — add `packages/agent-core-hatchery` to `format-check` recipe |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | **Format-only** — ruff format sweep clears blank-line and argument-wrap drift |
| `packages/agent-core-discord/tests/test_access_reload.py` | **Format-only** — ruff format sweep |
| Other workspace files (if any) | **Format-only** — additional files ruff format identifies as drifted |

No production source files are expected to change; the known drift is in test files. The Worker should verify this by inspecting `git diff --stat` after the sweep.

---

## Alternatives considered

1. **Add `ruff format --check` directly to the `lint` recipe** — consolidates all static checks in one recipe. Ruled out: `lint` maps to `ruff check` (lint rules) and `format-check` maps to `ruff format --check` (formatting rules); conflating them makes the recipe semantics opaque and makes it harder to run one without the other during iteration. The existing separation is intentional and should be preserved.

2. **Widen `format-check` to cover all packages (matching `lint-all`)** — would enforce formatting across the full workspace immediately. Ruled out: the issue explicitly asks to match `lint`'s scope, not `lint-all`'s. Unilaterally widening to all packages may surface formatting issues in packages like `agent-core-discord`, `agent-core-voice`, and `agent-core-inbound` that weren't gated before, requiring a larger sweep that the issue does not scope. YAGNI.

3. **Clear drift only in the packages the gate will cover** — run `ruff format packages/core packages/agent-core-channel packages/agent-core-hatchery` rather than `.`. Ruled out: leaves known drift in `packages/agent-core-discord` on main, which is confusing to future contributors and would become a gate failure if the scope is later widened. The issue explicitly says "run `ruff format` across the repo". A full sweep is cheap and the right call.

---

## Open questions

None. The justfile, CI workflow, format-check recipe, and drift files were all directly read and verified. The lockfile confirms ruff 0.15.10 is already the operative version.

---

## Out of scope

- Widening `format-check` beyond `lint`'s scope to cover all packages (`lint-all`). Follow-on if desired.
- Widening the `format` and `fix` convenience recipes to also cover `packages/agent-core-hatchery`. Those are developer-convenience targets; the gate (`format-check`) is the enforcement mechanism. Widening `fix` / `format` is a separate cleanup.
- Changing the `ruff>=0.14` specifier in `pyproject.toml`. The lockfile already pins 0.15.10 for CI; tightening the specifier is cosmetic and a separate concern.
- Changes to `.github/workflows/ci.yml`. The workflow delegates to `just check`; wiring the recipe is sufficient.
- Changes to `check-fast`. The fast gate omits coverage and is explicitly documented as not enforcing all checks; adding `format-check` there is a separate decision.
