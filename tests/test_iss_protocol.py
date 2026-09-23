import pytest

from skatai.iss.protocol import (
    ISSProtocolError,
    parse_game_declaration,
    parse_move_line,
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


@pytest.mark.parametrize(
    "action",
    ["GO", "NOH", "HH", "CHS", "GHZ", "N", "C"],
)
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


def test_invalid_card_rejected():
    with pytest.raises(ISSProtocolError):
        parse_move_line("0 C6")
