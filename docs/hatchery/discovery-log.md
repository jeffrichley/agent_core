# Hatchery discovery log

Append-only record of items the spec didn't anticipate, discovered during implementation. Each phase's checkpoint adds an entry.

## Phase 1 (slice 2.1 — agent-core/core conf.d) — completed 2026-05-10

Landed as PR #77 (commit `e385077` on main).

- **Endpoint-collision error class translation** — Spec assumed existing endpoint registration would raise `BusBootError` on duplicate name. Reality: `Bus.register()` raises generic `ValueError`. Wrapped the call in `build_bus_from_config` to translate `ValueError → BusBootError` so collisions surface as a specific boot-failure class. Small scope addition to PR-1; documented in commit `b9cf3dd` body.
- **Subagent commit-message artifact** — One commit (`b9cf3dd`) has a stray `@` in subject line from PowerShell heredoc. Cosmetic only; full commit content correct. Future subagent dispatches should use bash-style heredocs.
- **Pyproject stub for hatchery package** — Pepper's source-material commit on main added `packages/agent-core-hatchery/` (docs + templates-draft) without a pyproject.toml. The workspace glob `packages/*` requires one. Added a minimal stub in PR-1 (`commit 09a685e`) so `uv sync` resolves; Phase 2 Task 2.1 expanded it with real deps + entry points.

## Phase 2 (slice 2.2 — hatchery skeleton + memory templates) — completed 2026-05-10

- **`endpoint_name` in HatchConfig** — Spec described `endpoint_name` as both a regular field with a default AND a computed property that resolves to `being_name_lower` when unset. Implementer chose `model_validator(mode="after")` over `computed_field` + alias because pydantic 2 computed fields are read-only and the alias setup gets awkward. Result is cleaner: a normal mutable field with default-resolution at validation time.
- **Missing template `_being_/handoff.md`** — `file-classes.yaml` listed `memory/_being_/handoff.md` under the `system:` class but the templates-draft didn't include it. Implementer created an empty template (the file is daemon-managed; empty seed is correct per spec). Caught during Task 2.7 hatcher integration test.
- **Cross-platform path regex escaping** — `pytest.raises(VaultExistsError, match=str(vault_path))` in test_hatcher_basic.py fails on Windows because backslashes in paths are invalid regex escape sequences. Fixed with `re.escape(...)`.
- **File-class manifest gained `memory/**/.gitkeep` under system** — Walking templates with rglob picks up `.gitkeep` markers; they need a class. Treated as system (auto-managed sentinel). Also added the 4 new zone READMEs (projects/people/ideas/dreams) under reference.
- **Pre-existing monorepo pytest issue** — Running `uv run pytest` at the repo root fails with a conftest collision between `agent-core-discord/tests/conftest.py` and `agent-core-webcam/tests/conftest.py` (both register as `tests.conftest`). Not caused by hatchery work. Workaround: run each package's test suite separately. All packages pass individually (1237 tests total, 0 failures across the repo).

