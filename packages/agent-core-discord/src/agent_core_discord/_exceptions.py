"""Shared exception types for the agent_core_discord package.

Leaf module — imports nothing from other agent_core_discord modules so
that _acks.py and _lifecycle.py can import from here without creating
circular-import cycles.
"""

from __future__ import annotations


class _ToolError(Exception):
    """User-error during tool dispatch — produces an Acknowledgment with note."""


class _PersistError(Exception):
    """Attachment could not be persisted (unsafe path, etc.). Neutral —
    shared by the MCP tool and the inbound path; the tool layer
    translates it to _ToolError."""
