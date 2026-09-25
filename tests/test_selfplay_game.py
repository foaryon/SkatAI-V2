from dataclasses import replace

import pytest

from skatai.game.bidding import BID_VALUES
from skatai.game.rules import card_points
from skatai.selfplay.cardplay import RandomLegalPolicy, make_deal, run_cardplay
from skatai.selfplay.declaration import run_declaration
from skatai.selfplay.game import run_game
from skatai.selfplay.scoring import score_basic_episode


class BidPolicy:
    def __init__(self, hand, max_bid):
        self.hand, self.max_bid = hand, max_bid

    def probability_continue(self, view):
        assert view.hand == self.hand
        return float(BID_VALUES[view.bid_index] <= self.max_bid)


class ContractPolicy:
    def __init__(self, pickup):
        self.pickup = pickup

    def choose_contract(self, view):
        if self.pickup and not view.picked_up_skat:
            assert len(view.cards) == 10 and "PICKUP" in view.legal_contracts
            return "PICKUP"
        assert len(view.cards) == (12 if self.pickup else 10)
        assert "G" in view.legal_contracts
        return "G"


class DiscardPolicy:
    def choose_discard(self, view):
        assert len(view.hand12) == 12
        return view.hand12[:2]


class InspectPlay:
    def __init__(self, seat, pickup):
        self.seat, self.pickup, self.calls = seat, pickup, 0

    def play_card(self, view):
        self.calls += 1
        assert view.seat == self.seat
        assert view.winning_bid >= 18
        assert view.open_hand_cards == ()
        if view.seat == view.declarer and self.pickup:
            assert len(view.skat_cards) == 2 and not view.blind_hand
        else:
            assert view.skat_cards == ()
        return view.legal_cards[0]


@pytest.mark.parametrize("pickup", [False, True])
@pytest.mark.parametrize("seed", [0, 1, 42])
def test_auction_declaration_and_legal_cardplay(seed, pickup):
    deal = make_deal(seed)

    def episode():
        play = [InspectPlay(seat, pickup) for seat in range(3)]
        result = run_game(
            seed,
            bidding_policies=[BidPolicy(deal.hands[seat], 18) for seat in range(3)],
            declaration_policies=[ContractPolicy(pickup) for _ in range(3)],
            discard_policies=[DiscardPolicy() for _ in range(3)],
            cardplay_policies=play,
            legal_contracts=("G",),
        )
        assert [p.calls for p in play] == [10, 10, 10]
        return result

    result = episode()
    assert result == episode()
    assert result.declaration is not None and result.cardplay is not None
    assert result.declaration.picked_up_skat == pickup
    assert len(result.cardplay.plays) == 30
    assert result.cardplay.declarer_final_points + result.cardplay.defender_trick_points == 120
    assert result.bidding.winning_bid == 18
    assert result.cardplay.skat_points == sum(card_points(c) for c in result.declaration.final_skat)
    scored = score_basic_episode(result)
    assert scored.signed_game_value != 0
    assert scored.won == (scored.signed_game_value > 0)
    with pytest.raises(ValueError, match="EPISODE_REPLAY_OR_POINT_MISMATCH"):
        score_basic_episode(replace(
            result, cardplay=replace(result.cardplay, declarer_final_points=0),
        ))
    swapped = list(result.cardplay.plays)
    swapped[0] = (swapped[0][0], result.cardplay.plays[1][1])
    swapped[1] = (swapped[1][0], result.cardplay.plays[0][1])
    with pytest.raises(ValueError, match="EPISODE_ILLEGAL_OR_UNOWNED_PLAY"):
        score_basic_episode(replace(
            result, cardplay=replace(result.cardplay, plays=tuple(swapped)),
        ))


def test_all_pass_skips_declaration_and_play():
    deal = make_deal(3)
    result = run_game(
        3, bidding_policies=[BidPolicy(hand, 0) for hand in deal.hands],
        declaration_policies=[ContractPolicy(False)] * 3,
        discard_policies=[DiscardPolicy()] * 3,
        cardplay_policies=[RandomLegalPolicy(i) for i in range(3)],
        legal_contracts=("G",),
    )
    assert result.bidding.all_pass and result.declaration is result.cardplay is None
    with pytest.raises(ValueError, match="NO_PLAYED_GAME_TO_SCORE"):
        score_basic_episode(result)


def test_invalid_discard_contract_partition_and_deal_identity_fail_closed():
    deal = make_deal(1)

    class BadDiscard:
        def choose_discard(self, view):
            return (view.hand12[0], view.hand12[0])

    with pytest.raises(ValueError, match="ILLEGAL_DISCARD"):
        run_declaration(
            deal, declarer=0, winning_bid=18, max_accepted_bids_by_seat=(18, 0, 0),
            legal_contracts=("G",), declaration_policy=ContractPolicy(True),
            discard_policy=BadDiscard(),
        )

    class BadContract:
        def choose_contract(self, view):
            return "N"

    with pytest.raises(ValueError, match="CONTRACT_NOT_IN_LEGAL_SET"):
        run_declaration(
            deal, declarer=0, winning_bid=18, max_accepted_bids_by_seat=(18, 0, 0),
            legal_contracts=("G",), declaration_policy=BadContract(),
            discard_policy=DiscardPolicy(),
        )
    with pytest.raises(ValueError, match="INVALID_POST_PICKUP_PARTITION"):
        run_cardplay(
            deal, contract="G", declarer=0,
            policies=[RandomLegalPolicy(i) for i in range(3)],
            final_hand=deal.hands[1], final_skat=deal.skat,
        )
    with pytest.raises(ValueError, match="DEAL_IDENTITY_MISMATCH"):
        run_declaration(
            replace(deal, identity_sha256="bad"), declarer=0, winning_bid=18,
            max_accepted_bids_by_seat=(18, 0, 0), legal_contracts=("G",),
            declaration_policy=ContractPolicy(False), discard_policy=DiscardPolicy(),
        )
