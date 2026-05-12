"""Tests for FileAttachment Pydantic model (#64)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import FileAttachment, TextMessagePayload


def test_file_attachment_requires_path():
    """Missing `path` raises ValidationError at publish time."""
    with pytest.raises(ValidationError):
        FileAttachment()


def test_file_attachment_rejects_empty_string_path():
    """Empty `path` rejected by Field(min_length=1)."""
    with pytest.raises(ValidationError):
        FileAttachment(path="")


def test_file_attachment_allows_extra_fields():
    """extra='allow' lets aspirational fields land without schema migration."""
    attachment = FileAttachment(path="/abs/file.pdf", filename="renamed.pdf")
    assert attachment.path == "/abs/file.pdf"
    # filename available via model_extra (Pydantic v2 extra-allow mechanism)
    assert attachment.model_extra is not None
    assert attachment.model_extra.get("filename") == "renamed.pdf"


def test_text_message_payload_defaults_attachments_empty():
    """Backward compat: existing publishes without `attachments` still validate."""
    payload = TextMessagePayload(text="hi")
    assert payload.attachments == []


def test_text_message_payload_typo_in_attachment_key_raises_at_publish():
    """Named-symptom regression lock: a typo in the attachment dict key
    surfaces at envelope-construction time, synchronously to the agent's
    publish call, not as a later yellow Ack.
    """
    with pytest.raises(ValidationError):
        TextMessagePayload(
            text="hi",
            attachments=[{"paht": "/abs/file.pdf"}],  # typo: missing 'path'
        )
