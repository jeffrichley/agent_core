"""Tests for the per-kind schema-summary registry.

Each summarizer must return a value-free shape: keys describe the payload
structure, but no user-supplied values leak through. Tool/event/status/of
fields are structural identifiers (closed enums or namespaces) and are
safe to surface verbatim per the design spec.
"""

from __future__ import annotations

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    CancellationPayload,
    EventPayload,
    ProgressPayload,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core.bus_tail.summaries import SUMMARIZERS, summarize_payload


def test_summarize_text_message_returns_shape_only():
    payload = TextMessagePayload(
        text="hello world",
        attachments=[{"path": "/a.pdf"}, {"path": "/b.pdf"}],
    )
    summary = SUMMARIZERS["TextMessage"](payload)
    assert summary == {"text_length": 11, "attachment_count": 2}
    # No raw text value leaks.
    assert "hello world" not in str(summary)


def test_summarize_tool_invocation_includes_tool_name_and_keys():
    payload = ToolInvocationPayload(tool="send_envelope", args={"to": "pepper", "text": "hi"})
    summary = SUMMARIZERS["ToolInvocation"](payload)
    assert summary == {
        "tool": "send_envelope",
        "arg_count": 2,
        "arg_keys": ["text", "to"],  # sorted
    }
    # No arg values leak.
    assert "pepper" not in str(summary)
    assert "hi" not in str(summary)


def test_summarize_event_includes_type_version_keys():
    payload = EventPayload(type="HandoffReady", schema_version="1", data={"x": 1, "y": 2})
    summary = SUMMARIZERS["Event"](payload)
    assert summary == {
        "type": "HandoffReady",
        "schema_version": "1",
        "data_keys": ["x", "y"],
    }
    # No data values leak.
    assert "1" not in summary["data_keys"]
    assert 1 not in summary["data_keys"]


def test_summarize_cancellation_marks_reason_presence_only():
    with_reason = CancellationPayload(reason="user_clicked_stop")
    without = CancellationPayload(reason=None)
    assert SUMMARIZERS["Cancellation"](with_reason) == {"has_reason": True}
    assert SUMMARIZERS["Cancellation"](without) == {"has_reason": False}


def test_summarize_progress_keeps_status_drops_note_text():
    payload = ProgressPayload(status="working", note="halfway done", percent=50.0)
    summary = SUMMARIZERS["Progress"](payload)
    assert summary == {"status": "working", "has_note": True, "has_percent": True}
    assert "halfway done" not in str(summary)


def test_summarize_acknowledgment_includes_of_reference():
    payload = AcknowledgmentPayload(of="env-123", note="seen")
    summary = SUMMARIZERS["Acknowledgment"](payload)
    assert summary == {"of": "env-123", "has_note": True}


def test_summarize_payload_dispatches_by_kind():
    payload = TextMessagePayload(text="hi", attachments=[])
    summary = summarize_payload(payload)
    assert summary == {"text_length": 2, "attachment_count": 0}


def test_summarize_payload_unknown_kind_returns_warning():
    # Construct a payload-like object with an unknown kind via a stub.
    class FakePayload:
        kind = "FutureKind"

    summary = summarize_payload(FakePayload())
    assert summary == {"warning": "no summarizer for kind=FutureKind"}


def test_registry_covers_every_envelope_kind():
    """Guard against new envelope kinds shipping without summarizers."""
    from typing import get_args

    from agent_core.bus.envelope import EnvelopePayload

    # EnvelopePayload is Annotated[Union[...], Field(...)].
    # get_args returns (Union[...], Field(...)); first element is the union.
    union = get_args(EnvelopePayload)[0]
    payload_classes = get_args(union)
    kinds = {cls.model_fields["kind"].default for cls in payload_classes}
    assert kinds == set(SUMMARIZERS.keys()), (
        f"missing summarizers for: {kinds - set(SUMMARIZERS.keys())}; "
        f"stale entries: {set(SUMMARIZERS.keys()) - kinds}"
    )
