from dataclasses import replace

import pytest

from skatai.game.bidding import BID_VALUES, replay
from skatai.selfplay.bidding import run_bidding
from skatai.selfplay.cardplay import make_deal


class MaxBidPolicy:
    def __init__(self, max_bid, own_hand):
        self.max_bid = max_bid
        self.own_hand = own_hand

    def probability_continue(self, observation):
        assert observation.hand == self.own_hand
        assert "skat" not in vars(observation)
        return 1.0 if BID_VALUES[observation.bid_index] <= self.max_bid else 0.0


@pytest.mark.parametrize("seed", [0, 1, 42, 20260925])
@pytest.mark.parametrize("max_bids", [(0, 0, 0), (0, 18, 20), (24, 35, 18)])
def test_auction_replays_under_existing_v2_bidding_rules(seed, max_bids):
    deal = make_deal(seed)
    policies = [MaxBidPolicy(max_bids[seat], deal.hands[seat]) for seat in range(3)]
    episode = run_bidding(deal, policies=policies)
    tokens = [token for actor, action in episode.actions for token in (str(actor), action)]
    replayed = replay(tokens)
    assert replayed.ok
    assert (episode.winner, episode.winning_bid, episode.all_pass) == (
        replayed.winner, replayed.winning_bid, replayed.all_pass
    )
    assert run_bidding(deal, policies=policies) == episode


def test_all_pass_and_bad_identity_or_probability_fail_closed():
    deal = make_deal(1)
    policies = [MaxBidPolicy(0, deal.hands[seat]) for seat in range(3)]
    episode = run_bidding(deal, policies=policies)
    assert episode.all_pass and episode.actions == ((1, "p"), (2, "p"), (0, "p"))
    with pytest.raises(ValueError, match="DEAL_IDENTITY_MISMATCH"):
        run_bidding(replace(deal, identity_sha256="bad"), policies=policies)

    class BadPolicy:
        def probability_continue(self, observation):
            return 1.5

    with pytest.raises(ValueError, match="PROBABILITY_OUT_OF_RANGE"):
        run_bidding(deal, policies=[BadPolicy()] * 3)
