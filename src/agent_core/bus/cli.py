"""CLI for `agent-core bus *` subcommands."""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

import typer
from rich.console import Console

from agent_core.bus.runner import BusBootError, build_bus_from_config

app = typer.Typer(help="Bus operations: run, status, mailbox, trace, dlq, replay.")
console = Console()
log = logging.getLogger(__name__)


@app.command()
def run(
    config: Path = typer.Option(
        Path("./agent_core.yaml"),
        "--config",
        "-c",
        help="Path to agent_core.yaml",
        exists=True,
        readable=True,
    ),
) -> None:
    """Start the bus and all configured endpoints. Runs until SIGINT/SIGTERM."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(_run_bus(config))
    except BusBootError as exc:
        console.print(f"[red]boot error:[/red] {exc}")
        raise typer.Exit(code=1)


async def _run_bus(config_path: Path) -> None:
    bus = await build_bus_from_config(config_path)
    await bus.start()
    console.print(
        f"[green]bus running[/green] — {len(bus._endpoints_by_name)} endpoint(s); "
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
        # Windows — fall through; SIGINT will raise KeyboardInterrupt.
        pass

    # Sweep tasks
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
        console.print("[yellow]bus stopped[/yellow]")
