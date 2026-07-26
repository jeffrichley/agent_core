# Spec: daemon doctor --fix: prune superseded venvs / junctions / build dirs / drifted .mcp.json (issue #501)

## Goal

Wire `--fix` pruning actions for the four venv-GC detector categories that C2-3a left report-only: superseded versioned venvs, broken stable links, orphaned partial builds, and drifted `.mcp.json`. Add three pruner functions (`prune_superseded_venvs`, `remove_broken_stable_link`, `remove_orphaned_partial_builds`) to `packages/core/src/agent_core/daemon/venv_gc.py`, import `repair_mcp_json` from the existing C2-2 module, and update the `doctor` command's venv-GC section so that `--fix` acts on each category. Dead central corpse removal is already wired (C2-3a). Report-only remains the default behaviour. Design authority: `docs/superpowers/specs/2026-07-14-interpreter-venv-resolution-design.md` (M3) and the C2-3a spec for issue #500.

## Acceptance criteria

- `packages/core/src/agent_core/daemon/venv_gc.py` gains three new public functions:
  - `prune_superseded_venvs(paths: list[Path]) → list[Path]` — `shutil.rmtree` per path; silently skips already-absent paths and per-path `OSError`; returns paths actually removed in input order.
  - `remove_broken_stable_link(stable: Path) → bool` — only acts when `stable` is a symlink/junction whose target does **not** exist; calls `os.unlink`; returns `True` if removed, `False` for absent paths, healthy links, plain directories, or `OSError`.
  - `remove_orphaned_partial_builds(paths: list[Path]) → list[Path]` — same contract as `prune_superseded_venvs`: `shutil.rmtree`, idempotent, returns removed.
- When `doctor --fix` is invoked, all four venv-GC categories act:
  - **Superseded venvs**: calls `prune_superseded_venvs`; prints `superseded venv (removed): <path>` or `superseded venv (removal failed): <path>` per entry.
  - **Broken stable links**: calls `remove_broken_stable_link` per path; prints `broken stable link (removed): <path>` plus a rebuild hint on success, or `broken stable link (removal failed): <path>` on failure.
  - **Orphaned partial builds**: calls `remove_orphaned_partial_builds`; prints `orphaned partial build (removed): <path>` or `orphaned partial build (removal failed): <path>` per entry.
  - **Drifted `.mcp.json`**: calls `repair_mcp_json` from `agent_core.venv.mcp_config` (C2-2 / #316); derives `being_name` as `path.parent.name.lstrip(".")`; prints `drifted .mcp.json (repaired): <path>` or `drifted .mcp.json (already canonical): <path>`.
- Without `--fix`, the report-only messages for superseded venvs, orphaned partial builds, and drifted `.mcp.json` are unchanged. The broken-stable-link report-only message **gains** a `→ run with --fix to remove` hint (currently absent, inconsistent with other categories).
- `prune_superseded_venvs` is idempotent: running `find_superseded_venvs` after a pruning pass returns `[]` (only two dirs remain, which is the keep threshold).
- Unit tests in `packages/core/tests/test_daemon_venv_gc.py` cover: each new pruner function's happy path; the already-absent idempotency case; and the `test_idempotent_second_pass` scenario (detect → prune → detect again → empty).
- `packages/core/tests/test_daemon_cli.py` gains four new tests: one per pruning category, each monkeypatching the relevant pruner to capture its call arguments.
- `just check` passes (lint, typecheck, full test suite, coverage).

## Approach

No GoF pattern. **SRP** — the three pruner functions live in `venv_gc.py` alongside `remove_dead_central_corpses` (the C2-3a pruner), keeping all GC mutation logic in one pure-functions module. The CLI delegates; it does no inline deletion. **DIP** — pruners accept pre-detected `list[Path]` so they are testable without invoking `run_venv_doctor`.

**Imports already in place.** `venv_gc.py` already imports `import shutil` and `import os` (lines 17–18, added in C2-3a). No new imports are needed in `venv_gc.py`. `repair_mcp_json` is imported as a module-level import in `cli.py`, consistent with how all other module-level CLI imports are structured; unlike `find_drifted_mcp_json`'s lazy-import pattern, the CLI entry point tolerates eager imports.

**`being_name` derivation.** `drifted_mcp_jsons` entries are `vault_root / ".mcp.json"` where `vault_root == being_home == ~/.<being_name>`. Reversing: `path.parent.name.lstrip(".")` recovers the being name. This derivation is reliable for standard being homes (those discovered by `discover_being_homes`).

**`remove_broken_stable_link` safety.** The function's guard is identical to `find_broken_stable_link`'s: only a symlink/junction whose `stable.exists()` is `False` qualifies. A plain directory or a healthy symlink is never touched. `os.unlink` removes a dangling symlink without following its (absent) target; it also removes Windows junctions (Python 3.12+ `os.unlink` on a junction removes the junction entry, not its target). This matches the pattern used by `atomic_repoint` in `venv/builder.py` (lines 193–195).

**Report-only path untouched.** The dead-corpse `--fix` block (lines 419–432 of `cli.py`) is not modified by this ticket.

## Sub-requests (topologically sorted)

1. **Add `prune_superseded_venvs` to `venv_gc.py`** — insert after the existing `remove_dead_central_corpses` function (after line 278):
   ```python
   def prune_superseded_venvs(paths: list[Path]) -> list[Path]:
       """Remove superseded versioned venv directories.

       Uses shutil.rmtree. Already-absent paths are silently skipped (idempotent).
       A per-path OSError (e.g. permission denied) is caught; that path is excluded
       from the returned list. Returns paths actually removed, in input order.
       """
       removed: list[Path] = []
       for p in paths:
           try:
               if p.exists():
                   shutil.rmtree(p)
               else:
                   continue  # already absent — idempotent, not counted
           except OSError:
               continue
           removed.append(p)
       return removed
   ```

2. **Add `remove_broken_stable_link` to `venv_gc.py`** — insert after `prune_superseded_venvs`:
   ```python
   def remove_broken_stable_link(stable: Path) -> bool:
       """Remove a broken (dangling) stable symlink or junction.

       Only removes if stable is a symlink/junction AND its target does not exist.
       Returns True if removed; False if the path is absent, is a plain directory,
       points to an existing target, or if removal raised OSError.
       """
       is_link = stable.is_symlink() or (sys.platform == "win32" and stable.is_junction())
       if not is_link:
           return False
       if stable.exists():
           return False  # target exists — healthy link, do not touch
       try:
           os.unlink(stable)
           return True
       except OSError:
           return False
   ```

3. **Add `remove_orphaned_partial_builds` to `venv_gc.py`** — insert after `remove_broken_stable_link`:
   ```python
   def remove_orphaned_partial_builds(paths: list[Path]) -> list[Path]:
       """Remove orphaned partial build directories.

       Identical contract to prune_superseded_venvs: shutil.rmtree per path,
       already-absent paths silently skipped (idempotent), per-path OSError silently
       skipped. Returns paths actually removed, in input order.
       """
       removed: list[Path] = []
       for p in paths:
           try:
               if p.exists():
                   shutil.rmtree(p)
               else:
                   continue
           except OSError:
               continue
           removed.append(p)
       return removed
   ```

4. **Update the import line in `cli.py`** (line 56) and add `repair_mcp_json`. Change:
   ```python
   from agent_core.daemon.venv_gc import remove_dead_central_corpses, run_venv_doctor
   ```
   to:
   ```python
   from agent_core.daemon.venv_gc import (
       prune_superseded_venvs,
       remove_broken_stable_link,
       remove_dead_central_corpses,
       remove_orphaned_partial_builds,
       run_venv_doctor,
   )
   ```
   Add this import after the existing `daemon.*` imports and before the `supervisor` import (or alongside the other `venv.*`-adjacent imports at the top of the file):
   ```python
   from agent_core.venv.mcp_config import repair_mcp_json
   ```

5. **Wire superseded venvs `--fix` in `cli.py`** — replace lines 433–436 (the four report-only lines):
   ```python
   if venv_report.superseded_venvs:
       if fix:
           removed = prune_superseded_venvs(venv_report.superseded_venvs)
           removed_set = set(removed)
           for path in venv_report.superseded_venvs:
               label = "(removed)" if path in removed_set else "(removal failed)"
               console.print(f"  superseded venv {label}: {path}")
           if removed:
               console.print(f"  → pruned {len(removed)} superseded venv(s)")
       else:
           for path in venv_report.superseded_venvs:
               console.print(f"  superseded venv: {path}")
           console.print("  → run with --fix to prune (keeps current + N-1)")
   ```

6. **Wire broken stable links `--fix` in `cli.py`** — replace lines 437–439 (three report-only lines). Note: the report-only path also gains a `→ run with --fix` hint here, since it was previously the only category without one:
   ```python
   if venv_report.broken_stable_links:
       if fix:
           for path in venv_report.broken_stable_links:
               if remove_broken_stable_link(path):
                   console.print(f"  broken stable link (removed): {path}")
                   console.print(
                       "  → rebuild with [bold]agent-core venv build <being>[/bold]"
                   )
               else:
                   console.print(f"  broken stable link (removal failed): {path}")
       else:
           for path in venv_report.broken_stable_links:
               console.print(f"  broken stable link: {path}")
           console.print(
               "  → run with --fix to remove; then rebuild with "
               "[bold]agent-core venv build <being>[/bold]"
           )
   ```

7. **Wire orphaned partial builds `--fix` in `cli.py`** — replace lines 440–443:
   ```python
   if venv_report.orphaned_partial_builds:
       if fix:
           removed = remove_orphaned_partial_builds(venv_report.orphaned_partial_builds)
           removed_set = set(removed)
           for path in venv_report.orphaned_partial_builds:
               label = "(removed)" if path in removed_set else "(removal failed)"
               console.print(f"  orphaned partial build {label}: {path}")
           if removed:
               console.print(f"  → removed {len(removed)} orphaned partial build(s)")
       else:
           for path in venv_report.orphaned_partial_builds:
               console.print(f"  orphaned partial build: {path}")
           console.print("  → run with --fix to remove")
   ```

8. **Wire drifted `.mcp.json` `--fix` in `cli.py`** — replace lines 444–447:
   ```python
   if venv_report.drifted_mcp_jsons:
       if fix:
           for path in venv_report.drifted_mcp_jsons:
               being_name = path.parent.name.lstrip(".")
               _, changed = repair_mcp_json(
                   being_name,
                   vault_root=path.parent,
                   daemon_config_dir=home,
               )
               label = "(repaired)" if changed else "(already canonical)"
               console.print(f"  drifted .mcp.json {label}: {path}")
       else:
           for path in venv_report.drifted_mcp_jsons:
               console.print(f"  drifted .mcp.json: {path}")
           console.print(
               "  → run [bold]agent-core venv regen-mcp <being>[/bold] to repair"
           )
   ```

9. **Update the comment on line 410 in `cli.py`** — replace:
   ```python
   # Venv GC section (C2-3a: report-only, --fix is not yet wired here)
   ```
   with:
   ```python
   # Venv GC section (C2-3a detectors; C2-3b --fix actions wired)
   ```

10. **Add `TestPruneSupersededVenvs` to `test_daemon_venv_gc.py`** — append after the existing `TestRemoveDeadCentralCorpses` class:
    ```python
    # ---------------------------------------------------------------------------
    # prune_superseded_venvs
    # ---------------------------------------------------------------------------

    class TestPruneSupersededVenvs:
        def test_removes_single_dir(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import prune_superseded_venvs

            d = tmp_path / "0.6.0"
            d.mkdir()
            (d / "pyvenv.cfg").write_text("home = /usr")
            removed = prune_superseded_venvs([d])
            assert removed == [d]
            assert not d.exists()

        def test_removes_multiple_dirs(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import prune_superseded_venvs

            d1 = tmp_path / "0.5.0"
            d2 = tmp_path / "0.6.0"
            d1.mkdir()
            d2.mkdir()
            removed = prune_superseded_venvs([d1, d2])
            assert d1 in removed
            assert d2 in removed
            assert not d1.exists()
            assert not d2.exists()

        def test_already_absent_is_idempotent(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import prune_superseded_venvs

            phantom = tmp_path / "0.5.0"  # never created
            assert prune_superseded_venvs([phantom]) == []

        def test_empty_list_returns_empty(self) -> None:
            from agent_core.daemon.venv_gc import prune_superseded_venvs

            assert prune_superseded_venvs([]) == []

        @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
        def test_idempotent_second_pass(self, tmp_path: Path) -> None:
            """After pruning, detector returns [] on a second pass (keep-set invariant)."""
            from agent_core.daemon.venv_gc import find_superseded_venvs, prune_superseded_venvs

            venvs_dir = tmp_path / "venvs"
            for v in ("0.6.0", "0.7.0", "0.8.0"):
                (venvs_dir / v).mkdir(parents=True)
            stable = tmp_path / ".venv"
            os.symlink(venvs_dir / "0.8.0", stable)

            superseded = find_superseded_venvs(venvs_dir, stable)
            assert superseded == [venvs_dir / "0.6.0"]
            prune_superseded_venvs(superseded)
            # Only 0.7.0 and 0.8.0 remain — ≤2 dirs, so nothing more to prune.
            assert find_superseded_venvs(venvs_dir, stable) == []
    ```

11. **Add `TestRemoveBrokenStableLinkPruner` to `test_daemon_venv_gc.py`** — append after `TestPruneSupersededVenvs`:
    ```python
    # ---------------------------------------------------------------------------
    # remove_broken_stable_link (pruner)
    # ---------------------------------------------------------------------------

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    class TestRemoveBrokenStableLinkPruner:
        def test_removes_dangling_symlink(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import remove_broken_stable_link

            stable = tmp_path / ".venv"
            os.symlink(tmp_path / "nonexistent", stable)
            result = remove_broken_stable_link(stable)
            assert result is True
            assert not stable.is_symlink()

        def test_does_not_remove_healthy_symlink(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import remove_broken_stable_link

            target = tmp_path / "venvs" / "0.8.0"
            target.mkdir(parents=True)
            stable = tmp_path / ".venv"
            os.symlink(target, stable)
            result = remove_broken_stable_link(stable)
            assert result is False
            assert stable.exists()

        def test_returns_false_for_plain_dir(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import remove_broken_stable_link

            plain = tmp_path / ".venv"
            plain.mkdir()
            assert remove_broken_stable_link(plain) is False

        def test_returns_false_when_absent(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import remove_broken_stable_link

            assert remove_broken_stable_link(tmp_path / ".venv") is False
    ```

12. **Add `TestRemoveOrphanedPartialBuilds` to `test_daemon_venv_gc.py`** — append after `TestRemoveBrokenStableLinkPruner`:
    ```python
    # ---------------------------------------------------------------------------
    # remove_orphaned_partial_builds
    # ---------------------------------------------------------------------------

    class TestRemoveOrphanedPartialBuilds:
        def test_removes_single_dir(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import remove_orphaned_partial_builds

            d = tmp_path / "0.8.0"
            d.mkdir()
            removed = remove_orphaned_partial_builds([d])
            assert removed == [d]
            assert not d.exists()

        def test_already_absent_is_idempotent(self, tmp_path: Path) -> None:
            from agent_core.daemon.venv_gc import remove_orphaned_partial_builds

            phantom = tmp_path / "0.7.0"  # never created
            assert remove_orphaned_partial_builds([phantom]) == []

        def test_empty_list_returns_empty(self) -> None:
            from agent_core.daemon.venv_gc import remove_orphaned_partial_builds

            assert remove_orphaned_partial_builds([]) == []
    ```

13. **Add four `--fix` CLI tests to `test_daemon_cli.py`** — append after the existing `test_doctor_fix_removes_dead_corpses` test:
    ```python
    def test_doctor_fix_prunes_superseded_venvs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """daemon doctor --fix calls prune_superseded_venvs for detected superseded venvs."""
        monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

        from agent_core.daemon.venv_gc import VenvGcReport

        superseded_path = tmp_path / "venvs" / "0.6.0"
        fake_venv_report = VenvGcReport(superseded_venvs=[superseded_path])
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_venv_doctor",
            lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
        )
        prune_calls: list[list] = []

        def fake_prune(paths: list) -> list:
            prune_calls.append(list(paths))
            return list(paths)  # simulate all removed

        monkeypatch.setattr("agent_core.daemon.cli.prune_superseded_venvs", fake_prune)
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_config_hygiene",
            lambda config_dir, fix: __import__(
                "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
            ).HygieneReport(),
        )

        result = runner.invoke(daemon_app, ["doctor", "--fix"])
        assert result.exit_code == 1  # has_issues remains True
        assert prune_calls == [[superseded_path]]
        assert "removed" in result.stdout.lower()


    def test_doctor_fix_removes_broken_stable_link(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """daemon doctor --fix calls remove_broken_stable_link for each broken link."""
        monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

        from agent_core.daemon.venv_gc import VenvGcReport

        stable_path = tmp_path / ".venv"
        fake_venv_report = VenvGcReport(broken_stable_links=[stable_path])
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_venv_doctor",
            lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
        )
        removal_calls: list = []

        def fake_remove(p: Path) -> bool:
            removal_calls.append(p)
            return True  # simulate successful removal

        monkeypatch.setattr("agent_core.daemon.cli.remove_broken_stable_link", fake_remove)
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_config_hygiene",
            lambda config_dir, fix: __import__(
                "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
            ).HygieneReport(),
        )

        result = runner.invoke(daemon_app, ["doctor", "--fix"])
        assert result.exit_code == 1
        assert removal_calls == [stable_path]
        assert "removed" in result.stdout.lower()


    def test_doctor_fix_removes_orphaned_partial_builds(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """daemon doctor --fix calls remove_orphaned_partial_builds for orphaned dirs."""
        monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

        from agent_core.daemon.venv_gc import VenvGcReport

        orphan_path = tmp_path / "venvs" / "0.8.0"
        fake_venv_report = VenvGcReport(orphaned_partial_builds=[orphan_path])
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_venv_doctor",
            lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
        )
        remove_calls: list[list] = []

        def fake_remove(paths: list) -> list:
            remove_calls.append(list(paths))
            return list(paths)

        monkeypatch.setattr("agent_core.daemon.cli.remove_orphaned_partial_builds", fake_remove)
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_config_hygiene",
            lambda config_dir, fix: __import__(
                "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
            ).HygieneReport(),
        )

        result = runner.invoke(daemon_app, ["doctor", "--fix"])
        assert result.exit_code == 1
        assert remove_calls == [[orphan_path]]
        assert "removed" in result.stdout.lower()


    def test_doctor_fix_repairs_drifted_mcp_json(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """daemon doctor --fix calls repair_mcp_json with correct being_name and vault_root."""
        monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

        from agent_core.daemon.venv_gc import VenvGcReport

        wren_home = tmp_path / ".wren"
        wren_home.mkdir()
        mcp_json_path = wren_home / ".mcp.json"
        fake_venv_report = VenvGcReport(drifted_mcp_jsons=[mcp_json_path])
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_venv_doctor",
            lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
        )
        repair_calls: list[tuple] = []

        def fake_repair(
            being_name: str, *, vault_root: Path, daemon_config_dir: Path
        ) -> tuple[Path, bool]:
            repair_calls.append((being_name, vault_root, daemon_config_dir))
            return mcp_json_path, True  # simulate repaired

        monkeypatch.setattr("agent_core.daemon.cli.repair_mcp_json", fake_repair)
        monkeypatch.setattr(
            "agent_core.daemon.cli.run_config_hygiene",
            lambda config_dir, fix: __import__(
                "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
            ).HygieneReport(),
        )

        result = runner.invoke(daemon_app, ["doctor", "--fix"])
        assert result.exit_code == 1
        assert len(repair_calls) == 1
        being_name_called, vault_root_called, _ = repair_calls[0]
        assert being_name_called == "wren"
        assert vault_root_called == wren_home
        assert "repaired" in result.stdout.lower()
    ```

## File-level changes

| File | Change | What changes |
|---|---|---|
| `packages/core/src/agent_core/daemon/venv_gc.py` | **Modify** | Add three pruner functions after `remove_dead_central_corpses`: `prune_superseded_venvs`, `remove_broken_stable_link`, `remove_orphaned_partial_builds` |
| `packages/core/src/agent_core/daemon/cli.py` | **Modify** | Extend the `venv_gc` import to include the three new pruners; add `from agent_core.venv.mcp_config import repair_mcp_json`; update the venv GC section comment; replace four report-only blocks with `if fix` / `else` branches for superseded venvs, broken stable links, orphaned partial builds, and drifted `.mcp.json` |
| `packages/core/tests/test_daemon_venv_gc.py` | **Modify** | Append three test classes: `TestPruneSupersededVenvs` (with idempotency test), `TestRemoveBrokenStableLinkPruner`, `TestRemoveOrphanedPartialBuilds` |
| `packages/core/tests/test_daemon_cli.py` | **Modify** | Append four `--fix` tests: `test_doctor_fix_prunes_superseded_venvs`, `test_doctor_fix_removes_broken_stable_link`, `test_doctor_fix_removes_orphaned_partial_builds`, `test_doctor_fix_repairs_drifted_mcp_json` |

No new files. No changes to `pyproject.toml`, `justfile`, CI workflows, or other packages.

## Alternatives considered

1. **Extend `run_venv_doctor` to accept `fix: bool` and run mutations inside the aggregator.** Would collapse detection and mutation into one call, removing the separate pruner functions. Ruled out: violates the pure-functions contract established in C2-3a (`venv_gc.py` is explicitly "detects (but does not prune)" per its module docstring); makes `run_venv_doctor` untestable with a single `tmp_path` fixture; diverges from `config_hygiene.py`'s proven separation of `run_config_hygiene(fix=True)` where the fix path is wired inline.

2. **Add a single `fix_venv_gc(report, *, home, daemon_config_dir)` function in `venv_gc.py` to bundle all four pruning calls.** Would give the CLI a single call instead of four. Ruled out as YAGNI: `cli.py` is the only caller, and four named calls with named intermediate `removed` lists are more readable and individually monkeypatchable in tests. A bundle function buys nothing at one call site.

3. **Add `repair_mcp_json` as a lazy import inside the `--fix` branch of the CLI (matching the `find_drifted_mcp_json` pattern).** `find_drifted_mcp_json` uses a lazy import because it lives in a pure-functions module that avoids module-level side effects. The CLI already carries many module-level imports; a module-level import of `repair_mcp_json` is consistent with that style and makes the dependency explicit. Ruled out (lazy form).

## Open questions

None. `repair_mcp_json` signature confirmed at `packages/core/src/agent_core/venv/mcp_config.py:215`. `os` and `shutil` confirmed present in `venv_gc.py` at lines 17–18. `remove_dead_central_corpses` confirmed present in `venv_gc.py` at line 250 and imported in `cli.py` at line 56.

## Out of scope

- Repairing drifted `.mcp.json` files _without_ `--fix` — that remains `agent-core venv regen-mcp <being>` (C2-2 / #316).
- Auto-rebuilding a being's venv after removing a broken stable link — the doctor prints a hint to run `agent-core venv build <being>`; the build step is C2-1 territory.
- Removing or replacing the `# TODO (C2-3b)` note in the existing report-only messages in `cli.py` with something other than the `--fix` wiring itself.
- Changes to `run_venv_doctor`, the five detector functions, or `VenvGcReport` — the C2-3a detection layer is complete and correct.
- Source and test daemon instance homes (`~/.agent-core-source/`, `~/.agent-core-test/`) — the doctor passes `home_for(inst)` as `daemon_home`, so `--instance source` already correctly scopes the GC pass.
- Pruning `dead_central_corpses` — already wired in C2-3a's implementation (lines 419–432 of `cli.py`); not touched here.
