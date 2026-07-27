"""agent-core-presence — inject a local, staleness-guarded presence tag.

Phase 1 (this package today) provides two things:

* :class:`~agent_core_presence.injector.PresenceInjector` — an agent_core
  hook that runs in-session on each lifecycle event, reads the presence-state
  file, and emits a compact presence tag (or an explicit "unknown" when the
  reading is missing or stale).
* :mod:`agent_core_presence.state` — the presence-state file *contract*
  (:class:`~agent_core_presence.state.PresenceState`) plus atomic
  write/read helpers, shared between the reader (the hook) and the writer.

The continuous computer-vision watcher that *produces* the state file is a
separate out-of-band process (Phase 2); it is only ever the writer. The JSON
state file is the interface between the two.
"""
