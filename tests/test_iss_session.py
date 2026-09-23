import pytest

from skatai.iss.service import parse_service_line
from skatai.iss.session import ISSSessionError, ISSSessionState


def test_table_lifecycle_and_move_transcript():
    s = ISSSessionState()
    s.set_connected("skatai")
    assert s.connected is True

    table = s.apply(parse_service_line("create T1 skatai skat"))
    assert table is not None
    assert table.is_player is True

    s.apply(parse_service_line("table T1 skatai start game-1"))
    assert table.in_progress is True

    s.apply(parse_service_line("table T1 skatai play 1 18"))
    s.apply(parse_service_line("table T1 skatai play 0 y"))
    s.apply(parse_service_line("table T1 skatai play 1 p"))
    assert [m.kind for m in table.moves] == ["bid", "answer_yes", "pass"]
    assert table.last_move.actor == "1"

    s.apply(parse_service_line("table T1 skatai end (;GM[Skat]ID[1])"))
    assert table.in_progress is False
    assert table.game_sgf == "(;GM[Skat]ID[1])"

    removed = s.apply(parse_service_line("destroy T1 skatai"))
    assert removed is table
    assert "T1" not in s.tables


def test_invalid_wire_move_fails_closed():
    s = ISSSessionState()
    s.apply(parse_service_line("create T1 skatai skat"))
    with pytest.raises(ISSSessionError):
        s.apply(parse_service_line("table T1 skatai play 0 C6"))


def test_unknown_table_event_fails_closed():
    s = ISSSessionState()
    with pytest.raises(ISSSessionError):
        s.apply(parse_service_line("table nope skatai go"))


def test_invitation_is_recorded_without_auto_join():
    s = ISSSessionState()
    s.apply(parse_service_line("invite kermit T7 pw"))
    assert s.invitations == [
        {"from_id": "kermit", "table_id": "T7", "table_password": "pw"}
    ]
    assert s.tables == {}


def test_disconnect_marks_tables_not_in_progress():
    s = ISSSessionState()
    table = s.apply(parse_service_line("create T1 skatai skat"))
    s.apply(parse_service_line("table T1 skatai start game"))
    assert table.in_progress
    s.set_disconnected()
    assert s.connected is False
    assert table.in_progress is False
