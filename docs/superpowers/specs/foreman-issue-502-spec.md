# Spec: daemon doctor corpse removal via --fix (issue #502)

## Goal

Wire `--fix` in `agent-core daemon doctor` to actually remove the old shared-venv corpses (`~/.agent-core/.venv-v*` and the plain-directory `.venv`) that `find_dead_central_corpses` already detects (C2-3a, #500). Adds `remove_dead_central_corpses` to `venv_gc.py`, updates the `doctor` CLI to call it when `--fix` is set, and updates the output to distinguish removed from found. Issue: https://github.com/jeffrichley/agent_core/issues/502. Design authority: `docs/superpowers/specs/2026-07-14-interpreter-venv-resolution-design.md` (M3).

## Acceptance criteria

- `packages/core/src/agent_core/daemon/venv_gc.py` exports `remove_dead_central_corpses(corpses: list[Path]) -> list[Path]`.
- `remove_dead_central_corpses` calls `shutil.rmtree` for real directories and `Path.unlink` for non-directory paths; paths already absent are silently skipped (idempotent); an `OSError` per path is caught and that path is omitted from the returned list; returns paths actually removed.
- On Python 3.12+, Windows junctions in `corpses` are removed by `shutil.rmtree` without following the junction target (Python 3.12 changed `shutil.rmtree` to treat junctions as files).
- `daemon doctor --fix` calls `remove_dead_central_corpses(venv_report.dead_central_corpses)` when `dead_central_corpses` is non-empty and prints `dead corpse (removed): {path}` for each removed path, `dead corpse (removal failed): {path}` for any that could not be removed, and `→ removed N corpse(s)` when at least one was removed.
- `daemon doctor` (without `--fix`) continues to print `dead corpse: {path}` lines and a hint `→ run with --fix to remove` (the "(C2-3b)" reference in the current hint is dropped — this ticket IS the fix).
- `daemon doctor --fix` does not touch any non-dead-corpse venv GC findings (superseded venvs, broken stable links, orphaned partial builds). Those remain report-only.
- The `doctor` command imports `remove_dead_central_corpses` from `agent_core.daemon.venv_gc` at module level alongside the existing `run_venv_doctor` import.
- Unit tests in `packages/core/tests/test_daemon_venv_gc.py` cover `remove_dead_central_corpses`: removes a versioned dir, removes multiple corpses, removes a plain `.venv` dir, returns empty for an empty input, skips a path that is already absent.
- `packages/core/tests/test_daemon_cli.py` gains one test: `test_doctor_fix_removes_dead_corpses` — monkeypatches `run_venv_doctor` to return a report with one dead corpse, monkeypatches `remove_dead_central_corpses` to record calls and return the corpse list, invokes `doctor --fix`, asserts the removal was attempted and output contains "removed".
- `just check` passes (lint + full test suite with coverage).

## Approach

No GoF pattern applies. Guiding principles: **SRP** — `remove_dead_central_corpses` has one responsibility (delete a list of paths), it is a pure mutation function with no implicit state; **DIP** — it accepts an injected `list[Path]` so tests can use `tmp_path` without touching the real filesystem. This mirrors the exact pattern in `config_hygiene.py`: `find_debris_files` detects; `run_config_hygiene(fix=True)` removes; unit tests for the removal path use `tmp_path`.

**Safety guard is structural, not runtime.** `find_dead_central_corpses` (C2-3a) already excludes the live stable venv from its output: it only flags `.venv-v*` entries (old pre-migration versioned scheme) and `~/.agent-core/.venv` if and only if it is a plain directory (`not .is_symlink() and not .is_junction()`). After the C2-1 migration, the daemon's active `.venv` is a symlink/junction, so it is excluded. `remove_dead_central_corpses` therefore receives only structurally safe paths — no runtime "is this in use?" check is needed.

**`shutil.rmtree` on Python 3.12+.** The project requires Python 3.12+ (per `CLAUDE.md`). Python 3.12 changed `shutil.rmtree` to treat Windows directory junctions as files (removes the junction without following its target). Combined with the fact that `.venv-v*` entries are real directories (not junctions — junctions were only introduced for the new stable `.venv` in C2-1), this makes `shutil.rmtree` the correct and safe removal primitive.

**CLI output follows `config_hygiene.py` precedent.** `HygieneReport` separates `debris_found` from `debris_removed`; the CLI prints "debris (removed)" vs "debris" depending on membership. For corpses, `VenvGcReport` is not mutated — the CLI branches on the `remove_dead_central_corpses` return value directly, which is consistent with the size of the change (no schema change to `VenvGcReport`).

**Only dead corpses get fixed; other venv GC categories remain report-only.** Superseded venvs, broken stable links, and orphaned partial builds all require careful current-venv resolution before pruning (especially on Windows junctions). Those are out of scope for this S-sized ticket; the existing "→ run with --fix (C2-3b)" hints for those categories are updated to remove the ticket reference and say "→ run with --fix to remove" so future work can wire them without a confusing stale reference.

Wait — re-reading the issue: the issue says `--fix` removes the *named corpses* only. The other venv GC hints currently say "(C2-3b)". Since C2-3b was the planned general-fix ticket (which this ticket subsumes for corpses specifically), updating those hints to remove the stale C2-3b reference is clean-up within scope.

## Sub-requests (topologically sorted)

1. **Add `remove_dead_central_corpses` to `packages/core/src/agent_core/daemon/venv_gc.py`.**

   Add `import shutil` to the existing standard-library imports block at the top of the file. Then add the following function after `find_dead_central_corpses`:

   ```python
   def remove_dead_central_corpses(corpses: list[Path]) -> list[Path]:
       """Remove dead central corpse paths returned by find_dead_central_corpses.

       Uses shutil.rmtree for real directories (including Windows junctions on
       Python 3.12+, where rmtree treats junctions as files and does not follow
       their targets). Non-directory paths are removed with Path.unlink. Paths
       already absent are silently skipped (idempotent). OSError (e.g. permission
       denied) is caught per path; that path is not included in the returned list.

       Safety: callers must pass only paths returned by find_dead_central_corpses,
       which structurally excludes live stable symlinks/junctions (.venv that is
       currently in use is a symlink/junction, not a plain directory, so the
       detector never returns it).

       Returns the paths actually removed, in input order.
       """
       removed: list[Path] = []
       for p in corpses:
           try:
               if p.is_dir() and not p.is_symlink():
                   shutil.rmtree(p)
               elif p.exists() or p.is_symlink():
                   p.unlink()
               else:
                   continue  # already absent — idempotent; don't count as removed
           except OSError:
               continue
           removed.append(p)
       return removed
   ```

2. **Update the `from agent_core.daemon.venv_gc import` line in `packages/core/src/agent_core/daemon/cli.py`.**

   Change:
   ```python
   from agent_core.daemon.venv_gc import run_venv_doctor
   ```
   to:
   ```python
   from agent_core.daemon.venv_gc import remove_dead_central_corpses, run_venv_doctor
   ```

3. **Update the `doctor` command body in `packages/core/src/agent_core/daemon/cli.py`.**

   Replace the dead-corpses display block (currently lines 418–421):
   ```python
   if venv_report.dead_central_corpses:
       for path in venv_report.dead_central_corpses:
           console.print(f"  dead corpse: {path}")
       console.print("  → run with --fix (C2-3b) to remove")
   ```
   with:
   ```python
   if venv_report.dead_central_corpses:
       if fix:
           removed_corpses = remove_dead_central_corpses(venv_report.dead_central_corpses)
           removed_set = set(removed_corpses)
           for path in venv_report.dead_central_corpses:
               if path in removed_set:
                   console.print(f"  dead corpse (removed): {path}")
               else:
                   console.print(f"  dead corpse (removal failed): {path}")
           if removed_corpses:
               console.print(f"  → removed {len(removed_corpses)} corpse(s)")
       else:
           for path in venv_report.dead_central_corpses:
               console.print(f"  dead corpse: {path}")
           console.print("  → run with --fix to remove")
   ```

   Also update the stale "(C2-3b)" references in the other venv GC hint lines (lines 424–425, 430–431). Change:
   ```python
   console.print("  → run with --fix (C2-3b) to prune (keeps current + N-1)")
   ```
   to:
   ```python
   console.print("  → run with --fix to prune (keeps current + N-1)")
   ```
   and:
   ```python
   console.print("  → run with --fix (C2-3b) to remove")
   ```
   (the orphaned-partial-builds hint) to:
   ```python
   console.print("  → run with --fix to remove")
   ```

4. **Add `TestRemoveDeadCentralCorpses` to `packages/core/tests/test_daemon_venv_gc.py`.**

   Add this class at the end of the file (after `TestRunVenvDoctor`):

   ```python
   # ---------------------------------------------------------------------------
   # remove_dead_central_corpses
   # ---------------------------------------------------------------------------

   class TestRemoveDeadCentralCorpses:
       def test_removes_versioned_dir(self, tmp_path: Path) -> None:
           from agent_core.daemon.venv_gc import remove_dead_central_corpses

           corpse = tmp_path / ".venv-v0.7.0"
           corpse.mkdir()
           (corpse / "pyvenv.cfg").write_text("home = /usr")

           removed = remove_dead_central_corpses([corpse])
           assert removed == [corpse]
           assert not corpse.exists()

       def test_removes_plain_venv_dir(self, tmp_path: Path) -> None:
           from agent_core.daemon.venv_gc import remove_dead_central_corpses

           plain_venv = tmp_path / ".venv"
           plain_venv.mkdir()

           removed = remove_dead_central_corpses([plain_venv])
           assert removed == [plain_venv]
           assert not plain_venv.exists()

       def test_removes_multiple_corpses(self, tmp_path: Path) -> None:
           from agent_core.daemon.venv_gc import remove_dead_central_corpses

           c1 = tmp_path / ".venv-v0.6.1"
           c2 = tmp_path / ".venv-v0.7.0"
           c1.mkdir()
           c2.mkdir()

           removed = remove_dead_central_corpses([c1, c2])
           assert c1 in removed
           assert c2 in removed
           assert not c1.exists()
           assert not c2.exists()

       def test_empty_list_returns_empty(self) -> None:
           from agent_core.daemon.venv_gc import remove_dead_central_corpses

           assert remove_dead_central_corpses([]) == []

       def test_already_absent_is_silently_skipped(self, tmp_path: Path) -> None:
           from agent_core.daemon.venv_gc import remove_dead_central_corpses

           phantom = tmp_path / ".venv-v0.9.0"  # never created
           removed = remove_dead_central_corpses([phantom])
           assert removed == []  # not counted as removed since it was never there
   ```

5. **Add `test_doctor_fix_removes_dead_corpses` to `packages/core/tests/test_daemon_cli.py`.**

   Add this test after `test_doctor_reports_venv_gc_section`:

   ```python
   def test_doctor_fix_removes_dead_corpses(
       tmp_path: Path, monkeypatch: pytest.MonkeyPatch
   ) -> None:
       """daemon doctor --fix calls remove_dead_central_corpses for detected corpses."""
       monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

       from agent_core.daemon.venv_gc import VenvGcReport

       corpse_path = tmp_path / ".venv-v0.7.0"
       fake_venv_report = VenvGcReport(dead_central_corpses=[corpse_path])
       monkeypatch.setattr(
           "agent_core.daemon.cli.run_venv_doctor",
           lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
       )

       removal_calls: list[list] = []

       def fake_remove(corpses: list) -> list:
           removal_calls.append(list(corpses))
           return list(corpses)

       monkeypatch.setattr(
           "agent_core.daemon.cli.remove_dead_central_corpses",
           fake_remove,
       )
       monkeypatch.setattr(
           "agent_core.daemon.cli.run_config_hygiene",
           lambda config_dir, fix: __import__(
               "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
           ).HygieneReport(),
       )

       result = runner.invoke(daemon_app, ["doctor", "--fix"])
       assert result.exit_code == 1  # has_issues remains True (corpses were found)
       assert removal_calls == [[corpse_path]]  # called exactly once with the detected corpse
       assert "removed" in result.stdout.lower()
   ```

## File-level changes

| File | Change |
|------|--------|
| `packages/core/src/agent_core/daemon/venv_gc.py` | **Modify** — add `import shutil`; add `remove_dead_central_corpses(corpses: list[Path]) -> list[Path]` after `find_dead_central_corpses` |
| `packages/core/src/agent_core/daemon/cli.py` | **Modify** — extend `venv_gc` import to include `remove_dead_central_corpses`; replace dead-corpses display block in `doctor` with fix-aware version; update stale "(C2-3b)" hint strings in superseded-venvs and orphaned-partial-builds sections |
| `packages/core/tests/test_daemon_venv_gc.py` | **Modify** — add `TestRemoveDeadCentralCorpses` class (5 test cases) |
| `packages/core/tests/test_daemon_cli.py` | **Modify** — add `test_doctor_fix_removes_dead_corpses` test |

## Alternatives considered

1. **Add `dead_central_corpses_removed: list[Path]` field to `VenvGcReport` and handle removal inside `run_venv_doctor`.** Would mirror `HygieneReport.debris_removed`/`run_config_hygiene(fix=True)` more exactly. Ruled out — `VenvGcReport`'s docstring explicitly says "Report-only — no mutations"; the report and its callers were designed in C2-3a to be side-effect-free. Mixing detection and mutation in `run_venv_doctor` would make the module harder to test and violate the contract established in C2-3a. A separate `remove_dead_central_corpses` function with injected-path arguments is more testable and consistent with the "detectors vs. mutators" split.

2. **Implement `--fix` for ALL venv GC categories (superseded venvs, broken links, orphaned partial builds, corpses) in this ticket.** The issue explicitly scopes to corpses only (size S). Superseded-venv pruning requires stable symlink resolution to avoid deleting a running venv; broken-link removal and orphaned-build pruning have different risk profiles. Implementing all four in a single S ticket would over-scope and risks missing edge cases. Corpses are structurally safe to remove (the detector already enforces the live-link exclusion); the others are not.

3. **Use `os.unlink`/`os.rmdir` instead of `shutil.rmtree`.** `os.rmdir` only removes empty directories — venv directories contain many files. `shutil.rmtree` is the correct primitive for recursive removal. The Python 3.12 junction-safety property (`shutil.rmtree` treats junctions as files) makes it appropriate for both POSIX symlinks and Windows junctions.

## Open questions

None. The integration point is `daemon/cli.py:doctor`, the detector (`find_dead_central_corpses`) is confirmed to be in `venv_gc.py` (C2-3a, issue #500), the Python version is confirmed 3.12+ (per `CLAUDE.md`), and the `config_hygiene.py` pattern is the established precedent for find+fix pairs in the doctor subsystem.

## Out of scope

- `--fix` for superseded versioned venvs under `~/.<being>/.agent-core/venvs/` — requires stable-symlink resolution before pruning; deferred.
- `--fix` for broken stable symlinks/junctions — requires decision on whether to remove or repair; deferred.
- `--fix` for orphaned partial builds — deferred.
- Auto-repairing drifted `.mcp.json` files via `--fix` — use `agent-core venv regen-mcp <being>`; deferred.
- Logging (structured) of removed paths — the Rich console output is sufficient for the S-size scope.
