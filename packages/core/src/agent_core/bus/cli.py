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
from agent_core.bus.watchdog import Watchdog

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
    watchdog = Watchdog(  # pragma: no cover
        bus.config.watchdog_timeout_seconds,
        heartbeat_path=bus.config.storage_path.parent / "watchdog_heartbeat",
    )
    watchdog.start()  # pragma: no cover
    try:
        await bus.start()

        # Install shutdown handlers BEFORE announcing readiness. The
        # "bus running" line is the barrier that operators (and
        # test_run_starts_and_stops_on_sigint) wait on before sending SIGINT.
        # If the handlers were registered *after* that line, a SIGINT landing in
        # the window between them would hit Python's default handler and
        # terminate the process with exit code 130 instead of shutting down
        # cleanly (returncode 0). Registering first makes the readiness line a
        # true barrier — closing the race for the test and for real Ctrl+C.
        stop_event = asyncio.Event()

        def _shutdown(*_):
            stop_event.set()

        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _shutdown)
            loop.add_signal_handler(signal.SIGTERM, _shutdown)
        except NotImplementedError:
            pass  # Windows — SIGINT raises KeyboardInterrupt directly.

        # The readiness announcement only runs inside a live `bus run` process.
        # Its end-to-end coverage comes from test_run_starts_and_stops_on_sigint,
        # which spawns a real subprocess — so in-process pytest-cov can't see
        # these lines across the process boundary. Excluded from the patch-
        # coverage gate rather than faked by a redundant in-process test.
        # (A proper subprocess-coverage setup would let us drop these pragmas.)
        endpoint_count = len(bus._endpoints_by_name)  # pragma: no cover
        host_str = f" + http on :{http_host.port}" if http_host else ""  # pragma: no cover
        console.print(  # pragma: no cover
            f"[green]bus running[/green] — {endpoint_count} endpoint(s){host_str}; "
            "press Ctrl+C to stop."
        )

        async def _ttl_loop():
            while not stop_event.is_set():
                watchdog.heartbeat()  # pragma: no cover
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
                watchdog.heartbeat()  # pragma: no cover
                try:
                    await bus.run_redelivery_sweep_once()
                except Exception:
                    log.exception("redelivery sweep failed")
                try:
                    await bus.run_supervisor_tick_once()
                except Exception:
                    log.exception("supervisor tick failed")
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
        watchdog.stop()  # pragma: no cover
        if http_host is not None:
            await http_host.stop()
        console.print("[yellow]bus stopped[/yellow]")


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
        conn = store._require_conn()
        async with conn.execute(
            "SELECT state, COUNT(*) FROM envelopes GROUP BY state"
        ) as cur:
            rows = await cur.fetchall()
        agg = Table(title="State counts")
        agg.add_column("state")
        agg.add_column("count")
        for state, count in rows:
            agg.add_row(state, str(count))
        console.print(agg)

        # Degraded endpoints table (only shown when any exist)
        degraded = await store.list_supervisor_degraded()
        if degraded:
            deg_table = Table(title="Degraded Endpoints")
            deg_table.add_column("name")
            deg_table.add_column("last_error")
            deg_table.add_column("since")
            for row in degraded:
                deg_table.add_row(row["name"], row["last_error"] or "", row["updated_at"])
            console.print(deg_table)

        # Heartbeat freshness (watchdog_heartbeat file written by _run_bus).
        heartbeat_path = bus.config.storage_path.parent / "watchdog_heartbeat"
        if heartbeat_path.exists():
            try:
                ts_str = heartbeat_path.read_text(encoding="utf-8").strip()
                last_beat = datetime.fromisoformat(ts_str)
                age_s = (datetime.now(UTC) - last_beat).total_seconds()
                console.print(f"last heartbeat: {age_s:.0f}s ago")
            except (ValueError, OSError):
                console.print("last heartbeat: [dim]unreadable[/dim]")
        else:
            console.print(
                "last heartbeat: [dim]no file (bus not running or watchdog disabled)[/dim]"
            )
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


# Sub-app for `bus dlq list` and `bus dlq purge`. We deliberately do NOT
# declare a parent callback that consumes --config: Click attaches options
# to the most-specific command they appear under, so a parent --config plus
# a child --config conflict (parent eats the value, child gets its
# `exists=True` default which doesn't exist in cwd). Each subcommand owns
# its own --config; `bus dlq` with no subcommand shows help.
dlq_app = typer.Typer(help="Dead-letter operations: list, purge.")
app.add_typer(dlq_app, name="dlq")


@dlq_app.command("list")
def dlq_list(config: Path = _RUN_CONFIG_OPTION):
    """List dead-letter envelopes."""
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
            reason = row["nack_reason"] if row is not None else ""
            table.add_row(env.id, env.from_, env.to, env.kind, reason or "")
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
