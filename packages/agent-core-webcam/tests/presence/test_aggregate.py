"""Pure faces->state mapping — no camera, no model, no I/O."""

from __future__ import annotations

from agent_core_webcam.presence.aggregate import aggregate, bbox_area

_BIG = (100, 100, 300, 400)  # area 200*300 = 60000
_SMALL = (10, 10, 40, 50)  # area 30*40 = 1200


def test_bbox_area() -> None:
    assert bbox_area(_BIG) == 200 * 300
    assert bbox_area(_SMALL) == 30 * 40


def test_no_faces_is_empty_scene() -> None:
    s = aggregate([], principal="jeff", source="desk-cam", now=1000.0)
    assert s.at_desk is False and s.known == [] and s.unknown_count == 0
    assert s.updated_at == 1000.0 and s.source == "desk-cam"


def test_jeff_alone_at_desk() -> None:
    s = aggregate([("jeff", _BIG)], principal="jeff", source="desk-cam", now=1.0)
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 0


def test_jeff_at_desk_plus_stranger_behind() -> None:
    s = aggregate(
        [("jeff", _BIG), ("unknown", _SMALL)], principal="jeff", source="d", now=1.0
    )
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 1


def test_stranger_at_desk_jeff_away() -> None:
    s = aggregate([("unknown", _BIG)], principal="jeff", source="d", now=1.0)
    assert s.at_desk is False and s.known == [] and s.unknown_count == 1


def test_stranger_at_desk_jeff_small_in_background() -> None:
    # Largest face is the stranger -> not at desk, but Jeff IS seen -> known.
    s = aggregate(
        [("unknown", _BIG), ("jeff", _SMALL)], principal="jeff", source="d", now=1.0
    )
    assert s.at_desk is False  # Jeff isn't the one driving the desk
    assert s.known == ["jeff"]  # but he's present
    assert s.unknown_count == 1


def test_two_strangers() -> None:
    s = aggregate(
        [("unknown", _BIG), ("unknown", _SMALL)], principal="jeff", source="d", now=1.0
    )
    assert s.at_desk is False and s.known == [] and s.unknown_count == 2
