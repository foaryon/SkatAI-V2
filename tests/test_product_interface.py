import pytest

from skatai.runtime.interface import (
    BiddingObservation,
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
    SkatAI,
    SkatAIInterfaceError,
)


HAND10 = ("C7","C8","C9","CT","CJ","CQ","CK","CA","S7","S8")
HAND12 = HAND10 + ("S9","ST")


class Bidder:
    def __init__(self, p=0.75):
        self.p = p
    def probability_continue(self, observation):
        return self.p


class Declarer:
    def __init__(self, choice="G"):
        self.choice = choice
    def choose_contract(self, observation):
        return self.choice


class Discarder:
    def choose_discard(self, observation):
        return observation.hand12[-2], observation.hand12[-1]


class Player:
    def play_card(self, observation):
        return observation.legal_cards[0]


def _ai(**kw):
    return SkatAI(
        bidding=kw.get("bidding", Bidder()),
        declaration=kw.get("declaration", Declarer()),
        discard=kw.get("discard", Discarder()),
        cardplay=kw.get("cardplay", Player()),
    )


def test_integrated_interface_happy_path():
    b = BiddingObservation.create(
        HAND10,
        actor=1,
        bidder=1,
        answerer=0,
        bid_index=0,
        decision_role="BIDDER",
    )
    assert _ai().decide_bid(b) == "CONTINUE"

    d = DeclarationObservation.create(
        HAND10,
        seat=1,
        winning_bid=18,
        picked_up_skat=False,
        legal_contracts=("G","C","N"),
    )
    assert _ai().choose_contract(d) == "G"

    discard = DiscardObservation.create(HAND12, seat=1, winning_bid=18)
    assert _ai().choose_discard(discard) == ("S9","ST")

    c = CardplayObservation.create(
        HAND10,
        seat=1,
        declarer=1,
        contract="G",
        winning_bid=18,
        current_trick=((0,"H7"),),
        played_cards=((0,"H7"),),
        legal_cards=("C7","C8"),
    )
    assert _ai().play_card(c) == "C7"


def test_product_boundary_rejects_illegal_component_outputs():
    d = DeclarationObservation.create(
        HAND10,
        seat=1,
        winning_bid=18,
        picked_up_skat=False,
        legal_contracts=("C","N"),
    )
    with pytest.raises(SkatAIInterfaceError, match="ILLEGAL_CONTRACT"):
        _ai(declaration=Declarer("G")).choose_contract(d)

    class BadDiscard:
        def choose_discard(self, observation):
            return "D7", "D8"

    with pytest.raises(SkatAIInterfaceError, match="DISCARD_NOT_OWNED"):
        _ai(discard=BadDiscard()).choose_discard(
            DiscardObservation.create(HAND12, seat=1, winning_bid=18)
        )

    class BadPlay:
        def play_card(self, observation):
            return "DA"

    obs = CardplayObservation.create(
        HAND10,
        seat=1,
        declarer=0,
        contract="C",
        winning_bid=18,
        current_trick=(),
        played_cards=(),
        legal_cards=("C7","C8"),
    )
    with pytest.raises(SkatAIInterfaceError, match="ILLEGAL_CARDPLAY"):
        _ai(cardplay=BadPlay()).play_card(obs)


def test_bidding_observation_contains_only_own_hand_and_public_duel_state():
    obs = BiddingObservation.create(
        HAND10,
        actor=2,
        bidder=2,
        answerer=1,
        bid_index=4,
        decision_role="ANSWERER",
    )
    fields = set(obs.__dataclass_fields__)
    assert fields == {
        "hand", "actor", "bidder", "answerer", "bid_index", "decision_role"
    }
    assert "opponent_hands" not in fields
    assert "skat" not in fields


def test_cardplay_legal_cards_must_be_owned():
    with pytest.raises(SkatAIInterfaceError, match="LEGAL_CARD_NOT_IN_HAND"):
        CardplayObservation.create(
            HAND10,
            seat=0,
            declarer=1,
            contract="G",
            winning_bid=18,
            current_trick=(),
            played_cards=(),
            legal_cards=("D7",),
        )


def test_open_hand_cards_may_shrink_during_cardplay():
    from skatai.runtime.interface import CardplayObservation

    obs = CardplayObservation.create(
        ["C7"],
        seat=1,
        declarer=0,
        contract="NHO",
        winning_bid=23,
        current_trick=(),
        played_cards=((0, "CA"),),
        legal_cards=["C7"],
        points_self=11,
        points_other=0,
        max_accepted_bids_by_seat=[23,18,0],
        blind_hand=True,
        open_hand_cards=["C8","C9","CT","CJ","CQ","CK","S7","S8","S9"],
    )
    assert len(obs.open_hand_cards) == 9


def test_product_boundary_rejects_impossible_ouvert_public_hand():
    common = dict(
        seat=0, declarer=0, contract="NHO", winning_bid=23,
        current_trick=(), played_cards=(), legal_cards=(HAND10[0],),
    )
    with pytest.raises(SkatAIInterfaceError, match="OUVERT_PUBLIC_HAND_INCOMPLETE_OR_STALE"):
        CardplayObservation.create(HAND10, **common)
    with pytest.raises(SkatAIInterfaceError, match="OUVERT_OWN_HAND_MISMATCH"):
        CardplayObservation.create(
            HAND10, **common, open_hand_cards=HAND10[1:] + ("S9",)
        )
    with pytest.raises(SkatAIInterfaceError, match="OUVERT_PUBLIC_HAND_INCOMPLETE_OR_STALE"):
        CardplayObservation.create(
            HAND10[1:], **dict(common, legal_cards=(HAND10[1],),
                               played_cards=((0, HAND10[0]),)),
            open_hand_cards=HAND10,
        )
    with pytest.raises(SkatAIInterfaceError, match="OUVERT_DEFENDER_OWNS_PUBLIC_CARD"):
        CardplayObservation.create(
            (HAND10[0],), **dict(common, seat=1), open_hand_cards=HAND10,
        )
    with pytest.raises(SkatAIInterfaceError, match="NONOUVERT_PUBLIC_HAND"):
        CardplayObservation.create(
            HAND10, **dict(common, contract="G"), open_hand_cards=HAND10,
        )
