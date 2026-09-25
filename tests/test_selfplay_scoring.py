import pytest

from skatai.selfplay.cardplay import DECK
from skatai.selfplay.scoring import score_basic_game


def score(contract, *, bid=18, points=61, tricks=5, cards=DECK[:12]):
    return score_basic_game(
        contract=contract, winning_bid=bid, declarer_cards=cards,
        declarer_points=points, declarer_tricks=tricks,
    )


def test_grand_base_hand_schneider_and_overbid():
    assert score("G").matadors == 1
    assert score("G").signed_game_value == 48
    assert score("GH").signed_game_value == 72
    assert score("GH", points=90).signed_game_value == 96
    overbid = score("G", bid=60)
    assert overbid.natural_value == 48 and overbid.overbid
    assert not overbid.won and overbid.signed_game_value == -144
    assert score("GH", points=30).signed_game_value == -192


def test_hearts_token_is_distinct_from_hand_modifier():
    assert score("H").contract == "H"
    assert score("HH").game_level == score("H").game_level + 1


def test_without_top_jack_uses_signed_matador_provenance():
    without_club_jack = tuple(card for card in DECK if card != "CJ")[:12]
    result = score("G", cards=without_club_jack)
    assert result.matadors == -1
    assert result.game_level == 2


@pytest.mark.parametrize(
    "contract,bid,value", [("N", 23, 23), ("NH", 35, 35),
                           ("NO", 46, 46), ("NHO", 59, 59)],
)
def test_fixed_null_values(contract, bid, value):
    assert score(contract, bid=bid, tricks=0).signed_game_value == value
    assert score(contract, bid=bid, tricks=1).signed_game_value == -2 * value


def test_unsupported_or_invalid_inputs_fail_closed():
    with pytest.raises(ValueError, match="UNSUPPORTED_BASIC_CONTRACT"):
        score("GHS")
    with pytest.raises(ValueError, match="NULL_OVERBID"):
        score("N", bid=24, tricks=0)
    with pytest.raises(ValueError, match="INVALID_DECLARER_TWELVE_CARDS"):
        score("G", cards=DECK[:11])
