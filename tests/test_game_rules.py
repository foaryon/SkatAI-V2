import pytest

from skatai.game.rules import (
    card_points,
    category,
    game_type_from_contract,
    legal_cards,
    replay_tricks,
    trick_winner,
)


def test_contract_mapping_handles_iss_modifiers():
    assert game_type_from_contract("GHO") == "GRAND"
    assert game_type_from_contract("CHZ") == "CLUBS"
    assert game_type_from_contract("NHO") == "NULL"
    assert game_type_from_contract("NO") == "NULL"


@pytest.mark.parametrize("contract", ["GARBAGE", "NONSENSE", "GSS", "NS", "GZ", "X", ""])
def test_contract_mapping_rejects_malformed_game_type(contract):
    with pytest.raises(ValueError, match="UNSUPPORTED_CONTRACT"):
        game_type_from_contract(contract)


def test_suit_game_jacks_and_trump_follow_rule():
    hand = ("CJ", "C7", "H7")
    assert category("CJ", "CLUBS") == "TRUMP"
    assert category("C7", "CLUBS") == "TRUMP"
    assert legal_cards(hand, ((0, "H8"),), "CLUBS") == ("H7",)
    assert legal_cards(hand, ((0, "SJ"),), "CLUBS") == ("CJ", "C7")


def test_null_rank_order_and_follow_rule():
    assert trick_winner(((0, "H7"), (1, "HJ"), (2, "HA")), "NULL") == 2
    assert legal_cards(("H8", "C7"), ((0, "H7"),), "NULL") == ("H8",)


def test_replay_tracks_turns_winners_and_points():
    r = replay_tricks(
        [
            (0, "CA"), (1, "C7"), (2, "CT"),
            (0, "H7"), (1, "HA"), (2, "H8"),
        ],
        game_type="NULL",
        declarer=0,
    )
    assert r["completed_tricks"][0]["winner"] == 0
    assert r["completed_tricks"][1]["winner"] == 1
    assert r["declarer_trick_points"] == 21
    assert r["defender_trick_points"] == 11
    assert r["expected_actor"] == 1


def test_card_points_standard_skat_values():
    assert [card_points(x) for x in ("CA", "CT", "CK", "CQ", "CJ", "C9")] == [11,10,4,3,2,0]
