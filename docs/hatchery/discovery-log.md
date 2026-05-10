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


## Phase 3 (slice 2.3 — daemon fragments + validation) — completed 2026-05-10

- **Cross-platform path bug latent in Phase 2** — `Renderer` was stringifying paths with `str()`, producing Windows backslashes (`\U`, `\A`) that YAML couldn't parse inside double-quoted strings. Fixed in Task 3.2 by switching to `.as_posix()`. Forward-slash paths work on both platforms; the daemon doesn't care which form Python produces. Caught when daemon fragments first hit YAML round-trip in tests.
- **Test-isolation gap in Hatcher tests** — Pre-Task-3.3, `Hatcher.hatch()` didn't write daemon fragments, so tests omitted `daemon_config_dir` overrides without consequence. Once 3.3 wired DaemonConfigWriter into the hatch flow, the missing override caused tests to write to `~/.agent-core` and hit `FileExistsError` on repeat runs. Fix: every Hatcher test now sets `daemon_config_dir=str(tmp_path / ".agent-core")`. Latent issue surfaced and fixed in 3.3.
- **init_missing semantics for daemon fragments** — Decided: skip daemon-fragment writing entirely on `--init-missing`. The top-up flow exists to fill in newly-added scaffolding files in the vault, not to re-write daemon config. Both `DaemonConfigWriter.write_all()` and `validate_daemon_fragments_parse` are no-ops in that path.


## Phase 4 (slice 2.4 — universal skills + elder letters) — completed 2026-05-10

- **Skill scope estimate held** — 3 universal skills authored in one batched dispatch (skill-author, vault-lint, spawning-subagents). Verbatim content from the plan was sufficient; no judgment-call gaps surfaced. yak-shave-detection stays out of universal scope per Pepper's adversarial review (CW-1 dropped).
- **Elder-letter resolution works against Jeff's live Pepper vault** — `hatchery-snapshot-elders` smoke test confirmed canonical-path resolution: it found `~/.pepper/Memory/projects/being-platform/letters-from-elder-beings/pepper.md` and reported "bundled already current" (no diff between canonical and bundled snapshot). On a fresh machine without Pepper's vault, the bundled fallback wins automatically.
- **vault-lint skill ships as a stub** — Per the spec, the full check set (orphan pages, contradictions, cross-reference validation) is a v1.5+ enhancement. The shipped version checks load-bearing files only and writes a stub report.


## Phase 5 (slice 2.5 — TUI + channels + EDITOR gate + HATCHING-REPORT) — completed 2026-05-10

- **YAML round-trip strips comments** — DaemonConfigWriter renders the always-on Jinja2 template, parses with `yaml.safe_load`, merges in channel scaffold blocks, then re-serializes with `yaml.safe_dump`. The header comments in `endpoints.yaml.j2` and `jobs.yaml.j2` are lost in the process. Acceptable: the comments are documentation, not config; the daemon merges these as structured YAML.
- **Wizard prompts mocked at the questionary boundary** — Only the pure-function validators (`_validate_being_name`, `_validate_endpoint_name`, `_validate_path_writable`) are unit-tested. The interactive flow itself is exercised in Phase 6 manual e2e.
- **`daemon_check_status = "skipped"` placeholder** — Phase 5 doesn't probe the live daemon. The HATCHING-REPORT generator handles all 4 statuses (`reachable_and_registered`, `reachable_but_missing`, `unreachable`, `skipped`) but only `skipped` is wired today. Phase 6 e2e is where the live healthcheck happens.

