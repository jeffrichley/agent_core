"""``agent-core briefs`` Typer subapp (cutover #09 / T15).

Three operator-facing subcommands for exercising the brief framework
without going through the bus:

* ``briefs compose`` — fire a brief composition in-process and print the
  ``ComposeBrief`` data dict. Useful for debugging playbooks + gather
  configs before wiring an orchestrator on the bus.
* ``briefs fetchers list`` — show every fetcher discovered under one or
  more ``--fetcher-path`` directories (type_id, namespace, source,
  last-modified).
* ``briefs fetchers test`` — run a single fetcher in isolation against an
  on-disk YAML config and print the namespace-merged context dict.

Standalone by design: T15 ships before T16 wires the framework into
``agent_core.yaml``, so this CLI takes paths directly via repeatable
flags rather than reading config. The shape mirrors
``agent-core bus-log`` (cutover #04) — Annotated typer flags, JSON
output, ``BadParameter`` / ``Exit(1)`` for bad input rather than raw
tracebacks.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from agent_core_briefs.engine import FetcherInvocation, gather_context
from agent_core_briefs.loader import LoaderError, discover_implementations
from agent_core_briefs.mcp import compose_brief
from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint
from agent_core_briefs.protocol import Fetcher

briefs_app = typer.Typer(
    name="briefs",
    help="Brief framework — compose + inspect fetchers (cutover #09).",
    no_args_is_help=True,
)

# Built-in fetcher source directory. The orchestrator auto-prepends this
# to ``fetcher_paths`` at boot; the CLI mirrors that contract so
# ``briefs fetchers list/test`` always sees ``cli`` / ``filesystem_read``
# / ``now`` even without an explicit ``--fetcher-path``. Operators get
# the built-ins for free; ``--fetcher-path`` adds custom directories on
# top.
_BUILTIN_FETCHERS_DIR = Path(__file__).parent / "fetchers"

fetchers_app = typer.Typer(
    name="fetchers",
    help="Inspect and exercise registered fetchers in isolation.",
    no_args_is_help=True,
)
briefs_app.add_typer(fetchers_app, name="fetchers")


# ---- helpers ----


def _validate_fetcher_paths(paths: list[Path]) -> list[Path]:
    """Each ``--fetcher-path`` must exist and be a directory.

    Surface a typo-induced bad path as a CLI boundary error rather than
    letting the loader silently warn-and-skip — the operator asked us to
    look here, so refusing to find it is wrong.
    """
    for p in paths:
        if not p.exists():
            raise typer.BadParameter(
                f"path does not exist: {p}",
                param_hint="--fetcher-path",
            )
        if not p.is_dir():
            raise typer.BadParameter(
                f"path is not a directory: {p}",
                param_hint="--fetcher-path",
            )
    return paths


def _load_fetcher_catalog(paths: list[Path]) -> dict[str, type[Fetcher]]:
    """Wrap :func:`discover_implementations` with operator-friendly errors.

    Always prepends ``_BUILTIN_FETCHERS_DIR`` so the three built-ins
    (``cli``, ``filesystem_read``, ``now``) are discoverable without an
    explicit ``--fetcher-path`` — mirrors the orchestrator's contract
    (operators don't have to copy package source into their custom
    fetcher dirs).

    ``LoaderError`` (duplicate type_id, syntax error, etc.) is re-raised as
    ``BadParameter`` so the operator sees a one-line surface instead of a
    traceback.
    """
    resolved = [_BUILTIN_FETCHERS_DIR, *paths]
    try:
        return discover_implementations(resolved, protocol=Fetcher)
    except LoaderError as exc:
        raise typer.BadParameter(str(exc), param_hint="--fetcher-path") from exc


def _resolve_when(when: str | None) -> datetime:
    """Parse ``--when`` (ISO-8601) or default to now (UTC). Naive → UTC."""
    if when is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(when)
    except ValueError as exc:
        raise typer.BadParameter(
            f"must be ISO-8601: {when}",
            param_hint="--when",
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_vars(entries: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--var KEY=VALUE`` flags into a dict.

    Splits on the first ``=`` so values may contain ``=`` characters.
    Empty keys reject loudly — those would be a typo, not an intent.
    """
    out: dict[str, str] = {}
    for raw in entries or []:
        if "=" not in raw:
            raise typer.BadParameter(
                f"must be KEY=VALUE, got {raw!r}",
                param_hint="--var",
            )
        key, _, value = raw.partition("=")
        if not key:
            raise typer.BadParameter(
                f"empty KEY: {raw!r}",
                param_hint="--var",
            )
        out[key] = value
    return out


def _emit_json(obj: Any) -> None:
    """JSON-dump with ``default=str`` for datetimes / Paths."""
    typer.echo(json.dumps(obj, indent=2, default=str))


# ---- briefs compose ----


@briefs_app.command("compose")
def compose(
    brief_type: Annotated[
        str,
        typer.Option("--type", help="Playbook brief type to compose."),
    ],
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            help="Target agent name (used as default_target_agent on the orchestrator).",
        ),
    ],
    playbooks_path: Annotated[
        Path,
        typer.Option(
            "--playbooks-path",
            help="Directory of playbook .md files (one per brief_type).",
        ),
    ],
    fetcher_path: Annotated[
        list[Path] | None,
        typer.Option(
            "--fetcher-path",
            help="Directory of fetcher modules. Repeatable.",
        ),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option("--scope", help="Brief scope (echoed into the result)."),
    ] = None,
    when: Annotated[
        str | None,
        typer.Option("--when", help="ISO-8601 timestamp; defaults to now (UTC)."),
    ] = None,
    var: Annotated[
        list[str] | None,
        typer.Option(
            "--var",
            help="KEY=VALUE substitution for playbook + gather config. Repeatable.",
        ),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help=(
                "Default per-fetcher timeout in seconds (matches production orchestrator "
                "default; gather config can override per-fetcher)."
            ),
        ),
    ] = 300.0,
) -> None:
    """Run a brief composition in-process and print the ComposeBrief data dict.

    Bypasses the bus entirely: builds an orchestrator from the supplied
    paths, calls ``compose_brief`` (the same self-launch path the agent's
    MCP tool uses), and emits the result as JSON. Useful for verifying a
    playbook + gather config produce the expected context before wiring
    them onto a running agent.
    """
    if not playbooks_path.exists() or not playbooks_path.is_dir():
        raise typer.BadParameter(
            f"playbooks-path must be an existing directory: {playbooks_path}",
            param_hint="--playbooks-path",
        )

    fetcher_paths = _validate_fetcher_paths(list(fetcher_path or []))
    vars_map = _parse_vars(var)
    when_resolved = _resolve_when(when)
    catalog = _load_fetcher_catalog(fetcher_paths)

    orchestrator = BriefsOrchestratorEndpoint(
        name="briefs.cli",
        playbooks_path=playbooks_path,
        vars_map=vars_map,
        fetcher_catalog=catalog,
        default_target_agent=agent,
        default_timeout_seconds=timeout,
    )

    try:
        compose_data = asyncio.run(
            compose_brief(
                orchestrator=orchestrator,
                brief_type=brief_type,
                scope=scope,
                when=when_resolved,
            )
        )
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc), param_hint="--type") from exc
    except ValueError as exc:
        # compose_brief raises ValueError for unknown brief_type and
        # other input-shape problems — surface as bad CLI input rather
        # than a traceback.
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _emit_json(compose_data)


# ---- briefs fetchers list ----


@fetchers_app.command("list")
def list_fetchers(
    fetcher_path: Annotated[
        list[Path] | None,
        typer.Option(
            "--fetcher-path",
            help="Directory of fetcher modules. Repeatable.",
        ),
    ] = None,
) -> None:
    """List discovered fetchers as JSON (type_id, namespace, source, last_modified)."""
    paths = _validate_fetcher_paths(list(fetcher_path or []))
    catalog = _load_fetcher_catalog(paths)

    rows: list[dict[str, Any]] = []
    for type_id, cls in catalog.items():
        source = inspect.getsourcefile(cls) or inspect.getfile(cls)
        try:
            mtime = Path(source).stat().st_mtime if source else None
            last_modified = (
                datetime.fromtimestamp(mtime, tz=UTC).isoformat() if mtime is not None else None
            )
        except OSError:
            last_modified = None
        rows.append(
            {
                "type_id": type_id,
                "namespace": getattr(cls, "namespace", ""),
                "source": source,
                "last_modified": last_modified,
            }
        )

    rows.sort(key=lambda r: r["type_id"])
    _emit_json(rows)


# ---- briefs fetchers test ----


@fetchers_app.command("test")
def test_fetcher(
    brief_type: Annotated[
        str,
        typer.Option("--type", help="Fetcher type_id to invoke."),
    ],
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            help="YAML file containing the fetcher's config dict.",
        ),
    ],
    fetcher_path: Annotated[
        list[Path] | None,
        typer.Option(
            "--fetcher-path",
            help="Directory of fetcher modules. Repeatable.",
        ),
    ] = None,
    when: Annotated[
        str | None,
        typer.Option("--when", help="ISO-8601 timestamp; defaults to now (UTC)."),
    ] = None,
    namespace: Annotated[
        str | None,
        typer.Option(
            "--namespace",
            help="Override the fetcher class's namespace for this run.",
        ),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Fetcher timeout in seconds (matches production orchestrator default of 300s).",
        ),
    ] = 300.0,
) -> None:
    """Run a single fetcher in isolation; print its namespace-merged context.

    The ``--config`` YAML is the same shape that would go under a
    fetcher's ``config:`` block in a gather_config.yaml. The output is
    the ``gather_context`` result — i.e., the fetcher's payload nested
    under its namespace, or ``{"_errors": {...}}`` on failure.
    """
    if not config.exists():
        raise typer.BadParameter(
            f"config file does not exist: {config}",
            param_hint="--config",
        )
    try:
        raw_config = yaml.safe_load(config.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise typer.BadParameter(
            f"failed to parse YAML in {config}: {exc}",
            param_hint="--config",
        ) from exc
    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise typer.BadParameter(
            f"config must be a YAML mapping at top level, got {type(raw_config).__name__}",
            param_hint="--config",
        )

    paths = _validate_fetcher_paths(list(fetcher_path or []))
    catalog = _load_fetcher_catalog(paths)

    cls = catalog.get(brief_type)
    if cls is None:
        known = sorted(catalog)
        raise typer.BadParameter(
            f"unknown type_id {brief_type!r} (loaded: {known})",
            param_hint="--type",
        )

    when_resolved = _resolve_when(when)
    invocation = FetcherInvocation(
        fetcher=cls(),
        config=raw_config,
        timeout_seconds=timeout,
        namespace_override=namespace,
    )
    result = asyncio.run(gather_context([invocation], when=when_resolved))
    _emit_json(result)
