"""The 2026-08-14 defect, as tests: a dead sensor must not look like a quiet one.

Regression suite for a 56-hour silent outage. The watcher stopped for an
unknown reason after eight caught transients, and every turn for two and a half
days the injector emitted the same sentence it emits when a reading is merely
31 seconds old. Nobody noticed, because nothing could have — the two states
produced byte-identical output.

The load-bearing test here is :func:`test_dead_and_stale_are_not_byte_identical`.
Everything else supports it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agent_core_webcam.presence.injector import PresenceInjector, _diagnose
from agent_core_webcam.presence.levels import (
    DEFAULT_TEMPLATES,
    Instrument,
    PresenceReading,
    humanize_age,
    render,
)
from agent_core_webcam.presence.state import (
    PresenceState,
    WatcherHeartbeat,
    heartbeat_path_for,
    read_heartbeat,
    write_heartbeat,
    write_state,
)

NOW = 1_000_000.0
MAX_AGE = 30.0
HB_MAX_AGE = 120.0


def _state(age: float) -> PresenceState:
    return PresenceState(updated_at=NOW - age, at_desk=True, known=["jeff"], unknown_count=0)


def _beat(age: float) -> WatcherHeartbeat:
    return WatcherHeartbeat(beat_at=NOW - age, last_frame_at=NOW - age, pid=123)


# --------------------------------------------------------------------------
# _diagnose: the five instrument states
# --------------------------------------------------------------------------


def test_fresh_reading_is_fresh_even_with_no_heartbeat() -> None:
    """A good reading is usable regardless of bookkeeping.

    Refusing a fresh reading because the heartbeat file is missing would make
    this change strictly worse than what it replaced.
    """
    assert _diagnose(NOW, _state(5), None, MAX_AGE, HB_MAX_AGE)[0] is Instrument.FRESH


def test_no_state_file_is_never_not_dead() -> None:
    """Never-configured must not be reported as a running system that broke."""
    instrument, age = _diagnose(NOW, None, None, MAX_AGE, HB_MAX_AGE)
    assert instrument is Instrument.NEVER
    assert age is None


def test_stale_reading_with_live_heartbeat_is_stale() -> None:
    """Loop turning, camera failing — a different fault from a dead process."""
    instrument, age = _diagnose(NOW, _state(3600), _beat(2), MAX_AGE, HB_MAX_AGE)
    assert instrument is Instrument.STALE
    assert age == 3600


def test_stale_reading_with_stale_heartbeat_is_dead() -> None:
    """Both signals old: the watcher is gone. This is the 2026-08-14 case."""
    instrument, _ = _diagnose(NOW, _state(200_000), _beat(200_000), MAX_AGE, HB_MAX_AGE)
    assert instrument is Instrument.DEAD


def test_stale_reading_with_NO_heartbeat_is_unknown_never_dead() -> None:
    """Absent heartbeat must NOT be read as death.

    A state file written before heartbeats existed is indistinguishable from a
    dead watcher. Claiming death without evidence is the same class of error as
    the silence this whole change exists to remove — so it degrades to UNKNOWN,
    which is equally cautious and does not assert what was not measured.
    """
    instrument, _ = _diagnose(NOW, _state(200_000), None, MAX_AGE, HB_MAX_AGE)
    assert instrument is Instrument.UNKNOWN
    assert instrument is not Instrument.DEAD


# --------------------------------------------------------------------------
# The regression itself
# --------------------------------------------------------------------------


def _render_for(instrument: Instrument, age: float | None, level: int = 3) -> str:
    reading = PresenceReading(
        have_reading=False,
        principal_present=False,
        unknown_present=True,
        instrument=instrument,
        age_seconds=age,
    )
    return render(reading, None, level=level, templates=DEFAULT_TEMPLATES)


def test_dead_and_stale_are_not_byte_identical() -> None:
    """THE REGRESSION. Before 2026-08-16 these produced the same string.

    This is the test that would have caught the outage on the Friday afternoon
    it began instead of the Sunday it was noticed.
    """
    dead = _render_for(Instrument.DEAD, 200_000)
    stale = _render_for(Instrument.STALE, 200_000)
    assert dead != stale


def test_every_instrument_state_renders_distinctly() -> None:
    """No two instrument states may collapse into the same text.

    Generalises the regression: it is not enough that DEAD differs from STALE
    today, because the defect was introduced by a refactor that made two
    branches converge. Any future collapse fails here.
    """
    rendered = {
        i: _render_for(i, 200_000) for i in (Instrument.STALE, Instrument.DEAD, Instrument.UNKNOWN)
    }
    rendered[Instrument.NEVER] = _render_for(Instrument.NEVER, None)
    assert len(set(rendered.values())) == len(rendered)


def test_dead_message_names_the_age_and_says_dead() -> None:
    """'No reading' hid this for 56 hours; the age is what makes it visible."""
    out = _render_for(Instrument.DEAD, 200_000)
    assert "2d" in out
    assert "DEAD" in out


def test_stale_says_failing_not_dead() -> None:
    """A live loop with a bad camera must not be reported as a dead watcher."""
    out = _render_for(Instrument.STALE, 3600)
    assert "DEAD" not in out
    assert "FAILING" in out


def test_never_does_not_claim_a_failure() -> None:
    """Never-configured is not a broken running system and must not read as one."""
    out = _render_for(Instrument.NEVER, None)
    assert "DEAD" not in out
    assert "never" in out.lower()


def test_unknown_liveness_does_not_assert_death() -> None:
    """Undeterminable liveness stays cautious without claiming a measurement."""
    out = _render_for(Instrument.UNKNOWN, 200_000)
    assert "DEAD" not in out


# --------------------------------------------------------------------------
# Caution is UNCHANGED — the security invariant
# --------------------------------------------------------------------------


def test_no_instrument_state_unlocks_gating() -> None:
    """Only a FRESH reading may ever relax caution.

    The whole change is about what the output CLAIMS, never about how cautious
    it is. Every non-fresh instrument state must still carry the level-3 trust
    gate, exactly as the single old sentence did.
    """
    for instrument in (Instrument.STALE, Instrument.DEAD, Instrument.NEVER, Instrument.UNKNOWN):
        out = _render_for(instrument, 200_000, level=3)
        assert DEFAULT_TEMPLATES["trust_gate"] in out, instrument


def test_restart_count_is_surfaced_even_when_healthy() -> None:
    """A flapping watcher looks fine at any instant; the count is the only tell."""
    reading = PresenceReading(
        have_reading=False,
        principal_present=False,
        unknown_present=True,
        instrument=Instrument.DEAD,
        age_seconds=60,
        restarts=4,
    )
    out = render(reading, None, level=1, templates=DEFAULT_TEMPLATES)
    assert "4 time(s)" in out


# --------------------------------------------------------------------------
# humanize_age
# --------------------------------------------------------------------------


def test_humanize_age_none_is_never() -> None:
    assert humanize_age(None) == "never"


def test_humanize_age_scales() -> None:
    assert humanize_age(9) == "9s"
    assert humanize_age(600) == "10m"
    assert humanize_age(3600 * 5 + 120) == "5h 2m"
    assert humanize_age(86400 * 2 + 3600 * 8) == "2d 8h"


# --------------------------------------------------------------------------
# Heartbeat round-trip
# --------------------------------------------------------------------------


def test_heartbeat_round_trips(tmp_path: Path) -> None:
    p = heartbeat_path_for(tmp_path / "state.json")
    write_heartbeat(WatcherHeartbeat(beat_at=1.5, last_frame_at=1.0, pid=7), p)
    got = read_heartbeat(p)
    assert got is not None
    assert got.beat_at == 1.5
    assert got.pid == 7


def test_heartbeat_path_is_beside_state(tmp_path: Path) -> None:
    """Writer and reader must never disagree about where the file lives."""
    assert heartbeat_path_for(tmp_path / "state.json").parent == tmp_path


def test_missing_heartbeat_reads_as_none_not_raise(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path / "nope.json") is None


def test_corrupt_heartbeat_reads_as_none_not_raise(tmp_path: Path) -> None:
    p = tmp_path / "hb.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_heartbeat(p) is None


# --------------------------------------------------------------------------
# End-to-end through the hook
# --------------------------------------------------------------------------


def test_injector_reports_dead_end_to_end(tmp_path: Path) -> None:
    """The real 2026-08-14 shape, through the public hook."""
    sp = tmp_path / "state.json"
    write_state(PresenceState(updated_at=time.time() - 200_000, at_desk=True), sp)
    write_heartbeat(WatcherHeartbeat(beat_at=time.time() - 200_000), heartbeat_path_for(sp))
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(sp), "level": 3})
    assert "DEAD" in out.content
    assert "2d" in out.content


def test_injector_fresh_reading_still_reports_facts(tmp_path: Path) -> None:
    """The happy path is untouched by the change."""
    sp = tmp_path / "state.json"
    write_state(
        PresenceState(updated_at=time.time(), at_desk=True, known=["jeff"]),
        sp,
    )
    write_heartbeat(WatcherHeartbeat(beat_at=time.time()), heartbeat_path_for(sp))
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(sp), "level": 3})
    assert "At desk: yes" in out.content
    assert "DEAD" not in out.content


def test_injector_never_raises_on_garbage(tmp_path: Path) -> None:
    """The hook must never raise into a session, whatever it finds."""
    sp = tmp_path / "state.json"
    sp.write_text("{{{garbage", encoding="utf-8")
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(sp), "level": 3})
    assert out.content


def test_restart_count_absent_is_none_not_zero(tmp_path: Path) -> None:
    """`None` and `0` differ: 'nobody counting' is not 'counted, stable'."""
    from agent_core_webcam.presence.injector import _read_restart_count

    assert _read_restart_count(tmp_path / "state.json") is None
    (tmp_path / "supervisor.json").write_text(json.dumps({"restarts_recent": 0}), encoding="utf-8")
    assert _read_restart_count(tmp_path / "state.json") == 0


# --------------------------------------------------------------------------
# Degraded state must survive a restart (Pepper, 2026-08-16)
# --------------------------------------------------------------------------


def test_degraded_hint_round_trips(tmp_path: Path) -> None:
    """A restart must not relearn what the last run already paid to discover.

    Without persistence, supervision restarts at full resolution every time, so
    a box that reliably kills 720p produces an endless fail-degrade-die-restart
    cycle — motion that looks like recovery and never converges.
    """
    from agent_core_webcam.presence.watcher import (
        _degraded_hint_path,
        _set_degraded_hint,
    )

    sp = tmp_path / "state.json"
    assert not _degraded_hint_path(sp).exists()
    _set_degraded_hint(sp, degraded=True)
    assert _degraded_hint_path(sp).exists()
    _set_degraded_hint(sp, degraded=False)
    assert not _degraded_hint_path(sp).exists()


def test_degraded_hint_never_raises_on_bad_path() -> None:
    """Bookkeeping that could kill the loop it serves is worse than the bug."""
    from agent_core_webcam.presence.watcher import _set_degraded_hint

    _set_degraded_hint(Path("\x00:/nonexistent/state.json"), degraded=True)


def test_clearing_an_absent_hint_is_not_an_error(tmp_path: Path) -> None:
    """Restore-when-not-degraded must be a no-op, not a crash."""
    from agent_core_webcam.presence.watcher import _set_degraded_hint

    _set_degraded_hint(tmp_path / "state.json", degraded=False)
