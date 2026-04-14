"""Desktop notification MCP server.

Provides toast notification tools that agents can call during a session.
Stays alive for the session lifetime, enabling interactive notifications
with reply fields and action buttons.

Tools:
    send_notification: Fire-and-forget toast notification.
    ask_user: Send notification with reply field, return immediately with ID.
    notify_with_buttons: Send notification with action buttons, return immediately with ID.
    get_reply: Check if the user has replied to a notification.
    clear_notifications: Clear all active notifications.

Launch:
    agent-core-notify           # stdio transport (for .mcp.json)

Register in .mcp.json:
    {
        "mcpServers": {
            "notify": {
                "command": "uv",
                "args": ["run", "--directory", "E:/workspaces/ai/agents/agent_core", "agent-core-notify"]
            }
        }
    }
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from desktop_notifier import (
    Attachment,
    Button,
    DesktopNotifier,
    Icon,
    ReplyField,
    Sound,
    Urgency,
)

# Logging to stderr — stdout is reserved for MCP stdio transport
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent_core.notify")

mcp = FastMCP("agent-core-notify")

# Shared notifier instance
_notifier = DesktopNotifier(app_name="Agent Core")

# Store for pending interactive notifications (id -> reply/button result)
_pending: dict[str, dict] = {}
# Reference to the running event loop for thread-safe callback signaling
_loop: asyncio.AbstractEventLoop | None = None

URGENCY_MAP = {
    "low": Urgency.Low,
    "normal": Urgency.Normal,
    "critical": Urgency.Critical,
}


def _make_sound(sound: str | None) -> Sound | None:
    """Create a Sound object — auto-detects file path vs system sound name."""
    if not sound:
        return None
    if Path(sound).suffix in (".wav", ".mp3", ".ogg", ".flac", ".aac", ".wma"):
        return Sound(path=Path(sound))
    return Sound(name=sound)


def _resolve_reply(notification_id: str, reply: str) -> None:
    """Thread-safe callback for reply fields."""
    if notification_id in _pending:
        _pending[notification_id]["reply"] = reply
        _pending[notification_id]["status"] = "replied"
        logger.info("Reply received for %s: %s", notification_id, reply)


def _resolve_button(notification_id: str, button: str) -> None:
    """Thread-safe callback for button presses."""
    if notification_id in _pending:
        _pending[notification_id]["reply"] = button
        _pending[notification_id]["status"] = "clicked"
        logger.info("Button clicked for %s: %s", notification_id, button)


def _resolve_dismissed(notification_id: str) -> None:
    """Thread-safe callback for dismissed notifications."""
    if notification_id in _pending:
        _pending[notification_id]["reply"] = ""
        _pending[notification_id]["status"] = "dismissed"
        logger.info("Notification dismissed: %s", notification_id)


@mcp.tool()
async def send_notification(
    title: str,
    message: str,
    urgency: str = "normal",
    icon: str | None = None,
    attachment: str | None = None,
    sound: str | None = None,
    thread: str | None = None,
) -> dict[str, str]:
    """Send a desktop toast notification (fire-and-forget).

    Args:
        title: Notification title (e.g., agent name).
        message: Notification body text.
        urgency: "low", "normal", or "critical". Default: "normal".
        icon: Path to an icon image file. Optional.
        attachment: Path to a file to attach. Optional.
        sound: Name of a system sound, or path to a sound file. Optional.
        thread: Thread ID to group related notifications. Optional.
    """
    urg = URGENCY_MAP.get(urgency, Urgency.Normal)
    icon_obj = Icon(path=Path(icon)) if icon else None
    attach_obj = Attachment(path=Path(attachment)) if attachment else None
    sound_obj = _make_sound(sound)

    await _notifier.send(
        title=title,
        message=message,
        urgency=urg,
        icon=icon_obj,
        attachment=attach_obj,
        sound=sound_obj,
        thread=thread,
    )

    logger.info("Notification sent: %s — %s", title, message)
    return {"status": "sent", "title": title, "message": message}


@mcp.tool()
async def ask_user(
    title: str,
    message: str,
    reply_button_title: str = "Reply",
    urgency: str = "normal",
    icon: str | None = None,
    sound: str | None = None,
    thread: str | None = None,
) -> dict[str, str]:
    """Send a notification with a reply field. Returns immediately with a notification_id.

    Use get_reply(notification_id) to check if the user has replied.
    This does NOT block — the agent can continue working while waiting.

    Args:
        title: Notification title.
        message: The question or prompt to show.
        reply_button_title: Label for the reply button. Default: "Reply".
        urgency: "low", "normal", or "critical". Default: "normal".
        icon: Path to an icon image file. Optional.
        sound: Name of a system sound, or path to a sound file. Optional.
        thread: Thread ID to group related notifications. Optional.
    """
    notification_id = str(uuid.uuid4())[:8]
    _pending[notification_id] = {"status": "pending", "reply": ""}

    urg = URGENCY_MAP.get(urgency, Urgency.Normal)
    icon_obj = Icon(path=Path(icon)) if icon else None
    sound_obj = _make_sound(sound)

    await _notifier.send(
        title=title,
        message=message,
        urgency=urg,
        icon=icon_obj,
        sound=sound_obj,
        thread=thread,
        reply_field=ReplyField(
            title="Type your reply",
            button_title=reply_button_title,
            on_replied=lambda text: _resolve_reply(notification_id, text),
        ),
        on_dismissed=lambda: _resolve_dismissed(notification_id),
    )

    logger.info("Ask sent [%s]: %s — %s", notification_id, title, message)
    return {"status": "pending", "notification_id": notification_id}


@mcp.tool()
async def notify_with_buttons(
    title: str,
    message: str,
    buttons: list[str],
    urgency: str = "normal",
    icon: str | None = None,
    sound: str | None = None,
    thread: str | None = None,
) -> dict[str, str]:
    """Send a notification with action buttons. Returns immediately with a notification_id.

    Use get_reply(notification_id) to check which button the user clicked.
    This does NOT block — the agent can continue working while waiting.

    Args:
        title: Notification title.
        message: Notification body text.
        buttons: List of button labels (e.g., ["Approve", "Deny", "Snooze"]).
        urgency: "low", "normal", or "critical". Default: "normal".
        icon: Path to an icon image file. Optional.
        sound: Name of a system sound, or path to a sound file. Optional.
        thread: Thread ID to group related notifications. Optional.
    """
    notification_id = str(uuid.uuid4())[:8]
    _pending[notification_id] = {"status": "pending", "reply": ""}

    button_objs = [
        Button(
            title=label,
            on_pressed=lambda lbl=label: _resolve_button(notification_id, lbl),
        )
        for label in buttons
    ]

    urg = URGENCY_MAP.get(urgency, Urgency.Normal)
    icon_obj = Icon(path=Path(icon)) if icon else None
    sound_obj = _make_sound(sound)

    await _notifier.send(
        title=title,
        message=message,
        urgency=urg,
        icon=icon_obj,
        sound=sound_obj,
        thread=thread,
        buttons=button_objs,
        on_dismissed=lambda: _resolve_dismissed(notification_id),
    )

    logger.info("Buttons sent [%s]: %s — %s [%s]", notification_id, title, message, ", ".join(buttons))
    return {"status": "pending", "notification_id": notification_id}


@mcp.tool()
async def get_reply(notification_id: str) -> dict[str, str]:
    """Check if the user has replied to or interacted with a notification.

    Returns the current status:
    - {"status": "pending"} — user hasn't responded yet
    - {"status": "replied", "reply": "user's text"} — user typed a reply
    - {"status": "clicked", "button": "Approve"} — user clicked a button
    - {"status": "dismissed"} — user dismissed the notification
    - {"status": "not_found"} — unknown notification_id

    Args:
        notification_id: The ID returned by ask_user or notify_with_buttons.
    """
    if notification_id not in _pending:
        return {"status": "not_found", "notification_id": notification_id}

    entry = _pending[notification_id]
    result = {"status": entry["status"], "notification_id": notification_id}

    if entry["status"] == "replied":
        result["reply"] = entry["reply"]
        del _pending[notification_id]
    elif entry["status"] == "clicked":
        result["button"] = entry["reply"]
        del _pending[notification_id]
    elif entry["status"] == "dismissed":
        del _pending[notification_id]

    return result


@mcp.tool()
async def clear_notifications() -> dict[str, str]:
    """Clear all active notifications sent by this server."""
    await _notifier.clear_all()
    _pending.clear()
    logger.info("All notifications cleared")
    return {"status": "cleared"}


def run() -> None:
    """Launch the MCP server on stdio transport."""
    mcp.run(transport="stdio")
