"""Leakage-safe, deterministic V2 auction episodes for controlled self-play."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from skatai.gameplay.bidding import simulate_auction
from skatai.gameplay.skatzero_bidding import max_accepted_bids_by_seat
from skatai.runtime.interface import BiddingObservation
from skatai.selfplay.cardplay import Deal, make_deal

SCHEMA = "skatai.v2.selfplay.bidding.v1"


class BiddingPolicy(Protocol):
    def probability_continue(self, observation: BiddingObservation) -> float: ...


@dataclass(frozen=True)
class BiddingEpisode:
    schema: str
    deal_seed: int
    deal_sha256: str
    threshold: float
    actions: tuple[tuple[int, str], ...]
    winner: int | None
    winning_bid: int | None
    all_pass: bool
    max_accepted_bids_by_seat: tuple[int, int, int]


def run_bidding(
    deal: Deal,
    *,
    policies: Sequence[BiddingPolicy],
    threshold: float = 0.5,
) -> BiddingEpisode:
    if len(policies) != 3:
        raise ValueError("THREE_POLICIES_REQUIRED")
    if deal != make_deal(deal.seed):
        raise ValueError("DEAL_IDENTITY_MISMATCH")

    def probability(hand, actor, bidder, answerer, bid_index, role):
        observation = BiddingObservation.create(
            hand,
            actor=actor,
            bidder=bidder,
            answerer=answerer,
            bid_index=bid_index,
            decision_role=role,
        )
        return policies[actor].probability_continue(observation)

    result = simulate_auction(deal.hands, probability, threshold=threshold)
    return BiddingEpisode(
        schema=SCHEMA,
        deal_seed=deal.seed,
        deal_sha256=deal.identity_sha256,
        threshold=float(threshold),
        actions=tuple((item.actor, item.native_action) for item in result.decisions),
        winner=result.winner,
        winning_bid=result.winning_bid,
        all_pass=result.all_pass,
        max_accepted_bids_by_seat=max_accepted_bids_by_seat(result),
    )
