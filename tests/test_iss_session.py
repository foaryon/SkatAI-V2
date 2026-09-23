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


def test_official_start_payload_binds_server_game_identity():
    s = ISSSessionState()
    table = s.apply(parse_service_line("create T1 skatai skat"))
    s.apply(parse_service_line("table T1 skatai start 42 skatai 60 kermit 59 zoot 58"))
    assert table.game_sequence == 42
    assert table.server_game_num == 42
    assert table.players == ("skatai", "kermit", "zoot")
    assert table.remaining_time_s == (60.0, 59.0, 58.0)


def test_synthetic_start_payload_keeps_local_sequence_fallback():
    s = ISSSessionState()
    table = s.apply(parse_service_line("create T1 skatai skat"))
    s.apply(parse_service_line("table T1 skatai start fixture"))
    assert table.game_sequence == 1
    assert table.server_game_num is None


def test_reconnect_start_replays_player_visible_sgf_without_hidden_leakage():
    from skatai.iss.session import replay_moves_from_player_sgf

    deal = (
        "C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|"
        "??.??.??.??.??.??.??.??.??.??|"
        "??.??.??.??.??.??.??.??.??.??|??.??"
    )
    sgf = (
        "(;GM[Skat]"
        "P0[SkatAI]P1[kermit]P2[zoot]"
        f"MV[w {deal} 1 18 0 p 2 20 1 p 2 GH 0 S7]"
        "R[] ;)"
    )
    moves = replay_moves_from_player_sgf(sgf)
    assert [m.kind for m in moves] == [
        "initial_deal", "bid", "pass", "bid", "pass", "declaration", "cardplay"
    ]
    assert moves[0].payload.hands[0][0] == "C7"
    assert moves[0].payload.hands[1][0] == "??"


def test_table_start_reconnect_restores_move_transcript():
    s = ISSSessionState()
    table = s.apply(parse_service_line("create T1 SkatAI 3"))
    deal = (
        "C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|"
        "??.??.??.??.??.??.??.??.??.??|"
        "??.??.??.??.??.??.??.??.??.??|??.??"
    )
    sgf = f"(;GM[Skat]MV[w {deal} 1 18 0 p 2 p]R[] ;)"
    s.apply(
        parse_service_line(
            "table T1 SkatAI start "
            "42 SkatAI 60 kermit 59 zoot 58 ! " + sgf
        )
    )
    assert table.game_sequence == 42
    assert [m.kind for m in table.moves] == [
        "initial_deal", "bid", "pass", "pass"
    ]
