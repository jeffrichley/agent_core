"""Per-kind schema-summary functions.

Each summarizer returns a value-free shape — keys describe the payload's
structure, but no user-supplied values leak. Tool/event/status/of fields
are structural identifiers (closed enums or namespaces) and are surfaced
verbatim per the design spec.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    CancellationPayload,
    EventPayload,
    ProgressPayload,
    TextMessagePayload,
    ToolInvocationPayload,
)


def summarize_text_message(p: TextMessagePayload) -> dict[str, Any]:
    return {"text_length": len(p.text), "attachment_count": len(p.attachments)}


def summarize_tool_invocation(p: ToolInvocationPayload) -> dict[str, Any]:
    return {
        "tool": p.tool,
        "arg_count": len(p.args),
        "arg_keys": sorted(p.args.keys()),
    }


def summarize_event(p: EventPayload) -> dict[str, Any]:
    return {
        "type": p.type,
        "schema_version": p.schema_version,
        "data_keys": sorted(p.data.keys()),
    }


def summarize_cancellation(p: CancellationPayload) -> dict[str, Any]:
    return {"has_reason": p.reason is not None}


def summarize_progress(p: ProgressPayload) -> dict[str, Any]:
    return {
        "status": p.status,
        "has_note": p.note is not None,
        "has_percent": p.percent is not None,
    }


def summarize_acknowledgment(p: AcknowledgmentPayload) -> dict[str, Any]:
    return {"of": p.of, "has_note": p.note is not None}


SUMMARIZERS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "TextMessage": summarize_text_message,
    "Event": summarize_event,
    "ToolInvocation": summarize_tool_invocation,
    "Cancellation": summarize_cancellation,
    "Progress": summarize_progress,
    "Acknowledgment": summarize_acknowledgment,
}


def summarize_payload(payload: Any) -> dict[str, Any]:
    """Dispatch to the registered summarizer; warn on unknown kinds."""
    kind = getattr(payload, "kind", None)
    summarizer = SUMMARIZERS.get(kind) if kind else None
    if summarizer is None:
        return {"warning": f"no summarizer for kind={kind}"}
    return summarizer(payload)


__all__ = ["SUMMARIZERS", "summarize_payload"]
