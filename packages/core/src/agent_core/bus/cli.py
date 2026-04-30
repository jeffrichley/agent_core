"""CLI for `agent-core bus *` subcommands."""

from __future__ import annotations

import asyncio
import logging
import re
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agent_core.bus.persistence import Persistence
from agent_core.bus.runner import BusBootError, build_bus_from_config

app = typer.Typer(help="Bus operations: run, status, mailbox, trace, dlq, replay.")
console = Console()
log = logging.getLogger(__name__)

_RUN_CONFIG_OPTION = typer.Option(
    Path("./agent_core.yaml"),
    "--config",
    "-c",
    help="Path to agent_core.yaml",
    exists=True,
    readable=True,
)


@app.command()
def run(
    config: Path = _RUN_CONFIG_OPTION,
) -> None:
    """Start the bus and all configured endpoints. Runs until SIGINT/SIGTERM."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(_run_bus(config))
    except BusBootError as exc:
        console.print(f"[red]boot error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


async def _run_bus(config_path: Path) -> None:
    bus, http_host = await build_bus_from_config(config_path)
    if http_host is not None:
        await http_host.start()
    try:
        await bus.start()
        endpoint_count = len(bus._endpoints_by_name)
        host_str = f" + http on :{http_host.port}" if http_host else ""
        console.print(
            f"[green]bus running[/green] — {endpoint_count} endpoint(s){host_str}; "
            "press Ctrl+C to stop."
        )

        stop_event = asyncio.Event()

        def _shutdown(*_):
            stop_event.set()

        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _shutdown)
            loop.add_signal_handler(signal.SIGTERM, _shutdown)
        except NotImplementedError:
            pass  # Windows — SIGINT raises KeyboardInterrupt directly.

        async def _ttl_loop():
            while not stop_event.is_set():
                try:
                    await bus.run_ttl_sweep_once()
                except Exception:
                    log.exception("TTL sweep failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=bus.config.ttl_sweep_seconds)
                except TimeoutError:
                    pass

        async def _redelivery_loop():
            while not stop_event.is_set():
                try:
                    await bus.run_redelivery_sweep_once()
                except Exception:
                    log.exception("redelivery sweep failed")
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=bus.config.redelivery_sweep_seconds
                    )
                except TimeoutError:
                    pass

        sweeps = [asyncio.create_task(_ttl_loop()), asyncio.create_task(_redelivery_loop())]
        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            for t in sweeps:
                t.cancel()
            await asyncio.gather(*sweeps, return_exceptions=True)
            await bus.stop()
    finally:
        if http_host is not None:
            await http_host.stop()
        console.print("[yellow]bus stopped[/yellow]")


def _config_option():
    return typer.Option(
        Path("./agent_core.yaml"),
        "--config",
        "-c",
        exists=True,
        readable=True,
    )


@app.command()
def status(config: Path = _RUN_CONFIG_OPTION):
    """Show endpoints, in-flight count, and DLQ depth."""
    asyncio.run(_status(config))


async def _status(config_path: Path) -> None:
    bus, _ = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        # Endpoint table
        ep_table = Table(title="Endpoints")
        ep_table.add_column("name")
        ep_table.add_column("description")
        ep_table.add_column("pending")
        for spec in bus._endpoints_by_name.values():
            count = await store.count_pending(spec.name)
            ep_table.add_row(spec.name, spec.description, str(count))
        console.print(ep_table)

        # Aggregate counts
        async with store._conn.execute(
            "SELECT state, COUNT(*) FROM envelopes GROUP BY state"
        ) as cur:
            rows = await cur.fetchall()
        agg = Table(title="State counts")
        agg.add_column("state")
        agg.add_column("count")
        for state, count in rows:
            agg.add_row(state, str(count))
        console.print(agg)
    finally:
        await store.close()


@app.command()
def mailbox(
    endpoint: str = typer.Argument(..., help="Endpoint name to inspect"),
    config: Path = _RUN_CONFIG_OPTION,
):
    """List pending envelopes for an endpoint."""
    asyncio.run(_mailbox(endpoint, config))


async def _mailbox(endpoint: str, config_path: Path) -> None:
    bus, _ = await build_bus_from_config(config_path)
    if endpoint not in bus._endpoints_by_name:
        console.print(f"[red]unknown endpoint:[/red] {endpoint}")
        raise typer.Exit(code=1)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        pending = await store.list_pending(endpoint)
        if not pending:
            console.print(f"[dim]mailbox '{endpoint}' is empty[/dim]")
            return
        table = Table(title=f"Mailbox: {endpoint} ({len(pending)} pending)")
        table.add_column("id")
        table.add_column("from")
        table.add_column("kind")
        table.add_column("created_at")
        for env in pending:
            table.add_row(env.id, env.from_, env.kind, env.created_at.isoformat())
        console.print(table)
    finally:
        await store.close()


@app.command()
def trace(
    correlation_id: str = typer.Argument(..., help="correlation_id to trace"),
    config: Path = _RUN_CONFIG_OPTION,
):
    """Show all envelopes in a correlation_id thread, in arrival order."""
    asyncio.run(_trace(correlation_id, config))


async def _trace(correlation_id: str, config_path: Path) -> None:
    bus, _ = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        thread = await store.list_by_correlation(correlation_id)
        if not thread:
            console.print(f"[dim]no envelopes found for correlation_id={correlation_id!r}[/dim]")
            return
        table = Table(title=f"Thread: {correlation_id}")
        table.add_column("id")
        table.add_column("from")
        table.add_column("to")
        table.add_column("kind")
        table.add_column("created_at")
        for env in thread:
            table.add_row(env.id, env.from_, env.to, env.kind, env.created_at.isoformat())
        console.print(table)
    finally:
        await store.close()


# Sub-app for `bus dlq` and `bus dlq purge`.
dlq_app = typer.Typer(help="Dead-letter operations.", invoke_without_command=True)
app.add_typer(dlq_app, name="dlq")


@dlq_app.callback(invoke_without_command=True)
def _dlq_default(ctx: typer.Context, config: Path = _RUN_CONFIG_OPTION):
    """List dead-letter envelopes (when `bus dlq` is invoked with no subcommand)."""
    if ctx.invoked_subcommand is None:
        asyncio.run(_dlq_list(config))


async def _dlq_list(config_path: Path) -> None:
    bus, _ = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        rows = await store.list_dead_letter()
        if not rows:
            console.print("[dim]DLQ is empty[/dim]")
            return
        table = Table(title=f"Dead-Letter Queue ({len(rows)})")
        table.add_column("id")
        table.add_column("from")
        table.add_column("to")
        table.add_column("kind")
        table.add_column("reason")
        for env in rows:
            row = await store.row(env.id)
            table.add_row(env.id, env.from_, env.to, env.kind, row["nack_reason"] or "")
        console.print(table)
    finally:
        await store.close()


@app.command()
def replay(
    envelope_id: str = typer.Argument(..., help="Envelope id to replay"),
    config: Path = _RUN_CONFIG_OPTION,
):
    """Reset a dead-letter envelope to pending. The next bus startup will redeliver it via drain_for."""
    asyncio.run(_replay(envelope_id, config))


async def _replay(envelope_id: str, config_path: Path) -> None:
    bus, _ = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        ok = await store.reset_for_replay(envelope_id)
        if not ok:
            console.print(f"[red]envelope {envelope_id!r} not found in DLQ[/red]")
            raise typer.Exit(code=1)
        console.print(f"[green]replayed:[/green] {envelope_id}")
    finally:
        await store.close()


_DURATION_RE = re.compile(r"^(\d+)([dhm])$")


def _parse_duration(s: str) -> timedelta:
    m = _DURATION_RE.match(s.strip().lower())
    if not m:
        raise typer.BadParameter(f"invalid duration: {s!r} (use e.g. '7d', '12h', '30m')")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(minutes=n)


@dlq_app.command("purge")
def dlq_purge(
    older_than: str = typer.Option(..., "--older-than"),
    config: Path = _RUN_CONFIG_OPTION,
):
    """Delete dead-letter envelopes older than the given duration (e.g. 7d, 24h)."""
    asyncio.run(_dlq_purge(older_than, config))


async def _dlq_purge(older_than: str, config_path: Path) -> None:
    bus, _ = await build_bus_from_config(config_path)
    store = Persistence(bus.config.storage_path)
    await store.connect()
    try:
        cutoff = datetime.now(UTC) - _parse_duration(older_than)
        n = await store.purge_dlq(older_than=cutoff)
        console.print(f"[green]purged {n} envelope(s) older than {older_than}[/green]")
    finally:
        await store.close()
