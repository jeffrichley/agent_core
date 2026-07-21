"""Lifecycle mixin for DiscordEndpoint.

Move-only extraction from endpoint.py (issue #440, Step 2 of F-B6).
Imports nothing from endpoint.py to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_core_credentials.secrets import SecretNotFoundError
from agent_core_credentials.secrets import get as get_secret
from agent_core_discord._state import _EndpointState
from agent_core_discord.access import AccessConfig, _build_access_config, load_access_config

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)
_active_endpoints: dict[str, Any] = {}


class _LifecycleMixin(_EndpointState):
    def _sweep_recent_inbounds_once(self) -> int:
        """Evict entries older than TTL; return count evicted.

        Walks oldest-first by insertion order; breaks at first non-stale
        entry (same shape as _sweep_pending_acks_once).
        """
        now = time.monotonic()
        ttl = self._recent_inbounds_ttl_seconds
        evicted = 0
        while self._recent_inbounds:
            oldest_id = next(iter(self._recent_inbounds))
            if now - self._recent_inbounds_timestamps.get(oldest_id, now) <= ttl:
                break
            self._recent_inbounds.popitem(last=False)
            self._recent_inbounds_timestamps.pop(oldest_id, None)
            evicted += 1
        return evicted

    def _sweep_pending_acks_once(self, *, now: float | None = None) -> int:
        """One pass of TTL eviction. Returns count evicted.

        Walks the OrderedDict from oldest to newest. Since insertion order
        is monotonic, we can break as soon as we find a non-stale entry.
        Eviction fires `_remote_remove_ack` as a fire-and-forget task.
        """
        now = now if now is not None else time.monotonic()
        cutoff = now - self.pending_acks_ttl_seconds
        evicted = 0
        while self._pending_acks:
            head_id = next(iter(self._pending_acks))
            emoji, channel_id, ts = self._pending_acks[head_id]
            if ts >= cutoff:
                break
            self._pending_acks.pop(head_id)
            self._awaiting_reply_ids.discard(head_id)
            self._awaiting_reply_ids_timestamps.pop(head_id, None)
            assert self._handle is not None
            self._handle.spawn(
                self._remote_remove_ack(head_id, emoji, channel_id),
                name=f"discord-endpoint-{self.name}-ttl-ack",
            )
            evicted += 1
        return evicted

    def _sweep_attachments_once(self) -> int:
        """One retention pass over <attachments_dir>.

        Age first: delete any <env_id>/ dir whose mtime is older than
        attachment_retention_days. Then size cap: while total bytes exceed
        attachment_max_total_bytes, delete whole dirs oldest-first by mtime.
        Whole-directory deletes only; never partial. A failed delete is
        logged and skipped — the sweep never raises into its loop.
        Returns the number of directories evicted.
        """
        import shutil

        root = self.attachments_dir
        try:
            if not root.exists():
                return 0
            root_resolved = root.resolve()
            entries = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            return 0

        def _safe_rmtree(d: Path) -> bool:
            try:
                if d.resolve().parent != root_resolved:
                    return False  # never walk outside the attachments root
                shutil.rmtree(d)
                return True
            except Exception:
                log.exception(
                    "discord(%s): attachment sweep failed to delete %s",
                    self.name,
                    d,
                )
                return False

        evicted = 0
        cutoff = time.time() - (self.attachment_retention_days * 86400)
        survivors: list[tuple[float, int, Path]] = []
        for d in entries:
            try:
                mtime = d.stat().st_mtime
                size = sum(
                    f.stat().st_size for f in d.rglob("*") if f.is_file()
                )
            except OSError:
                continue
            if mtime < cutoff:
                if _safe_rmtree(d):
                    evicted += 1
            else:
                survivors.append((mtime, size, d))

        total = sum(s for _, s, _ in survivors)
        if total > self.attachment_max_total_bytes:
            survivors.sort(key=lambda t: t[0])  # oldest first
            for _mtime, size, d in survivors:
                if total <= self.attachment_max_total_bytes:
                    break
                if _safe_rmtree(d):
                    total -= size
                    evicted += 1
        return evicted

    async def _pending_acks_sweep_loop(self) -> None:
        """Periodic TTL sweep. Runs until cancelled by stop()."""
        try:
            while True:
                await asyncio.sleep(self.pending_acks_sweep_seconds)
                try:
                    self._sweep_pending_acks_once()
                except Exception:
                    log.exception("discord endpoint '%s': sweep iteration failed", self.name)
        except asyncio.CancelledError:
            raise

    async def _access_config_reload_loop(self) -> None:
        """Periodic mtime-poll reload of access_config_path. Runs until cancelled by stop().

        Pre-validates JSON before swapping self._access so a partial write does not
        open the gate to permissive defaults. Keeps the previous config on any read or
        parse error; retries on the next poll cycle.
        """
        try:
            while True:
                await asyncio.sleep(self.access_config_reload_interval)
                if self.access_config_path is None:
                    continue
                try:
                    st = self.access_config_path.stat()
                    current_mtime = (st.st_mtime, st.st_size)
                except OSError:
                    continue
                if current_mtime == self._access_config_mtime:
                    continue
                # File changed — parse once; any error keeps previous config and retries.
                try:
                    raw_text = self.access_config_path.read_text(encoding="utf-8")
                    new_access = _build_access_config(
                        json.loads(raw_text), str(self.access_config_path)
                    )
                except Exception as exc:
                    log.warning(
                        "discord(%s): access config reload skipped (read/parse/schema error), "
                        "keeping previous config: %s",
                        self.name,
                        exc,
                    )
                    continue
                self._access = new_access
                self._access_config_mtime = current_mtime
                log.info(
                    "discord(%s): access config reloaded (channels=%d, dmPolicy=%s)",
                    self.name,
                    len(new_access.channels),
                    new_access.dm_policy,
                )
        except asyncio.CancelledError:
            raise

    async def _attachment_sweep_loop(self) -> None:
        """Periodic attachment retention sweep. Runs until cancelled by stop()."""
        try:
            while True:
                await asyncio.sleep(self.attachment_sweep_seconds)
                try:
                    self._sweep_attachments_once()
                except Exception:
                    log.exception(
                        "discord endpoint '%s': attachment sweep iteration failed",
                        self.name,
                    )
        except asyncio.CancelledError:
            raise

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        # Re-create the ready event each start so re-starts after stop() get a
        # fresh signal. asyncio.Event is bound to the running loop.
        self._ready_event = asyncio.Event()
        try:
            # 1. Load env_file (if set). dotenv is convenience, not load-bearing —
            #    if it's missing from the install, log and skip rather than
            #    failing the whole bus boot.
            if self.env_file is not None and self.env_file.exists():
                try:
                    from dotenv import load_dotenv
                except ImportError:
                    log.warning(
                        "dotenv not installed; skipping env_file %s",
                        self.env_file,
                    )
                else:
                    load_dotenv(self.env_file, override=False)
                    log.info("loaded env file: %s", self.env_file)

            # 2. Read the bot token. Fail fast if missing.
            try:
                token = get_secret(self.token_env)
            except SecretNotFoundError:
                raise RuntimeError(
                    f"discord endpoint '{self.name}': env var "
                    f"'{self.token_env}' is not set (env_file={self.env_file})"
                ) from None

            # 3. Load access policy (or use permissive defaults).
            self._access = load_access_config(self.access_config_path)
            # Record initial (mtime, size) so the poll task can detect future changes.
            # Using size alongside mtime handles filesystems with coarse mtime granularity.
            if self.access_config_path is not None and self.access_config_path.exists():
                try:
                    st = self.access_config_path.stat()
                    self._access_config_mtime = (st.st_mtime, st.st_size)
                except OSError:
                    self._access_config_mtime = None

            # 4. Create the Discord client. The factory seam lets tests inject a
            #    fake client without touching discord.py.
            if self._client_factory is None:
                import discord

                intents = discord.Intents.default()
                intents.message_content = True
                intents.reactions = True
                self._client = discord.Client(intents=intents)
            else:
                self._client = self._client_factory(intents=None)

            # 5. Wire event handlers. Use add_listener with explicit name=
            #    rather than @client.event so a future rename of the inner
            #    function can't silently mis-route the event.
            self._add_listener(self._make_on_message_handler(), "on_message")
            self._add_listener(self._make_on_reaction_add_handler(), "on_reaction_add")
            # Engagement events — wire the *raw* dispatch points so we
            # always fire, even when the underlying message has been
            # evicted from the client's message cache (the common case
            # for long-running agents). Caught on testbot 2026-05-05
            # Phase 6 verification: a vote on a bot-posted poll never
            # reached the agent because no listener was wired here.
            self._add_listener(
                self._make_on_raw_poll_vote_handler("discord.poll_vote_add"),
                "on_raw_poll_vote_add",
            )
            self._add_listener(
                self._make_on_raw_poll_vote_handler("discord.poll_vote_remove"),
                "on_raw_poll_vote_remove",
            )
            self._add_listener(
                self._make_on_raw_message_lifecycle_handler("discord.message_edit"),
                "on_raw_message_edit",
            )
            self._add_listener(
                self._make_on_raw_message_lifecycle_handler("discord.message_delete"),
                "on_raw_message_delete",
            )

            # An on_ready listener that flips the ready event so start() can
            # return once the gateway connection is live.
            ready_event = self._ready_event

            async def _ready_listener() -> None:
                ready_event.set()

            self._add_listener(_ready_listener, "on_ready")

            # 6. Register in the live endpoint map BEFORE kicking off the gateway
            #    loop so racing on_ready callbacks find us. Defense-in-depth
            #    name-collision guard — Bus.register also checks, but a stray
            #    second instance constructed in-process would otherwise silently
            #    shadow ours.
            existing = _active_endpoints.get(self.name)
            if existing is not None and existing is not self:
                raise RuntimeError(
                    f"discord endpoint '{self.name}': another live instance is "
                    f"already registered ({existing!r})"
                )
            _active_endpoints[self.name] = self

            # 7. Two-phase connect: login() returns once authenticated, then
            #    connect() runs the gateway loop until close. We park connect()
            #    in a background task so start() returns once on_ready fires.
            #    discord.Client.start(token) is the convenience equivalent of
            #    login() + connect() — and it never returns under normal
            #    operation, which would deadlock the bus boot loop.
            await self._client.login(token)
            self._client_task = asyncio.create_task(
                self._client.connect(),
                name=f"discord-endpoint-{self.name}-gateway",
            )

            # Race the ready event against the gateway task. Whichever
            # completes first wins. This avoids a 30s hang when connect()
            # raises immediately (bad token, network blip, gateway 401) —
            # the task completes with the exception and we surface the
            # real cause instead of a generic timeout.
            ready_wait = asyncio.create_task(self._ready_event.wait(), name="discord-ready-wait")
            done, _pending = await asyncio.wait(
                {ready_wait, self._client_task},
                timeout=30.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                # Timeout: cancel the ready waiter (the client task is
                # cleaned up by rollback below).
                ready_wait.cancel()
                raise RuntimeError(
                    f"discord endpoint '{self.name}': bot did not become ready within 30s"
                )
            if self._client_task in done:
                # connect() exited before on_ready fired — surface the real cause.
                ready_wait.cancel()
                exc = self._client_task.exception()
                if exc is not None:
                    raise RuntimeError(
                        f"discord endpoint '{self.name}': gateway connect failed before ready"
                    ) from exc
                raise RuntimeError(
                    f"discord endpoint '{self.name}': gateway connect returned before ready"
                )
            # ready_wait completed first — happy path. Kick off the
            # _pending_acks sweeper now that the loop is live.
            self._sweep_task = asyncio.create_task(
                self._pending_acks_sweep_loop(),
                name=f"discord-endpoint-{self.name}-acks-sweep",
            )
            self._attachment_sweep_task = asyncio.create_task(
                self._attachment_sweep_loop(),
                name=f"discord-endpoint-{self.name}-attach-sweep",
            )
            if self.access_config_path is not None and self.access_config_reload_interval > 0:
                self._access_reload_task = asyncio.create_task(
                    self._access_config_reload_loop(),
                    name=f"discord-endpoint-{self.name}-access-reload",
                )
        except BaseException:
            # Only pop if WE own this slot — never evict a sibling that may
            # have raced in.
            if _active_endpoints.get(self.name) is self:
                _active_endpoints.pop(self.name, None)
            # Cancel ALL background sweep tasks before awaiting any of them.
            # Awaiting one task gives the event loop a chance to start the
            # others; if asyncio.sleep is monkeypatched to a non-yielding stub,
            # any not-yet-cancelled task that starts running will spin forever.
            # Cancelling both first ensures each one receives CancelledError on
            # its very first step, regardless of scheduling order.
            if self._sweep_task is not None:
                self._sweep_task.cancel()
            if self._attachment_sweep_task is not None:
                self._attachment_sweep_task.cancel()
            if self._access_reload_task is not None:
                self._access_reload_task.cancel()
            if self._sweep_task is not None:
                try:
                    await self._sweep_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': sweep task raised during start rollback",
                        self.name,
                    )
                self._sweep_task = None
            if self._attachment_sweep_task is not None:
                try:
                    await self._attachment_sweep_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': attachment sweep raised during start rollback",
                        self.name,
                    )
                self._attachment_sweep_task = None
            if self._access_reload_task is not None:
                try:
                    await self._access_reload_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': access reload task raised during start rollback",
                        self.name,
                    )
                self._access_reload_task = None
            if self._client_task is not None:
                self._client_task.cancel()
                try:
                    await self._client_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': gateway task raised during start rollback",
                        self.name,
                    )
                self._client_task = None
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:
                    log.exception(
                        "discord endpoint '%s': client.close() raised during start rollback",
                        self.name,
                    )
            self._client = None
            self._handle = None
            raise

        log.info(
            "DiscordEndpoint(name=%s) started; target=%s, attachments=%s",
            self.name,
            self.target,
            self.attachments_dir,
        )

    async def stop(self) -> None:
        if _active_endpoints.get(self.name) is self:
            _active_endpoints.pop(self.name, None)
        for t in list(self._typing_tasks):
            t.cancel()
        self._typing_tasks.clear()
        # Drop typing / threading state so background typing tasks exit promptly.
        self._awaiting_reply_ids.clear()
        self._awaiting_reply_ids_timestamps.clear()
        self._inbound_envelope_discord.clear()
        # Cancel ALL background sweep tasks before awaiting any of them.
        # Awaiting one task gives the event loop a chance to start the others;
        # if asyncio.sleep is monkeypatched to a non-yielding stub (as in the
        # retry tests), any not-yet-cancelled task that starts running will spin
        # forever.  Cancelling both first ensures each one receives CancelledError
        # on its very first step, regardless of scheduling order.
        if self._sweep_task is not None:
            self._sweep_task.cancel()
        if self._attachment_sweep_task is not None:
            self._attachment_sweep_task.cancel()
        if self._access_reload_task is not None:
            self._access_reload_task.cancel()
        if self._sweep_task is not None:
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': sweep task raised during stop",
                    self.name,
                )
            self._sweep_task = None
        if self._attachment_sweep_task is not None:
            try:
                await self._attachment_sweep_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': attachment sweep raised during stop",
                    self.name,
                )
            self._attachment_sweep_task = None
        if self._access_reload_task is not None:
            try:
                await self._access_reload_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': access reload task raised during stop",
                    self.name,
                )
            self._access_reload_task = None
        if self._client_task is not None:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': gateway task raised during stop",
                    self.name,
                )
            self._client_task = None
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                log.exception("DiscordEndpoint(%s) error during client.close()", self.name)
            finally:
                self._client = None
        self._handle = None
        log.info("DiscordEndpoint(name=%s) stopped", self.name)
