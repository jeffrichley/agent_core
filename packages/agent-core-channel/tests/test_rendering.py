"""Issue #70: rendering pipeline — body encoder, per-kind renderers."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from agent_core_channel.rendering import encode_body, render_envelope, render_item


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
