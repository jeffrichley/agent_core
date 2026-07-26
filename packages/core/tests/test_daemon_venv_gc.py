"""Unit tests for agent_core.daemon.venv_gc (C2-3a, issue #500)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_core.daemon.venv_gc import (
    VenvGcReport,
    _version_sort_key,
    current_stable_target,
    discover_being_homes,
    find_broken_stable_link,
    find_dead_central_corpses,
    find_drifted_mcp_json,
    find_orphaned_partial_builds,
    find_superseded_venvs,
    run_venv_doctor,
)

# ---------------------------------------------------------------------------
# _version_sort_key
# ---------------------------------------------------------------------------

class TestVersionSortKey:
    def test_simple_semver(self) -> None:
        assert _version_sort_key("0.8.0") == (0, 8, 0)

    def test_different_major(self) -> None:
        assert _version_sort_key("1.0.0") > _version_sort_key("0.9.9")

    def test_non_numeric_returns_zero_tuple(self) -> None:
        assert _version_sort_key("abc") == (0,)

    def test_sorts_correctly_descending(self) -> None:
        names = ["0.6.1", "0.8.0", "0.7.0"]
        sorted_names = sorted(names, key=_version_sort_key, reverse=True)
        assert sorted_names == ["0.8.0", "0.7.0", "0.6.1"]


# ---------------------------------------------------------------------------
# discover_being_homes
# ---------------------------------------------------------------------------

class TestDiscoverBeingHomes:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert discover_being_homes(tmp_path) == []

    def test_finds_being_with_agent_core_venvs(self, tmp_path: Path) -> None:
        being_home = tmp_path / ".wren"
        (being_home / ".agent-core" / "venvs").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == [being_home]

    def test_ignores_non_hidden_dirs(self, tmp_path: Path) -> None:
        visible = tmp_path / "wren"
        (visible / ".agent-core" / "venvs").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == []

    def test_ignores_daemon_home_pattern(self, tmp_path: Path) -> None:
        # ~/.agent-core/ has venvs/ at top level, not .agent-core/venvs/
        daemon_home = tmp_path / ".agent-core"
        (daemon_home / "venvs").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == []

    def test_discovers_multiple_beings(self, tmp_path: Path) -> None:
        for being in ["pepper", "wren"]:
            (tmp_path / f".{being}" / ".agent-core" / "venvs").mkdir(parents=True)
        result = discover_being_homes(tmp_path)
        assert len(result) == 2
        assert result == sorted(result)  # deterministically sorted

    def test_home_root_does_not_exist_returns_empty(self, tmp_path: Path) -> None:
        assert discover_being_homes(tmp_path / "nonexistent") == []

    def test_ignores_dirs_without_venvs_subdir(self, tmp_path: Path) -> None:
        # Has .agent-core but no venvs/ inside it
        (tmp_path / ".wren" / ".agent-core").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == []


# ---------------------------------------------------------------------------
# current_stable_target
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
class TestCurrentStableTargetPosix:
    def test_returns_target_for_healthy_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "venvs" / "0.8.0"
        target.mkdir(parents=True)
        stable = tmp_path / ".venv"
        os.symlink(target, stable)
        assert current_stable_target(stable) == target.resolve()

    def test_returns_none_for_plain_dir(self, tmp_path: Path) -> None:
        plain = tmp_path / ".venv"
        plain.mkdir()
        assert current_stable_target(plain) is None

    def test_returns_none_when_stable_absent(self, tmp_path: Path) -> None:
        assert current_stable_target(tmp_path / ".venv") is None

    def test_returns_target_for_broken_symlink(self, tmp_path: Path) -> None:
        stable = tmp_path / ".venv"
        os.symlink(tmp_path / "nonexistent", stable)
        result = current_stable_target(stable)
        # Returns the (non-existent) resolved target, not None
        assert result is not None


# ---------------------------------------------------------------------------
# find_superseded_venvs
# ---------------------------------------------------------------------------

class TestFindSupersededVenvs:
    def _make_venvs_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "venvs"
        d.mkdir()
        return d

    def test_empty_venvs_dir_returns_empty(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        stable = tmp_path / ".venv"
        assert find_superseded_venvs(venvs_dir, stable) == []

    def test_one_version_returns_empty(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        (venvs_dir / "0.8.0").mkdir()
        stable = tmp_path / ".venv"
        assert find_superseded_venvs(venvs_dir, stable) == []

    def test_two_versions_returns_empty(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        (venvs_dir / "0.7.0").mkdir()
        (venvs_dir / "0.8.0").mkdir()
        stable = tmp_path / ".venv"
        assert find_superseded_venvs(venvs_dir, stable) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_three_versions_prunes_oldest(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        v060 = venvs_dir / "0.6.1"
        v070 = venvs_dir / "0.7.0"
        v080 = venvs_dir / "0.8.0"
        v060.mkdir()
        v070.mkdir()
        v080.mkdir()
        stable = tmp_path / ".venv"
        os.symlink(v080, stable)

        result = find_superseded_venvs(venvs_dir, stable)
        assert result == [v060]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_keeps_current_and_n_minus_1(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        v1 = venvs_dir / "0.6.0"
        v2 = venvs_dir / "0.7.0"
        v3 = venvs_dir / "0.8.0"
        v4 = venvs_dir / "0.9.0"
        for d in (v1, v2, v3, v4):
            d.mkdir()
        stable = tmp_path / ".venv"
        os.symlink(v4, stable)

        result = find_superseded_venvs(venvs_dir, stable)
        assert v1 in result
        assert v2 in result
        assert v3 not in result  # N-1
        assert v4 not in result  # current

    def test_absent_venvs_dir_returns_empty(self, tmp_path: Path) -> None:
        assert find_superseded_venvs(tmp_path / "venvs", tmp_path / ".venv") == []

    def test_broken_stable_link_keeps_top_two(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        v060 = venvs_dir / "0.6.0"
        v070 = venvs_dir / "0.7.0"
        v080 = venvs_dir / "0.8.0"
        v060.mkdir()
        v070.mkdir()
        v080.mkdir()
        # stable doesn't exist — no current can be determined
        stable = tmp_path / ".venv"
        result = find_superseded_venvs(venvs_dir, stable)
        assert v060 in result
        assert v070 not in result
        assert v080 not in result


# ---------------------------------------------------------------------------
# find_broken_stable_link
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
class TestFindBrokenStableLinkPosix:
    def test_returns_path_for_dangling_symlink(self, tmp_path: Path) -> None:
        stable = tmp_path / ".venv"
        os.symlink(tmp_path / "nonexistent", stable)
        assert find_broken_stable_link(stable) == stable

    def test_returns_none_for_healthy_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "venvs" / "0.8.0"
        target.mkdir(parents=True)
        stable = tmp_path / ".venv"
        os.symlink(target, stable)
        assert find_broken_stable_link(stable) is None

    def test_returns_none_for_plain_dir(self, tmp_path: Path) -> None:
        plain = tmp_path / ".venv"
        plain.mkdir()
        assert find_broken_stable_link(plain) is None

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert find_broken_stable_link(tmp_path / ".venv") is None


# ---------------------------------------------------------------------------
# find_orphaned_partial_builds
# ---------------------------------------------------------------------------

class TestFindOrphanedPartialBuilds:
    def test_absent_dir_returns_empty(self, tmp_path: Path) -> None:
        assert find_orphaned_partial_builds(tmp_path / "venvs") == []

    def test_complete_venv_is_not_orphaned(self, tmp_path: Path) -> None:
        from agent_core.venv.builder import python_in_venv

        venvs_dir = tmp_path / "venvs"
        v = venvs_dir / "0.8.0"
        py = python_in_venv(v)
        py.parent.mkdir(parents=True)
        py.write_text("")  # create fake python binary

        assert find_orphaned_partial_builds(venvs_dir) == []

    def test_dir_without_python_is_orphaned(self, tmp_path: Path) -> None:
        venvs_dir = tmp_path / "venvs"
        v = venvs_dir / "0.8.0"
        v.mkdir(parents=True)
        # No python binary created

        result = find_orphaned_partial_builds(venvs_dir)
        assert result == [v]

    def test_multiple_mixed_versions(self, tmp_path: Path) -> None:
        from agent_core.venv.builder import python_in_venv

        venvs_dir = tmp_path / "venvs"
        good = venvs_dir / "0.8.0"
        bad = venvs_dir / "0.7.0"
        py = python_in_venv(good)
        py.parent.mkdir(parents=True)
        py.write_text("")
        bad.mkdir(parents=True)

        result = find_orphaned_partial_builds(venvs_dir)
        assert result == [bad]


# ---------------------------------------------------------------------------
# find_drifted_mcp_json
# ---------------------------------------------------------------------------

class TestFindDriftedMcpJson:
    def test_returns_path_when_mcp_json_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        being_home = tmp_path / ".wren"
        being_home.mkdir()
        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: True,
        )

        result = find_drifted_mcp_json("wren", vault_root=being_home, daemon_config_dir=tmp_path)
        assert result == being_home / ".mcp.json"

    def test_returns_none_when_canonical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        being_home = tmp_path / ".wren"
        being_home.mkdir()
        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: False,
        )
        result = find_drifted_mcp_json("wren", vault_root=being_home, daemon_config_dir=tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# find_dead_central_corpses
# ---------------------------------------------------------------------------

class TestFindDeadCentralCorpses:
    def test_empty_daemon_home_returns_empty(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        assert find_dead_central_corpses(tmp_path) == []

    def test_absent_daemon_home_returns_empty(self, tmp_path: Path) -> None:
        assert find_dead_central_corpses(tmp_path / "nonexistent") == []

    def test_detects_old_versioned_dir(self, tmp_path: Path) -> None:
        corpse = tmp_path / ".venv-v0.7.0"
        corpse.mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert corpse in result

    def test_detects_multiple_old_versioned_dirs(self, tmp_path: Path) -> None:
        c1 = tmp_path / ".venv-v0.6.1"
        c2 = tmp_path / ".venv-v0.7.0"
        c1.mkdir()
        c2.mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert c1 in result
        assert c2 in result

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_does_not_flag_healthy_stable_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "venvs" / "0.8.0"
        target.mkdir(parents=True)
        stable = tmp_path / ".venv"
        os.symlink(target, stable)
        assert find_dead_central_corpses(tmp_path) == []

    def test_detects_plain_venv_dir_as_corpse(self, tmp_path: Path) -> None:
        # Old 0.6.1-era .venv was a real directory, not a junction/symlink
        plain_venv = tmp_path / ".venv"
        plain_venv.mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert plain_venv in result

    def test_results_are_sorted(self, tmp_path: Path) -> None:
        (tmp_path / ".venv-v0.7.0").mkdir()
        (tmp_path / ".venv-v0.6.1").mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# VenvGcReport
# ---------------------------------------------------------------------------

class TestVenvGcReport:
    def test_has_issues_false_when_empty(self) -> None:
        assert not VenvGcReport().has_issues

    def test_has_issues_true_when_superseded_venvs(self, tmp_path: Path) -> None:
        r = VenvGcReport(superseded_venvs=[tmp_path / "venvs" / "0.6.0"])
        assert r.has_issues

    def test_has_issues_true_when_dead_corpses(self, tmp_path: Path) -> None:
        r = VenvGcReport(dead_central_corpses=[tmp_path / ".venv-v0.7.0"])
        assert r.has_issues


# ---------------------------------------------------------------------------
# run_venv_doctor integration (monkeypatched detectors)
# ---------------------------------------------------------------------------

class TestRunVenvDoctor:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_discovers_being_and_collects_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full layout: daemon home with corpse + being home with 3 venvs."""
        daemon_home = tmp_path / ".agent-core"
        daemon_home.mkdir()
        corpse = daemon_home / ".venv-v0.7.0"
        corpse.mkdir()

        being_home = tmp_path / ".wren"
        venvs_dir = being_home / ".agent-core" / "venvs"
        for v in ("0.6.0", "0.7.0", "0.8.0"):
            (venvs_dir / v).mkdir(parents=True)
        # Stable symlink pointing to current (0.8.0)
        stable = being_home / ".venv"
        os.symlink(venvs_dir / "0.8.0", stable)

        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: False,
        )

        report = run_venv_doctor(
            daemon_home=daemon_home,
            home_root=tmp_path,
            daemon_config_dir=daemon_home,
        )
        assert corpse in report.dead_central_corpses
        # 0.6.0 is superseded (current=0.8.0, N-1=0.7.0)
        assert (venvs_dir / "0.6.0") in report.superseded_venvs

    def test_clean_layout_has_no_issues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No beings, clean daemon home → no issues."""
        daemon_home = tmp_path / ".agent-core"
        daemon_home.mkdir()

        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: False,
        )

        report = run_venv_doctor(
            daemon_home=daemon_home,
            home_root=tmp_path,
            daemon_config_dir=daemon_home,
        )
        assert not report.has_issues


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
