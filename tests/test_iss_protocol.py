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
    with pytest.raises(ISSProtocolError):
        parse_move_line("0 H9.H8")
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
