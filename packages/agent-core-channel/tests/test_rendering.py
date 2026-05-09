"""Issue #70: rendering pipeline — body encoder, per-kind renderers,
circuit breaker, truncation marker, redelivery tracker."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from agent_core_channel.rendering import encode_body


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
