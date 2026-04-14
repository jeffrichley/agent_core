"""Desktop notification MCP server.

Provides toast notification tools that agents can call during a session.
Stays alive for the session lifetime, enabling interactive notifications
with reply fields and action buttons.

Tools:
    send_notification: Fire-and-forget toast notification.
    ask_user: Interactive notification with reply field — blocks until user replies or timeout.
    notify_with_buttons: Notification with action buttons — blocks until user clicks one.

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

# Shared notifier instance — reused across tool calls
_notifier = DesktopNotifier(app_name="Agent Core")

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
    timeout_seconds: int = 120,
    urgency: str = "normal",
    icon: str | None = None,
    sound: str | None = None,
    thread: str | None = None,
) -> dict[str, str]:
    """Send a notification with a reply field and wait for the user's response.

    Blocks until the user replies or the timeout expires. The user's reply
    text is returned in the response.

    Args:
        title: Notification title.
        message: The question or prompt to show.
        reply_button_title: Label for the reply button. Default: "Reply".
        timeout_seconds: How long to wait for a reply. Default: 120.
        urgency: "low", "normal", or "critical". Default: "normal".
        icon: Path to an icon image file. Optional.
        sound: Name of a system sound, or path to a sound file. Optional.
        thread: Thread ID to group related notifications. Optional.
    """
    reply_event = asyncio.Event()
    reply_text: list[str] = []

    def on_replied(text: str) -> None:
        reply_text.append(text)
        reply_event.set()

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
            on_replied=on_replied,
        ),
    )

    logger.info("Ask sent: %s — %s (waiting %ds for reply)", title, message, timeout_seconds)

    try:
        await asyncio.wait_for(reply_event.wait(), timeout=timeout_seconds)
        user_reply = reply_text[0] if reply_text else ""
        logger.info("Reply received: %s", user_reply)
        return {"status": "replied", "reply": user_reply}
    except asyncio.TimeoutError:
        logger.info("Reply timed out after %ds", timeout_seconds)
        return {"status": "timeout", "reply": ""}


@mcp.tool()
async def notify_with_buttons(
    title: str,
    message: str,
    buttons: list[str],
    timeout_seconds: int = 120,
    urgency: str = "normal",
    icon: str | None = None,
    sound: str | None = None,
    thread: str | None = None,
) -> dict[str, str]:
    """Send a notification with action buttons and wait for the user's choice.

    Blocks until the user clicks a button, dismisses the notification,
    or the timeout expires.

    Args:
        title: Notification title.
        message: Notification body text.
        buttons: List of button labels (e.g., ["Approve", "Deny", "Snooze"]).
        timeout_seconds: How long to wait for a click. Default: 120.
        urgency: "low", "normal", or "critical". Default: "normal".
        icon: Path to an icon image file. Optional.
        sound: Name of a system sound, or path to a sound file. Optional.
        thread: Thread ID to group related notifications. Optional.
    """
    choice_event = asyncio.Event()
    chosen: list[str] = []

    def make_handler(label: str):
        def handler() -> None:
            chosen.append(label)
            choice_event.set()
        return handler

    def on_dismissed() -> None:
        chosen.append("dismissed")
        choice_event.set()

    button_objs = [
        Button(title=label, on_pressed=make_handler(label))
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
        on_dismissed=on_dismissed,
    )

    logger.info("Buttons sent: %s — %s [%s] (waiting %ds)", title, message, ", ".join(buttons), timeout_seconds)

    try:
        await asyncio.wait_for(choice_event.wait(), timeout=timeout_seconds)
        choice = chosen[0] if chosen else "unknown"
        logger.info("Button clicked: %s", choice)
        return {"status": "clicked", "button": choice}
    except asyncio.TimeoutError:
        logger.info("Button choice timed out after %ds", timeout_seconds)
        return {"status": "timeout", "button": ""}


@mcp.tool()
async def clear_notifications() -> dict[str, str]:
    """Clear all active notifications sent by this server."""
    await _notifier.clear_all()
    logger.info("All notifications cleared")
    return {"status": "cleared"}


def run() -> None:
    """Launch the MCP server on stdio transport."""
    mcp.run(transport="stdio")
