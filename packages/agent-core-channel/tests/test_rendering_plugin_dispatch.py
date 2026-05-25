"""Tests for set_plugin_renderers + render_envelope plugin-dispatch path.

Covers agent-core-channel/rendering.py changes from
docs/superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md §2.4.

Dispatch order: plugin -> built-in -> generic -> fallback.
Plugin renderers take precedence on collision with built-ins.
"""

import pytest

from agent_core_channel.rendering import render_envelope, set_plugin_renderers


@pytest.fixture(autouse=True)
def _clear_plugin_renderers():
    """Ensure tests start + end with a clean plugin-renderer table.

    Set to empty before AND after each test so leakage between tests in
    either direction is impossible.
    """
    set_plugin_renderers({})
    yield
    set_plugin_renderers({})


def _env(kind: str, payload: dict | None = None, **extra) -> dict:
    return {
        "id": "test-id",
        "kind": kind,
        "from": "test-sender",
        "urgency": "green",
        "payload": payload or {},
        "metadata": {},
        **extra,
    }


class TestPluginDispatch:
    def test_plugin_kind_dispatches_to_registered_renderer(self) -> None:
        def desire_renderer(env: dict) -> str:
            text = env["payload"].get("text", "")
            return f"<desire>{text}</desire>"

        set_plugin_renderers({"Desire": desire_renderer})
        out = render_envelope(_env("Desire", {"text": "past-me wanted this"}))
        assert "<desire>past-me wanted this</desire>" in out
        assert "kind='Desire'" in out
        # No render='fallback' attribute when plugin renderer succeeds.
        assert "render='fallback'" not in out

    def test_unknown_kind_with_no_plugin_falls_back(self) -> None:
        out = render_envelope(_env("Mystery", {"text": "unknown"}))
        assert "render='fallback'" in out
        assert "kind='Mystery'" in out

    def test_plugin_renderer_failure_falls_back(self) -> None:
        def broken_renderer(env: dict) -> str:
            raise RuntimeError("plugin renderer threw")

        set_plugin_renderers({"Broken": broken_renderer})
        out = render_envelope(_env("Broken", {"text": "x"}))
        assert "render='fallback'" in out

    def test_plugin_renderer_overrides_builtin_for_same_kind(self) -> None:
        # Plugin renderer wins over built-in TextMessage renderer.
        def custom_text_renderer(env: dict) -> str:
            return "<custom-text-render/>"

        set_plugin_renderers({"TextMessage": custom_text_renderer})
        out = render_envelope(_env("TextMessage", {"text": "ignored"}))
        assert "<custom-text-render/>" in out
        # The original built-in renderer would have produced "ignored" — confirm
        # it's NOT in the output (the plugin won).
        assert "ignored" not in out


class TestBuiltinDispatchUnchanged:
    """Without plugin renderers registered, built-in dispatch behaves as before."""

    def test_text_message_renders_via_builtin(self) -> None:
        out = render_envelope(_env("TextMessage", {"text": "hello"}))
        assert "kind='TextMessage'" in out
        assert "hello" in out
        assert "render='fallback'" not in out

    def test_event_renders_via_builtin(self) -> None:
        out = render_envelope(
            _env("Event", {"type": "Test", "data": {"foo": "bar"}})
        )
        assert "kind='Event'" in out
        # Generic JSON body for events.
        assert "Test" in out

    def test_unknown_kind_with_no_plugins_falls_back(self) -> None:
        # Sanity check that fallback path still works without plugin
        # registration — same case as the first plugin test but with the
        # plugin-renderer table genuinely empty (not just no match).
        out = render_envelope(_env("Unknown", {}))
        assert "render='fallback'" in out


class TestSetPluginRenderersTableManagement:
    def test_set_replaces_not_merges(self) -> None:
        set_plugin_renderers({"A": lambda env: "a"})
        set_plugin_renderers({"B": lambda env: "b"})
        # A is gone because set_plugin_renderers REPLACED the table.
        out_a = render_envelope(_env("A"))
        out_b = render_envelope(_env("B"))
        assert "render='fallback'" in out_a
        assert "render='fallback'" not in out_b

    def test_set_to_empty_clears_table(self) -> None:
        set_plugin_renderers({"X": lambda env: "x"})
        set_plugin_renderers({})
        out = render_envelope(_env("X"))
        assert "render='fallback'" in out
