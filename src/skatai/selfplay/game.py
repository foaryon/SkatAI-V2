"""Bounded auction-to-cardplay self-play with explicit scoring limits.

The caller provides legal contracts. This episode reports card points and
trick outcome; it does not adjudicate game value or overbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from skatai.selfplay.bidding import BiddingEpisode, BiddingPolicy, run_bidding
from skatai.selfplay.cardplay import CardplayEpisode, CardplayPolicy, make_deal, run_cardplay
from skatai.selfplay.declaration import (
    DeclarationEpisode, DeclarationPolicy, DiscardPolicy, run_declaration,
)

SCHEMA = "skatai.v2.selfplay.game.v1"


@dataclass(frozen=True)
class GameEpisode:
    schema: str
    deal_seed: int
    deal_sha256: str
    bidding: BiddingEpisode
    declaration: DeclarationEpisode | None
    cardplay: CardplayEpisode | None


def run_game(
    seed: int,
    *,
    bidding_policies: Sequence[BiddingPolicy],
    declaration_policies: Sequence[DeclarationPolicy],
    discard_policies: Sequence[DiscardPolicy],
    cardplay_policies: Sequence[CardplayPolicy],
    legal_contracts: Sequence[str],
    threshold: float = 0.5,
) -> GameEpisode:
    if len(declaration_policies) != 3 or len(discard_policies) != 3:
        raise ValueError("THREE_DECLARATION_AND_DISCARD_POLICIES_REQUIRED")
    deal = make_deal(seed)
    bidding = run_bidding(deal, policies=bidding_policies, threshold=threshold)
    if bidding.all_pass:
        return GameEpisode(SCHEMA, seed, deal.identity_sha256, bidding, None, None)
    assert bidding.winner is not None and bidding.winning_bid is not None
    seat = bidding.winner
    declaration = run_declaration(
        deal, declarer=seat, winning_bid=bidding.winning_bid,
        max_accepted_bids_by_seat=bidding.max_accepted_bids_by_seat,
        legal_contracts=legal_contracts,
        declaration_policy=declaration_policies[seat],
        discard_policy=discard_policies[seat],
    )
    cardplay = run_cardplay(
        deal, contract=declaration.contract, declarer=seat,
        policies=cardplay_policies,
        final_hand=declaration.final_hand if declaration.picked_up_skat else None,
        final_skat=declaration.final_skat if declaration.picked_up_skat else None,
        winning_bid=bidding.winning_bid,
        max_accepted_bids_by_seat=bidding.max_accepted_bids_by_seat,
    )
    return GameEpisode(SCHEMA, seed, deal.identity_sha256, bidding, declaration, cardplay)
