"""``agent-core bus-log`` Typer subapp."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer

from agent_core.bus_log import bootstrap_default_projectors, iter_for_agent
from agent_core.bus_log.reader import iter_envelopes
from agent_core.bus_log.writer import default_log_root

bus_log_app = typer.Typer(
    name="bus-log",
    help="Inspect the bus daily JSONL log (cutover #04).",
    no_args_is_help=True,
)


@bus_log_app.command("show")
def show(
    agent: Annotated[
        str,
        typer.Option("--agent", help="Agent name (perspective for filtering and projection)."),
    ],
    date: Annotated[
        str | None,
        typer.Option("--date", help="YYYY-MM-DD (in --timezone). Defaults to today."),
    ] = None,
    log_root: Annotated[
        Path | None,
        typer.Option("--log-root", help="Daily JSONL directory. Defaults to ~/.agent-core/bus/raw."),
    ] = None,
    timezone: Annotated[
        str,
        typer.Option("--timezone", help="IANA timezone for date interpretation and ts rendering."),
    ] = "US/Eastern",
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Output full envelopes (not projected Tool 3 rows)."),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Last N rows only."),
    ] = None,
) -> None:
    """Show bus log entries for AGENT on DATE.

    Default (projected) output: Tool 3-shaped rows ready for the
    reflection job. ``--raw`` emits full envelope JSON for debugging.
    """
    bootstrap_default_projectors()

    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise typer.BadParameter(
            f"Unknown timezone: {timezone}",
            param_hint="--timezone",
        ) from exc

    if date is not None:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            raise typer.BadParameter(
                f"--date must be YYYY-MM-DD: {date}",
                param_hint="--date",
            ) from exc

    if limit is not None and limit < 1:
        raise typer.BadParameter("--limit must be >= 1", param_hint="--limit")

    root = log_root if log_root is not None else default_log_root()
    target_date = date or datetime.now(UTC).astimezone(ZoneInfo(timezone)).date().isoformat()
    path = root / f"{target_date}.jsonl"

    items: Iterable[dict[str, Any]]
    if raw:
        items = (env.model_dump(by_alias=True, mode="json") for env in iter_envelopes(path))
        # Filter to the agent's perspective in raw mode too — operator usually wants their slice.
        items = (
            obj for obj in items
            if obj.get("to") == agent or obj.get("from") == agent
        )
    else:
        items = iter_for_agent(path, agent=agent, projected=True, timezone=timezone)

    rows = list(items)
    if limit is not None:
        rows = rows[-limit:]
    for row in rows:
        typer.echo(json.dumps(row))
