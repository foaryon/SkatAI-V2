import socket
import threading

import pytest

from skatai.iss.protocol import (
    ActionKind,
    ISSLineConnection,
    ISSProtocolError,
    ProtocolError,
    format_move,
    parse_deal,
    parse_game_declaration,
    parse_move,
    parse_move_line,
    parse_transcript,
)


SAMPLE_DEAL = (
    "w "
    "??.??.??.??.??.??.??.??.??.??|"
    "??.??.??.??.??.??.??.??.??.??|"
    "HJ.C9.SK.S8.S7.HT.DA.DK.D9.D7|??.??"
)


def test_official_sample_deal_view_parses():
    m = parse_move_line(SAMPLE_DEAL)
    assert m.actor == "w"
    assert m.kind == "initial_deal"
    assert len(m.payload.hands) == 3
    assert m.payload.hands[2] == (
        "HJ", "C9", "SK", "S8", "S7", "HT", "DA", "DK", "D9", "D7"
    )
    assert m.payload.skat == ("??", "??")


@pytest.mark.parametrize(
    ("line", "kind", "payload"),
    [
        ("1 18", "bid", 18),
        ("0 y", "answer_yes", "y"),
        ("1 p", "pass", "p"),
        ("2 s", "skat_request", "s"),
        ("w H9.H8", "skat_delivery", ("H9", "H8")),
        ("0 S9", "cardplay", "S9"),
    ],
)
def test_official_move_forms(line, kind, payload):
    m = parse_move_line(line)
    assert m.kind == kind
    assert m.payload == payload


@pytest.mark.parametrize("action", ["GO", "NOH", "HH", "CHS", "GHZ", "N", "C"])
def test_official_game_type_examples(action):
    x = parse_game_declaration(action)
    assert x["game_type"] == action
    assert x["cards"] == ()


def test_discard_and_declaration_form():
    m = parse_move_line("2 N.C9.SK")
    assert m.kind == "declaration"
    assert m.payload["game_type"] == "N"
    assert m.payload["cards"] == ("C9", "SK")


def test_world_only_actions_are_enforced():
    # Two-card player moves are legal discard half-moves. Single-card play
    # and bidding remain player-only, never world-originated.
    assert parse_move_line("0 H9.H8").kind == "discard_only"
    with pytest.raises(ISSProtocolError):
        parse_move_line("w S9")
    with pytest.raises(ISSProtocolError):
        parse_move_line("w 18")


def test_invalid_card_and_non_ladder_bid_rejected():
    with pytest.raises(ISSProtocolError):
        parse_move_line("0 C6")
    with pytest.raises(ISSProtocolError, match="BAD_BID"):
        parse_move_line("1 19")


def test_deal_rejects_duplicate_known_cards():
    with pytest.raises(ProtocolError, match="DUPLICATE"):
        parse_deal(
            "C7.C7.??.??.??.??.??.??.??.??|"
            "??.??.??.??.??.??.??.??.??.??|"
            "??.??.??.??.??.??.??.??.??.??|??.??"
        )


def test_modern_typed_transcript_api_maps_compatibility_api():
    moves = parse_transcript(
        [
            SAMPLE_DEAL,
            "1 18",
            "0 y",
            "1 p",
            "2 s",
            "w H9.H8",
            "2 NO.C9.SK",
            "0 S9",
        ]
    )
    assert [m.kind for m in moves] == [
        ActionKind.DEAL,
        ActionKind.BID,
        ActionKind.ANSWER_YES,
        ActionKind.PASS,
        ActionKind.PICKUP_SKAT,
        ActionKind.WORLD_SKAT,
        ActionKind.DECLARATION,
        ActionKind.CARDPLAY,
    ]
    assert moves[1].seat == 1
    assert moves[0].seat is None
    assert format_move("1", "18") == "1 18"


def test_line_transport_login_without_persisting_password(monkeypatch):
    server, client = socket.socketpair()

    def fake_server():
        r = server.makefile("r", encoding="utf-8", newline="\n")
        w = server.makefile("w", encoding="utf-8", newline="\n")
        assert r.readline().strip() == "SkatAIV2"
        w.write("password:\n")
        w.flush()
        assert r.readline().strip() == "secret-test-only"
        w.write("Welcome SkatAIV2 version test\n")
        w.flush()
        assert r.readline().strip() == "time"
        w.write("clock 123\n")
        w.flush()
        r.close()
        w.close()
        server.close()

    t = threading.Thread(target=fake_server)
    t.start()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: client)

    conn = ISSLineConnection.connect(
        "unused",
        80,
        "SkatAIV2",
        "secret-test-only",
        timeout_s=2,
    )
    assert conn.client_id == "SkatAIV2"
    assert "secret-test-only" not in repr(conn.__dict__)
    conn.send_line("time")
    assert conn.read_line() == "clock 123"
    conn.close()
    t.join(timeout=2)
    assert not t.is_alive()


@pytest.mark.parametrize(
    "action",
    ["GHO", "GHS", "GHZ", "CHO", "CHS", "CHZ", "NO", "NH", "NHO"],
)
def test_official_declaration_modifier_vocabulary(action):
    assert parse_game_declaration(action)["game_type"] == action


@pytest.mark.parametrize("action", ["GSS", "NHS", "NZ", "GS"])
def test_invalid_declaration_modifier_semantics_fail_closed(action):
    with pytest.raises(ISSProtocolError):
        parse_game_declaration(action)


def test_hidden_skat_delivery_for_defender_view_parses():
    m = parse_move_line("w ??.??")
    assert m.kind == "skat_delivery"
    assert m.payload == ("??", "??")


def test_hidden_discards_for_defender_view_parse():
    m = parse_move_line("2 G.??.??")
    assert m.kind == "declaration"
    assert m.payload["game_type"] == "G"
    assert m.payload["cards"] == ("??", "??")


def test_ouvert_defender_view_can_include_hidden_discards_and_open_hand():
    hand = "C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8"
    m = parse_move_line("2 GHO.??.??." + hand)
    assert m.kind == "declaration"
    assert m.payload["game_type"] == "GHO"
    assert m.payload["cards"][:2] == ("??", "??")
    assert len(m.payload["cards"]) == 12


def test_official_cardplay_control_moves_are_parsed():
    from skatai.iss.protocol import ActionKind, parse_move, parse_move_line

    assert parse_move("1 RE").kind == ActionKind.RESIGN
    shown = parse_move_line("2 SC.CA.SJ")
    assert shown.kind == "show_cards"
    assert shown.payload == ("CA", "SJ")


def test_official_world_terminal_moves_are_parsed():
    from skatai.iss.protocol import ActionKind, parse_move

    ti = parse_move("w TI.2")
    le = parse_move("w LE.0")
    assert ti.kind == ActionKind.TIMEOUT
    assert le.kind == ActionKind.LEAVE
    assert ti.seat is None
    assert le.seat is None


def test_player_two_card_half_move_is_discard_not_world_skat():
    m = parse_move_line("2 C7.D8")
    assert m.kind == "discard_only"
    assert m.payload == ("C7", "D8")


def test_hidden_half_move_defender_view_does_not_expose_discard():
    m = parse_move_line("2 C7.??.??")
    assert m.kind == "discard_only"
    assert m.payload == ("??", "??")


def test_split_ouvert_discard_keeps_open_hand_cards():
    hand = ["C7","C8","C9","CT","CJ","CQ","CK","CA","S7","S8"]
    m = parse_move_line("2 H7.D8." + ".".join(hand))
    assert m.kind == "discard_only"
    assert m.payload[:2] == ("H7", "D8")
    assert m.payload[2:] == tuple(hand)


def test_weird_official_hidden_split_discard_is_normalized_private():
    hand = ["C7","C8","C9","CT","CJ","CQ","CK","CA","S7","S8"]
    m = parse_move_line("2 H7.??.??." + ".".join(hand))
    assert m.kind == "discard_only"
    assert m.payload[:2] == ("??", "??")
    assert m.payload[2:] == tuple(hand)
