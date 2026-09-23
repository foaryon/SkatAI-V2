import socket
import threading

import pytest

from skatai.iss.protocol import (
    ActionKind,
    ISSLineConnection,
    ProtocolError,
    parse_deal,
    parse_move,
    parse_transcript,
)


def test_public_move_syntax_examples():
    deal = (
        "??.??.??.??.??.??.??.??.??.??|"
        "??.??.??.??.??.??.??.??.??.??|"
        "HJ.C9.SK.S8.S7.HT.DA.DK.D9.D7|??.??"
    )
    moves = parse_transcript(
        [
            f"w {deal}",
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


def test_deal_rejects_duplicate_known_cards():
    with pytest.raises(ProtocolError, match="DUPLICATE"):
        parse_deal(
            "C7.C7.??.??.??.??.??.??.??.??|"
            "??.??.??.??.??.??.??.??.??.??|"
            "??.??.??.??.??.??.??.??.??.??|??.??"
        )


def test_invalid_bid_is_rejected():
    with pytest.raises(ProtocolError, match="INVALID_BID"):
        parse_move("1 19")


def test_line_transport_login_without_persisting_password():
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

    original = socket.create_connection
    socket.create_connection = lambda *a, **k: client
    try:
        conn = ISSLineConnection.connect(
            "unused",
            80,
            "SkatAIV2",
            "secret-test-only",
            timeout_s=2,
        )
    finally:
        socket.create_connection = original

    assert conn.client_id == "SkatAIV2"
    assert "secret-test-only" not in repr(conn.__dict__)
    conn.send_line("time")
    assert conn.read_line() == "clock 123"
    conn.close()
    t.join(timeout=2)
    assert not t.is_alive()
