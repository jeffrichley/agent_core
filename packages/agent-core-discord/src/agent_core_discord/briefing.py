"""Canonical daily briefing embed dicts for Discord (cutover #03).

When Pepper's legacy ``send_briefing`` is not vendored in this repo, this module
is the **canonical** shape: field order inside the header embed is ``Focus`` then
``Calendar``; severity colors follow the ticket (15548997 critical, 16776960
warning). Any drift from Pepper should be reconciled in a follow-up parity pass.
"""

from __future__ import annotations

from typing import Any

# Discord embed integer colors (Pepper cutover #03 conventions).
BRIEFING_COLOR_CRITICAL = 15548997
BRIEFING_COLOR_WARNING = 16776960


def build_briefing_embeds(
    *,
    date_line: str,
    focus: str = "",
    calendar: str = "",
    critical_items: list[str] | None = None,
    warning_items: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return raw embed dicts ready for :meth:`discord.Embed.from_dict`.

    Order is stable: one header embed, then optional Critical (red), then
    optional Attention (yellow).
    """
    critical_items = list(critical_items or [])
    warning_items = list(warning_items or [])

    fields: list[dict[str, Any]] = []
    if focus.strip():
        fields.append({"name": "Focus", "value": focus.strip()[:1024], "inline": False})
    if calendar.strip():
        fields.append({"name": "Calendar", "value": calendar.strip()[:1024], "inline": False})

    header: dict[str, Any] = {
        "title": "Daily briefing",
        "description": date_line.strip()[:4096],
        "fields": fields,
    }

    out: list[dict[str, Any]] = [header]

    if critical_items:
        body = "\n".join(f"- {line.strip()[:500]}" for line in critical_items if line.strip())
        body = body[:4096] if body else "—"
        out.append(
            {
                "title": "Critical",
                "description": body,
                "color": BRIEFING_COLOR_CRITICAL,
            }
        )

    if warning_items:
        body = "\n".join(f"- {line.strip()[:500]}" for line in warning_items if line.strip())
        body = body[:4096] if body else "—"
        out.append(
            {
                "title": "Attention",
                "description": body,
                "color": BRIEFING_COLOR_WARNING,
            }
        )

    return out
