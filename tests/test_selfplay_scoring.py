import pytest

from skatai.selfplay.cardplay import DECK
from skatai.selfplay.scoring import score_basic_game


def score(contract, *, bid=18, points=61, tricks=5, cards=DECK[:12],
          research=False, research_schwarz_ouvert=False):
    return score_basic_game(
        contract=contract, winning_bid=bid, declarer_cards=cards,
        declarer_points=points, declarer_tricks=tricks,
        research_announced_schneider=research,
        research_announced_schwarz_ouvert=research_schwarz_ouvert,
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


def test_hand_schneider_announcement_requires_ninety_points():
    with pytest.raises(ValueError, match="ANNOUNCED_SCHNEIDER_REQUIRES_RESEARCH_GATE"):
        score("GHS", points=95, tricks=8)
    won = score("GHS", points=95, tricks=8, research=True)
    assert won.won and won.game_level == 5 and won.signed_game_value == 120
    failed = score("GHS", points=89, tricks=9, research=True)
    assert not failed.won and failed.signed_game_value == -240
    schwarz = score("GHS", points=120, tricks=10, research=True)
    assert schwarz.game_level == 6 and schwarz.signed_game_value == 144


def test_schwarz_and_ouvert_research_values_follow_official_order():
    # ISkO 2022 §§2.4.1, 2.5.1, 2.5.7–2.5.8, 2.5.11 and 3.6.3.
    # The order gives Grand ouvert with four matadors as 11 × 24 = 264.
    jacks = ("CJ", "SJ", "HJ", "DJ")
    four = jacks + tuple(c for c in DECK if c not in jacks)[:8]
    won = score("GHO", points=120, tricks=10, cards=four,
                research_schwarz_ouvert=True)
    assert (won.matadors, won.game_level, won.signed_game_value) == (4, 11, 264)
    # The order also gives Club ouvert with two matadors as 9 × 12 = 108.
    two = ("CJ", "SJ") + tuple(c for c in DECK if c not in jacks)[:10]
    assert score("CHO", points=120, tricks=10, cards=two,
                 research_schwarz_ouvert=True).signed_game_value == 108

    failed = score("GHZ", points=63, tricks=9, research_schwarz_ouvert=True)
    assert (failed.game_level, failed.natural_value, failed.won,
            failed.signed_game_value) == (7, 168, False, -336)
    open_failed = score("GHO", points=63, tricks=9, research_schwarz_ouvert=True)
    assert (open_failed.game_level, open_failed.natural_value,
            open_failed.signed_game_value) == (8, 192, -384)
    overbid = score("GHO", bid=200, points=120, tricks=10,
                    research_schwarz_ouvert=True)
    assert (overbid.overbid, overbid.won, overbid.signed_game_value) == (
        True, False, -432,
    )


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
        score("GQ")
    with pytest.raises(ValueError, match="ANNOUNCED_SCHWARZ_OUVERT_REQUIRES_RESEARCH_GATE"):
        score("GHZ")
    with pytest.raises(ValueError, match="NULL_OVERBID"):
        score("N", bid=24, tricks=0)
    with pytest.raises(ValueError, match="INVALID_DECLARER_TWELVE_CARDS"):
        score("G", cards=DECK[:11])
