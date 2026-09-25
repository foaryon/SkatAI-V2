import pytest

from skatai.evaluation.endgame_teacher import solve_endgame
from skatai.selfplay.cardplay import make_deal


@pytest.mark.parametrize("contract", ["C", "S", "H", "D", "G"])
def test_declarer_leads_ace_to_secure_points(contract):
    result = solve_endgame(
        (("CA", "C7"), ("C8", "H7"), ("C9", "H8")),
        contract=contract, declarer=0, leader=0,
    )
    assert result["card"] == ("C7" if contract == "C" else "CA")
    assert result["declarer_remaining_utility"] == 11
    assert result["action_values"][result["card"]] == 11
    if contract == "G":
        assert result["action_values"]["C7"] == 0
    assert result["offline_teacher_only"] is True


def test_null_declarer_avoids_first_trick():
    result = solve_endgame(
        (("CA", "C7"), ("C8", "H7"), ("C9", "H8")),
        contract="N", declarer=0, leader=0,
    )
    assert result["card"] == "C7"
    assert result["declarer_remaining_utility"] == 0
    assert result["utility_kind"] == "minus_declarer_tricks"
    assert result["action_values"]["CA"] == -2


def test_partial_trick_obeys_turn_and_follow_suit():
    result = solve_endgame(
        (("C7", "H9"), ("H7",), ("C9", "H8")),
        contract="G", declarer=0, leader=1,
        current_trick=((1, "C8"),),
    )
    assert result["actor"] == 2
    assert result["card"] == "C9"


@pytest.mark.parametrize("contract", ["C", "S", "H", "D", "G", "N"])
def test_three_card_bound_returns_values_for_every_legal_root_card(contract):
    hands = tuple(hand[:3] for hand in make_deal(42).hands)
    first = solve_endgame(hands, contract=contract, declarer=1, leader=0)
    assert set(first["action_values"]) == set(hands[0])
    assert first == solve_endgame(hands, contract=contract, declarer=1, leader=0)
    assert first["states_visited"] < 10000


@pytest.mark.parametrize("kwargs,error", [
    ({"hands": (("C7",), ("C8",), ("C7",))}, "INVALID_CARDS"),
    ({"hands": (("C7",), ("C8",), ())}, "CARD_BOUND"),
    ({"hands": (("C7",) * 4, ("C8",) * 4, ("C9",) * 4)}, "CARD_BOUND"),
    ({"hands": (("C7",), ("C8",), ("C9",)), "current_trick": ((2, "H7"),)}, "BAD_TRICK_ORDER"),
    ({"hands": (("C7",), ("C8",), ("C9",)), "contract": "GHO"}, "BASIC_CONTRACT_ONLY"),
])
def test_invalid_full_information_state_fails_closed(kwargs, error):
    args = {"contract": "G", "declarer": 0, "leader": 0} | kwargs
    with pytest.raises(ValueError, match=error):
        solve_endgame(**args)
