"""Tests for the register_envelope_renderers hookspec + get_envelope_renderers aggregator.

Covers plugins/specs.py + plugins/manager.py changes from
docs/superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md §2.2 + §2.3.
"""

import pluggy
import pytest

from agent_core.plugins.manager import (
    PluginRegistryError,
    create_plugin_manager,
    get_envelope_renderers,
)


hookimpl = pluggy.HookimplMarker("agent_core")


class _PluginA:
    @hookimpl
    def register_envelope_renderers(self) -> dict[str, object]:
        return {"Desire": lambda env: "<desire/>"}


class _PluginB:
    @hookimpl
    def register_envelope_renderers(self) -> dict[str, object]:
        return {"Thought": lambda env: "<thought/>"}


class _PluginCollidesWithA:
    @hookimpl
    def register_envelope_renderers(self) -> dict[str, object]:
        # Different callable; same kind as PluginA. Collision.
        return {"Desire": lambda env: "<desire-other/>"}


class _PluginReturnsNone:
    @hookimpl
    def register_envelope_renderers(self) -> dict[str, object] | None:
        return None  # No registrations from this plugin.


class _PluginReturnsEmpty:
    @hookimpl
    def register_envelope_renderers(self) -> dict[str, object]:
        return {}


class TestGetEnvelopeRenderers:
    def test_no_plugins_returns_empty(self) -> None:
        pm = create_plugin_manager()
        assert get_envelope_renderers(pm) == {}

    def test_single_plugin_contributes_one_renderer(self) -> None:
        pm = create_plugin_manager()
        pm.register(_PluginA(), name="plugin-a")
        renderers = get_envelope_renderers(pm)
        assert set(renderers.keys()) == {"Desire"}
        assert renderers["Desire"]({}) == "<desire/>"

    def test_two_plugins_contribute_distinct_kinds(self) -> None:
        pm = create_plugin_manager()
        pm.register(_PluginA(), name="plugin-a")
        pm.register(_PluginB(), name="plugin-b")
        renderers = get_envelope_renderers(pm)
        assert set(renderers.keys()) == {"Desire", "Thought"}

    def test_duplicate_kind_across_plugins_raises(self) -> None:
        pm = create_plugin_manager()
        pm.register(_PluginA(), name="plugin-a")
        pm.register(_PluginCollidesWithA(), name="plugin-colliding")
        with pytest.raises(PluginRegistryError, match="duplicate envelope-renderer kind 'Desire'"):
            get_envelope_renderers(pm)

    def test_plugin_returning_none_is_skipped(self) -> None:
        pm = create_plugin_manager()
        pm.register(_PluginA(), name="plugin-a")
        pm.register(_PluginReturnsNone(), name="plugin-none")
        renderers = get_envelope_renderers(pm)
        assert set(renderers.keys()) == {"Desire"}

    def test_plugin_returning_empty_dict_is_skipped(self) -> None:
        pm = create_plugin_manager()
        pm.register(_PluginA(), name="plugin-a")
        pm.register(_PluginReturnsEmpty(), name="plugin-empty")
        renderers = get_envelope_renderers(pm)
        assert set(renderers.keys()) == {"Desire"}

    def test_same_callable_registered_twice_is_idempotent(self) -> None:
        # Edge case: if a plugin somehow returns its renderer mapping from
        # multiple hooks, the aggregator should accept the same callable
        # without raising (it's not a real collision).
        shared_fn = lambda env: "<shared/>"  # noqa: E731

        class _PluginAA:
            @hookimpl
            def register_envelope_renderers(self) -> dict[str, object]:
                return {"Shared": shared_fn}

        class _PluginAB:
            @hookimpl
            def register_envelope_renderers(self) -> dict[str, object]:
                return {"Shared": shared_fn}

        pm = create_plugin_manager()
        pm.register(_PluginAA(), name="plugin-aa")
        pm.register(_PluginAB(), name="plugin-ab")
        renderers = get_envelope_renderers(pm)
        assert renderers["Shared"] is shared_fn
