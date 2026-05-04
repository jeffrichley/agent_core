"""markdown_file — render brief sections to a markdown file on disk.

Strategy
--------
The canonical "always-works" destination. Even if Discord is down or
unconfigured, briefs still land on disk in a human-readable, parseable
shape. This destination does no networking and never touches the bus —
``bus_handle`` is accepted purely for protocol conformance and ignored.

Config
------
- ``path`` (str, required): destination file path. Supports delivery-time
  ``{{var}}`` substitution (distinct from the ``${var}`` config-load-time
  substitution that already happened in T2). Supported keys:

  - ``{{when.date}}`` — ISO date (``2026-05-04``)
  - ``{{when.iso}}`` — full ISO timestamp
  - ``{{when.year}}`` — 4-digit year (``2026``)
  - ``{{when.month}}`` — 2-digit month (``05``)
  - ``{{when.day}}`` — 2-digit day (``04``)
  - ``{{brief_type}}`` — playbook brief_type
  - ``{{scope}}`` — scope, or ``""`` if None

  Parent directories are created if missing. ``~`` is expanded.

- ``encoding`` (str, optional): defaults to ``"utf-8"``.

Output format
-------------
H1 with brief_type and ISO timestamp, optional ``_Scope: ..._`` line,
one H2 per section with its title, each field rendered as bold name +
value, and a footer line. Multi-line field values are preserved as-is
via ``str(value)``.

Failure semantics
-----------------
``Destination.deliver`` is best-effort per the brief framework spec.
Config errors, unknown template keys, and OS-level write failures are
captured in ``DeliveryResult(success=False, error=...)`` rather than
propagating. ``DeliveryResult.ref`` on success is the absolute path of
the written file.

Async I/O
---------
The actual ``write_text`` call runs in ``asyncio.to_thread`` to honor
T4's cancellation contract — a cancelled deliver doesn't block the
event loop on disk I/O.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agent_core_briefs.protocol import DeliveryResult, PlaybookRef

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle


_TEMPLATE_PATTERN = re.compile(r"\{\{([\w.]+)\}\}")


class MarkdownFileDestination:
    """Render brief sections to a markdown file.

    Config:
    - ``path`` (str): destination file path. Supports ``{{when.date}}``,
      ``{{when.iso}}``, ``{{when.year}}``, ``{{when.month}}``,
      ``{{when.day}}``, ``{{brief_type}}``, ``{{scope}}`` substitution.
      Parent directories are created if missing.
    - ``encoding`` (str, optional): defaults to ``"utf-8"``.
    """

    type_id = "markdown_file"

    async def deliver(
        self,
        sections: list[dict],
        playbook: PlaybookRef,
        scope: str | None,
        when: datetime,
        config: dict,
        bus_handle: BusHandle,  # not used; included for protocol conformance
    ) -> DeliveryResult:
        """Resolve the path template, render markdown, write to disk.

        Empty ``sections`` still produces a file (H1 + optional scope +
        footer, no H2 sections) so audit trails always have an artifact
        for a triggered brief.
        """
        del bus_handle  # explicit: this destination never publishes

        try:
            path_template = config["path"]
        except KeyError:
            return DeliveryResult(
                success=False,
                error="markdown_file: config missing 'path'",
            )
        encoding = config.get("encoding", "utf-8")

        try:
            path = self._resolve_path(path_template, playbook=playbook, scope=scope, when=when)
        except ValueError as exc:
            return DeliveryResult(success=False, error=str(exc))

        content = self._render_markdown(sections, playbook=playbook, scope=scope, when=when)

        try:
            await asyncio.to_thread(self._write_file, path, content, encoding)
        except OSError as exc:
            return DeliveryResult(
                success=False,
                error=f"markdown_file: write failed at {path}: {exc}",
            )

        return DeliveryResult(success=True, ref=str(path.resolve()))

    @staticmethod
    def _write_file(path: Path, content: str, encoding: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)

    @staticmethod
    def _resolve_path(
        template: str,
        *,
        playbook: PlaybookRef,
        scope: str | None,
        when: datetime,
    ) -> Path:
        substitutions = {
            "when.date": when.date().isoformat(),
            "when.iso": when.isoformat(),
            "when.year": f"{when.year:04d}",
            "when.month": f"{when.month:02d}",
            "when.day": f"{when.day:02d}",
            "brief_type": playbook.brief_type,
            "scope": scope or "",
        }

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in substitutions:
                raise ValueError(
                    f"markdown_file: unknown template key {{{{{key}}}}} "
                    f"(known: {sorted(substitutions)})"
                )
            return substitutions[key]

        resolved = _TEMPLATE_PATTERN.sub(_replace, template)
        return Path(resolved).expanduser()

    @staticmethod
    def _render_markdown(
        sections: list[dict],
        *,
        playbook: PlaybookRef,
        scope: str | None,
        when: datetime,
    ) -> str:
        lines: list[str] = []
        lines.append(f"# {playbook.brief_type} — {when.isoformat()}")
        lines.append("")
        if scope is not None:
            lines.append(f"_Scope: {scope}_")
            lines.append("")
        for section in sections:
            title = section.get("title", "")
            lines.append(f"## {title}")
            lines.append("")
            for field in section.get("fields", []):
                name = field.get("name", "")
                value = field.get("value", "")
                lines.append(f"**{name}**")
                lines.append("")
                lines.append(str(value))
                lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"_Generated by agent_core_briefs at {when.isoformat()}_")
        lines.append("")
        return "\n".join(lines)
