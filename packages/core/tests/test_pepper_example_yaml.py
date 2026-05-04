"""Tripwire tests against ``docs/examples/pepper-agent-core.yaml``.

The example pepper config is the canonical wiring referenced by:
- Cutover #01 (IdentityInjector at SessionStart)
- Cutover #02 (HandoffWriter / HandoffInjector at SessionEnd, PreCompact, SessionStart)
- Cutover #07 (TimeInjector on UserPromptSubmit with track_session=true)

The acceptance failure mode that #07 names explicitly is "someone refactors the
pipeline, TimeInjector quietly moves to SessionStart-only, and I start getting
day-of-week wrong without anyone noticing." Unit tests on the tools in
isolation cannot catch a regression where the *registration* drops out of the
yaml. These tests load the real file and assert the wiring stays put.

If you intentionally restructure the example, update these tests too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.hooks.pipeline import Pipeline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_YAML = _REPO_ROOT / "docs" / "examples" / "pepper-agent-core.yaml"


@pytest.fixture(scope="module")
def pepper_pipeline() -> Pipeline:
    if not _EXAMPLE_YAML.exists():
        pytest.fail(
            f"docs/examples/pepper-agent-core.yaml not found at {_EXAMPLE_YAML}; "
            "Cutover #07 wiring tests cannot run."
        )
    return Pipeline(_EXAMPLE_YAML)


def _types_for(pipeline: Pipeline, event: str) -> list[str]:
    return [t.type for t in pipeline.config.pipelines.get(event, [])]


class TestPepperExampleYaml:
    """Lock in the canonical SessionStart/UserPromptSubmit/PreCompact/SessionEnd wiring."""

    def test_user_prompt_submit_registers_time_injector(self, pepper_pipeline: Pipeline):
        """Cutover #07 acceptance #1: TimeInjector must fire on every
        UserPromptSubmit so the agent re-anchors on time each turn. The
        documented regression mode (`feedback_day_labels`) is exactly what
        this assertion catches: a refactor that drops TimeInjector from
        UserPromptSubmit would silently break per-turn re-anchoring."""
        assert "builtin.time_injector" in _types_for(pepper_pipeline, "UserPromptSubmit")

    def test_user_prompt_submit_time_injector_has_track_session_true(
        self, pepper_pipeline: Pipeline
    ):
        """Without track_session=true, TimeInjector emits only the absolute
        time on UserPromptSubmit and the per-turn 'Last user turn Xm ago'
        deltas disappear. That is half the value of registering it on this
        event in the first place."""
        ups = pepper_pipeline.config.pipelines["UserPromptSubmit"]
        time_tools = [t for t in ups if t.type == "builtin.time_injector"]
        assert time_tools, "TimeInjector missing from UserPromptSubmit"
        assert time_tools[0].params.get("track_session") is True

    def test_session_start_registers_identity_and_handoff_injectors(
        self, pepper_pipeline: Pipeline
    ):
        """Cutover #07 acceptance #2 (firing of #01's tools). The example
        registers three IdentityInjector entries (SOUL, IDENTITY, preferences)
        and one HandoffInjector. Drop any one of them and the agent's
        SessionStart context is wrong."""
        types = _types_for(pepper_pipeline, "SessionStart")
        assert types.count("builtin.identity_injector") == 3
        assert types.count("builtin.handoff_injector") == 1

    def test_session_end_and_precompact_register_handoff_writer(
        self, pepper_pipeline: Pipeline
    ):
        """Cutover #07 acceptance #3 (firing of #02's writer). HandoffWriter
        must fire on both SessionEnd and PreCompact — losing either path
        produces silent continuity gaps for the next session."""
        assert "builtin.handoff_writer" in _types_for(pepper_pipeline, "SessionEnd")
        assert "builtin.handoff_writer" in _types_for(pepper_pipeline, "PreCompact")

    def test_user_prompt_submit_pipeline_runs_and_renders_current_time(
        self, pepper_pipeline: Pipeline
    ):
        """End-to-end: Pipeline.run("UserPromptSubmit", ...) actually executes
        TimeInjector and pipeline.render() includes the '## Current Time'
        heading. Catches regressions in render that would silently strip the
        injection (e.g., a future 'drop empty headings' optimization that
        misjudges TimeInjector output)."""
        results = pepper_pipeline.run("UserPromptSubmit", {"session_id": "yaml-tripwire"})
        rendered = pepper_pipeline.render(results)
        assert "## Current Time" in rendered


class TestPepperExampleYamlBusLog:
    """Cutover #04 wiring tripwire."""

    def test_bus_hooks_pre_publish_registers_daily_raw_jsonl(self):
        """The example yaml must register builtin.daily_raw_jsonl on
        pre_publish so Pepper's bus traffic is captured for tomorrow's
        reflection summary. If a future refactor drops this entry, the
        test fails — Pepper would silently stop logging her day."""
        import yaml as pyyaml
        raw = pyyaml.safe_load(_EXAMPLE_YAML.read_text(encoding="utf-8"))
        bus_hooks = (raw or {}).get("bus_hooks", {}) or {}
        pre_publish = bus_hooks.get("pre_publish") or []
        types = [entry.get("type") for entry in pre_publish]
        assert "builtin.daily_raw_jsonl" in types, (
            "Cutover #04 expects the daily JSONL hook on pre_publish"
        )
