"""Issue #70: rendering pipeline — body encoder, per-kind renderers,
circuit breaker, truncation marker, redelivery tracker."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from agent_core_channel.rendering import (
    FallbackToBare,
    InlineAll,
    RedeliveryTracker,
    _inbox_attrs,
    _render_preview,
    _render_with_truncation,
    apply_circuit_breaker,
    encode_body,
    render_envelope,
    render_item,
    truncation_marker,
)


class TestEncodeBody:
    def test_passes_through_safe_text(self) -> None:
        assert encode_body("hello world") == "hello world"

    def test_escapes_lt_gt(self) -> None:
        assert encode_body("2 < 3 > 1") == "2 &lt; 3 &gt; 1"

    def test_escapes_ampersand(self) -> None:
        assert encode_body("a & b") == "a &amp; b"

    def test_escapes_quotes(self) -> None:
        assert encode_body("she said \"hi\" and 'bye'") == (
            "she said &quot;hi&quot; and &apos;bye&apos;"
        )

    def test_escapes_inbox_close_tag_literal(self) -> None:
        assert "</inbox>" not in encode_body("ends with </inbox> in body")

    def test_idempotent_after_escape(self) -> None:
        # Note: HTML escape is NOT strictly idempotent (escaping &amp; → &amp;amp;)
        # but is well-defined: each application escapes the literals seen.
        # The contract is "the output never contains unescaped &<>'\" ".
        once = encode_body("a & b < c")
        for ch in ("&amp;", "&lt;"):
            assert ch in once
        twice = encode_body(once)
        # No bare special chars after a single escape (all are now &-prefixed).
        assert "<" not in once
        assert "<" not in twice

    def test_xml_roundtrip_safety_with_user_payload(self) -> None:
        """Pepper's verification target: malicious user content does not
        break a downstream XML parse of the surrounding <inbox> wrapper."""
        nasty = "<script>alert(1)</script> & < > ' \" </inbox>"
        encoded = encode_body(nasty)
        wrapped = f"<inbox>{encoded}</inbox>"
        # Plain XML parser succeeds — that's the contract.
        root = ET.fromstring(wrapped)
        assert root.tag == "inbox"
        # Body content round-trips back to the original after XML decoding.
        assert root.text == nasty


def _env(env_id: str, *, kind: str, payload: dict, **kwargs) -> dict:
    """Build an envelope dict in the shape returned by consume()."""
    base = {
        "id": env_id,
        "from": "discord-pepper",
        "to": "pepper",
        "kind": kind,
        "correlation_id": f"corr-{env_id}",
        "in_reply_to": None,
        "payload": payload,
        "metadata": {},
        "urgency": "green",
        "created_at": "2026-05-09T03:29:22+00:00",
    }
    base.update(kwargs)
    return base


class TestRenderEnvelope:
    def test_text_message(self) -> None:
        env = _env(
            "e-1",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "hello there"},
        )
        out = render_envelope(env)
        assert "<inbox" in out
        assert "from='discord-pepper'" in out
        assert "urgency='green'" in out
        assert "envelope_id='e-1'" in out
        assert "kind='TextMessage'" in out
        assert "hello there" in out
        assert out.endswith("</inbox>")

    def test_text_message_escapes_body(self) -> None:
        env = _env(
            "e-2",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "<script>alert(1)</script>"},
        )
        out = render_envelope(env)
        assert "&lt;script&gt;" in out
        assert "<script>" not in out

    def test_acknowledgment_uses_note(self) -> None:
        env = _env(
            "e-3",
            kind="Acknowledgment",
            urgency="yellow",
            in_reply_to="out-1",
            payload={"kind": "Acknowledgment", "of": "out-1", "note": "error: timeout"},
        )
        out = render_envelope(env)
        assert "kind='Acknowledgment'" in out
        assert "urgency='yellow'" in out
        assert "in_reply_to='out-1'" in out
        assert "error: timeout" in out

    def test_acknowledgment_falls_back_to_payload_when_no_note(self) -> None:
        env = _env(
            "e-3b",
            kind="Acknowledgment",
            urgency="red",
            in_reply_to="out-2",
            payload={"kind": "Acknowledgment", "of": "out-2", "note": None},
        )
        out = render_envelope(env)
        # Body falls back to JSON-stringified payload.
        body = out.split(">", 1)[1].rsplit("</inbox>", 1)[0].strip()
        # The body should be JSON-shaped (contain payload keys), not just "None".
        assert "&quot;of&quot;" in body or '"of"' in body, (
            f"Body should contain payload keys, got: {body!r}"
        )
        assert body != "None"

    def test_event(self) -> None:
        env = _env(
            "e-4",
            kind="Event",
            payload={"kind": "Event", "type": "deploy.started", "data": {"sha": "abc"}},
        )
        out = render_envelope(env)
        assert "kind='Event'" in out
        assert "deploy.started" in out
        assert "abc" in out

    def test_generic_kind_uses_json_payload(self) -> None:
        env = _env(
            "e-5",
            kind="BriefRequest",
            payload={"kind": "BriefRequest", "playbook": "morning_brief"},
        )
        out = render_envelope(env)
        assert "kind='BriefRequest'" in out
        assert "morning_brief" in out

    def test_unknown_kind_falls_back_to_repr(self) -> None:
        env = _env(
            "e-6",
            kind="ExoticPluginKind",
            payload={"kind": "ExoticPluginKind", "blob": "data"},
        )
        out = render_envelope(env)
        assert "kind='ExoticPluginKind'" in out
        assert "render='fallback'" in out

    def test_attribute_values_are_not_escaped(self) -> None:
        # envelope_id is a hex UUID, kind is bounded enum — no escaping needed.
        env = _env(
            "abc123",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "ok"},
        )
        out = render_envelope(env)
        assert "envelope_id='abc123'" in out
        assert "&apos;" not in out.split(">")[0]  # no escaped quotes in opening tag

    def test_xml_parseable_with_nasty_payload(self) -> None:
        env = _env(
            "e-nasty",
            kind="TextMessage",
            payload={"kind": "TextMessage", "text": "</inbox> & <script>"},
        )
        out = render_envelope(env)
        # Wrap and parse — must succeed.
        ET.fromstring(out)


class TestRenderBatchEntry:
    def test_single_wrapped_entry(self) -> None:
        item = {
            "type": "single",
            "envelope": _env("e-s", kind="TextMessage", payload={"kind": "TextMessage", "text": "x"}),
        }
        outs = render_item(item)
        assert len(outs) == 1
        assert "envelope_id='e-s'" in outs[0]

    def test_batch_entry_with_prefix(self) -> None:
        item = {
            "type": "batch",
            "from": "discord-pepper",
            "kind": "TextMessage",
            "urgency": "green",
            "envelopes": [
                _env("b-1", kind="TextMessage", payload={"kind": "TextMessage", "text": "first"}),
                _env("b-2", kind="TextMessage", payload={"kind": "TextMessage", "text": "second"}),
            ],
            "first_arrival": "2026-05-09T03:29:22+00:00",
            "total_age_seconds": 5,
        }
        outs = render_item(item)
        assert len(outs) == 2
        assert "batch='1/2'" in outs[0]
        assert "batch='2/2'" in outs[1]
        assert "envelope_id='b-1'" in outs[0]
        assert "envelope_id='b-2'" in outs[1]

    def test_flat_envelope_dict(self) -> None:
        env = _env("flat-1", kind="TextMessage", payload={"kind": "TextMessage", "text": "y"})
        outs = render_item(env)  # flat envelope (no "type" key)
        assert len(outs) == 1
        assert "envelope_id='flat-1'" in outs[0]

    def test_unknown_item_shape_fallback(self) -> None:
        """Defensive fallback when consume() returns a malformed item.

        Should produce a parseable <inbox> block with diagnostic info, not
        crash and not produce structurally broken output."""
        item = {"type": "exotic", "weirdness": True}
        outs = render_item(item)
        assert len(outs) == 1
        block = outs[0]
        # Block must be parseable XML.
        root = ET.fromstring(block)
        assert root.tag == "inbox"
        assert root.attrib.get("render") == "fallback"
        assert root.attrib.get("reason") == "unrecognized_item_shape"
        # Diagnostic body mentions the malformed type.
        assert "exotic" in block


class TestTruncationMarker:
    def test_format(self) -> None:
        assert truncation_marker("abc") == (
            "[content elided; envelope_id='abc'; call peek('abc') for full payload]"
        )


class TestApplyCircuitBreaker:
    def _flat(self, env_id: str, kind: str = "TextMessage", urgency: str = "green",
              text: str = "hi") -> dict:
        return {
            "id": env_id,
            "from": "discord-pepper",
            "to": "pepper",
            "kind": kind,
            "correlation_id": f"c-{env_id}",
            "in_reply_to": None,
            "payload": {"kind": kind, "text": text},
            "metadata": {},
            "urgency": urgency,
            "created_at": "2026-05-09T03:29:22+00:00",
        }

    def test_inlines_below_caps(self) -> None:
        items = [self._flat("e-1", text="hi"), self._flat("e-2", text="there")]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        assert len(result.rendered) == 2
        assert "e-1" in result.inlined_ids
        assert "e-2" in result.inlined_ids

    def test_falls_back_when_envelope_count_exceeds(self) -> None:
        items = [self._flat(f"e-{i}", text="x") for i in range(6)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="full",
        )
        assert isinstance(result, FallbackToBare)
        assert result.reason == "envelope_count"

    def test_per_envelope_cap_triggers_truncation_marker(self) -> None:
        big = self._flat("big-1", text="x" * 5000)  # exceeds per-envelope cap
        small = self._flat("small-1", text="hi")
        result = apply_circuit_breaker(
            [small, big],
            max_envelopes=5, max_total_bytes=20000, max_per_envelope_bytes=1024,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        # The big one is replaced with the truncation marker.
        big_block = next(b for b in result.rendered if "envelope_id='big-1'" in b)
        assert "call peek('big-1')" in big_block
        # The small one renders normally.
        small_block = next(b for b in result.rendered if "envelope_id='small-1'" in b)
        assert "call peek(" not in small_block

    def test_falls_back_when_total_bytes_exceeds(self) -> None:
        items = [self._flat(f"e-{i}", text="x" * 1000) for i in range(5)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=10, max_total_bytes=2000, max_per_envelope_bytes=2000,
            mode="full",
        )
        assert isinstance(result, FallbackToBare)
        assert result.reason == "total_bytes"

    def test_failure_envelopes_bypass_total_bytes_cap(self) -> None:
        # 5 yellow + red envelopes summing past total cap still inline.
        items = [self._flat(f"f-{i}", urgency="yellow", text="x" * 1000) for i in range(5)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=10, max_total_bytes=2000, max_per_envelope_bytes=2000,
            mode="full",
        )
        assert isinstance(result, InlineAll)

    def test_failure_envelopes_still_respect_per_envelope_cap(self) -> None:
        items = [self._flat("big-fail", urgency="red", text="x" * 5000)]
        result = apply_circuit_breaker(
            items,
            max_envelopes=10, max_total_bytes=20000, max_per_envelope_bytes=1024,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        assert "call peek('big-fail')" in result.rendered[0]

    def test_preview_mode_replaces_body_with_marker_with_preview(self) -> None:
        items = [self._flat("p-1", text="hello world this is a preview message")]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="preview",
        )
        assert isinstance(result, InlineAll)
        block = result.rendered[0]
        assert "preview='true'" in block
        assert "hello world" in block  # preview present
        assert "call peek('p-1')" in block  # marker still appended

    def test_inlined_envelopes_summary_shape(self) -> None:
        items = [self._flat("s-1", text="hi")]
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        assert len(result.inlined_envelopes_summary) == 1
        summary = result.inlined_envelopes_summary[0]
        assert summary["id"] == "s-1"
        assert summary["kind"] == "TextMessage"
        assert summary["from"] == "discord-pepper"
        assert summary["urgency"] == "green"
        assert "bytes" in summary

    def test_unrecognized_item_shape_does_not_crash(self) -> None:
        """apply_circuit_breaker pairs cleanly with render_item's defensive
        fallback for unknown item shapes (issue from Task 4 review)."""
        items = [{"type": "exotic", "weirdness": True}]
        # Should not raise.
        result = apply_circuit_breaker(
            items,
            max_envelopes=5, max_total_bytes=8192, max_per_envelope_bytes=4096,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        # The synthetic envelope contributes one rendered block.
        assert len(result.rendered) == 1
        assert "render='fallback'" in result.rendered[0]

    def test_per_envelope_truncation_preserves_fallback_marker(self) -> None:
        """When an envelope renders with render='fallback' (unknown kind) AND
        exceeds the per-envelope cap, the truncation rebuild must preserve the
        fallback attribute."""
        big_unknown = {
            "id": "big-fb",
            "from": "discord-pepper",
            "to": "pepper",
            "kind": "ExoticUnknownKind",
            "correlation_id": "c-big-fb",
            "in_reply_to": None,
            "payload": {"kind": "ExoticUnknownKind", "blob": "x" * 5000},
            "metadata": {},
            "urgency": "green",
            "created_at": "2026-05-09T03:29:22+00:00",
        }
        result = apply_circuit_breaker(
            [big_unknown],
            max_envelopes=5, max_total_bytes=20000, max_per_envelope_bytes=1024,
            mode="full",
        )
        assert isinstance(result, InlineAll)
        block = result.rendered[0]
        assert "render='fallback'" in block
        assert "call peek('big-fb')" in block


class TestRedeliveryTracker:
    def test_first_sighting_no_marker(self) -> None:
        tracker = RedeliveryTracker(capacity=10)
        assert tracker.note_and_get_marker("e-1") is None

    def test_second_sighting_returns_resend_marker(self) -> None:
        tracker = RedeliveryTracker(capacity=10)
        tracker.note_and_get_marker("e-1")
        assert tracker.note_and_get_marker("e-1") == "[RESEND #2] "

    def test_third_sighting_returns_resend_3(self) -> None:
        tracker = RedeliveryTracker(capacity=10)
        tracker.note_and_get_marker("e-1")
        tracker.note_and_get_marker("e-1")
        assert tracker.note_and_get_marker("e-1") == "[RESEND #3] "

    def test_lru_evicts_oldest(self) -> None:
        tracker = RedeliveryTracker(capacity=2)
        tracker.note_and_get_marker("e-1")
        tracker.note_and_get_marker("e-2")
        tracker.note_and_get_marker("e-3")  # evicts e-1
        # e-1 is now first-sighting again.
        assert tracker.note_and_get_marker("e-1") is None


def test_inbox_attrs_framework_only_no_metadata():
    env = {
        "id": "abc",
        "kind": "TextMessage",
        "from": "discord-pepper",
        "urgency": "green",
    }
    attrs = _inbox_attrs(env)
    assert attrs == [
        "kind='TextMessage'",
        "from='discord-pepper'",
        "urgency='green'",
        "envelope_id='abc'",
    ]


def test_inbox_attrs_includes_in_reply_to_when_set():
    env = {
        "id": "abc",
        "kind": "TextMessage",
        "from": "discord-pepper",
        "urgency": "green",
        "in_reply_to": "parent-xyz",
    }
    attrs = _inbox_attrs(env)
    assert "in_reply_to='parent-xyz'" in attrs


def test_inbox_attrs_omits_in_reply_to_when_none():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "in_reply_to": None,
    }
    attrs = _inbox_attrs(env)
    assert not any("in_reply_to" in a for a in attrs)


def test_inbox_attrs_emits_channel_id_and_name_when_both_present():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "metadata": {"discord": {
            "channel_id": "1491445346570866812",
            "channel_name": "#pepper-upgrade",
        }},
    }
    attrs = _inbox_attrs(env)
    assert any('channel_id="1491445346570866812"' in a for a in attrs)
    assert any('channel_name="#pepper-upgrade"' in a for a in attrs)


def test_inbox_attrs_emits_channel_id_alone_when_name_missing():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "metadata": {"discord": {"channel_id": "X"}},
    }
    attrs = _inbox_attrs(env)
    assert any('channel_id="X"' in a for a in attrs)
    assert not any("channel_name" in a for a in attrs)


def test_inbox_attrs_escapes_special_chars_in_channel_name():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "metadata": {"discord": {
            "channel_id": "1", "channel_name": "pepper's <chat> & co",
        }},
    }
    attrs = _inbox_attrs(env)
    # quoteattr is parser-safe: <, >, & are entity-escaped; ' and " are
    # handled by quote-style choice (double-quote framing when the value
    # contains '). Round-trip through an XML parse is the real contract.
    name_attr = next(a for a in attrs if a.startswith("channel_name="))
    assert "&lt;" in name_attr
    assert "&gt;" in name_attr
    assert "&amp;" in name_attr
    parsed = ET.fromstring(f"<inbox {name_attr}/>")
    assert parsed.attrib["channel_name"] == "pepper's <chat> & co"


def test_inbox_attrs_omits_both_when_channel_id_missing_even_if_name_present():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {"channel_name": "#x"}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_omits_both_on_empty_strings():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {"channel_id": "", "channel_name": ""}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_omits_both_on_none_values():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {"channel_id": None, "channel_name": None}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_handles_empty_discord_dict():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": {"discord": {}},
    }
    attrs = _inbox_attrs(env)
    assert not any("channel" in a for a in attrs)


def test_inbox_attrs_handles_missing_metadata():
    env = {"id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green"}
    attrs = _inbox_attrs(env)
    assert len(attrs) == 4  # framework only


def test_inbox_attrs_handles_non_dict_metadata_defensively():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "x", "urgency": "green",
        "metadata": "oops",
    }
    # Should not crash; behavior: returns framework attrs only.
    attrs = _inbox_attrs(env)
    assert len(attrs) == 4


def test_render_envelope_includes_channel_attrs_for_discord_inbound():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green",
        "payload": {"text": "hi"},
        "metadata": {"discord": {
            "channel_id": "1491", "channel_name": "#pepper-upgrade",
        }},
    }
    block = render_envelope(env)
    assert 'channel_id="1491"' in block
    assert 'channel_name="#pepper-upgrade"' in block
    assert "<inbox " in block
    assert "</inbox>" in block


def test_render_preview_includes_channel_attrs_for_discord_inbound():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green", "payload": {"text": "hi"},
        "metadata": {"discord": {"channel_id": "1491", "channel_name": "#x"}},
    }
    block = _render_preview(env)
    assert 'channel_id="1491"' in block
    assert "preview='true'" in block


def test_render_with_truncation_includes_channel_attrs_for_discord_inbound():
    env = {
        "id": "abc", "kind": "TextMessage", "from": "discord-pepper",
        "urgency": "green", "payload": {"text": "hi"},
        "metadata": {"discord": {"channel_id": "1491"}},
    }
    block = _render_with_truncation(env, body=truncation_marker("abc"), fallback=False)
    assert 'channel_id="1491"' in block
