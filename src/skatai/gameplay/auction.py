"""Pure V2 auction simulation shared by learned bidding and self-play."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from skatai.data.bidding_features import encode_hand
from skatai.game.bidding import BiddingState

@dataclass(frozen=True)
class AuctionDecision:
    ordinal: int
    actor: int
    bidder: int
    answerer: int
    bid_index: int
    current_offer: int
    decision_role: str
    probability_continue: float
    native_action: str


@dataclass(frozen=True)
class AuctionResult:
    winner: int | None
    winning_bid: int | None
    all_pass: bool
    decisions: tuple[AuctionDecision, ...]


DecisionProbability = Callable[
    [Sequence[str], int, int, int, int, str], float
]


def simulate_auction(
    hands: Sequence[Sequence[str]],
    probability_continue: DecisionProbability,
    *,
    threshold: float = 0.5,
    max_actions: int = 256,
) -> AuctionResult:
    if len(hands) != 3:
        raise ValueError(f"EXPECTED_3_HANDS:{len(hands)}")
    for hand in hands:
        encode_hand(hand)  # validate exactly ten unique legal cards
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"BAD_THRESHOLD:{threshold}")

    state = BiddingState()
    decisions: list[AuctionDecision] = []

    for ordinal in range(max_actions):
        if state.finished:
            break
        actor = state.expected_actor
        bidder = state.bidder
        answerer = state.answerer
        bid_index = state.bid_index
        offer = state.offer
        role = state.decision_role
        p = float(
            probability_continue(
                hands[actor], actor, bidder, answerer, bid_index, role
            )
        )
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"PROBABILITY_OUT_OF_RANGE:{p}")

        if p >= threshold:
            native = str(offer) if role == "BIDDER" else "y"
        else:
            native = "p"

        state.apply(actor, native)
        decisions.append(
            AuctionDecision(
                ordinal=ordinal,
                actor=actor,
                bidder=bidder,
                answerer=answerer,
                bid_index=bid_index,
                current_offer=offer,
                decision_role=role,
                probability_continue=p,
                native_action=native,
            )
        )
    else:
        raise RuntimeError(f"AUCTION_ACTION_LIMIT:{max_actions}")

    return AuctionResult(
        winner=state.winner,
        winning_bid=state.winning_bid,
        all_pass=state.winner is None,
        decisions=tuple(decisions),
    )


def simulate_max_bid_auction(
    hands: Sequence[Sequence[str]],
    max_bids: Sequence[int],
    *,
    max_actions: int = 256,
) -> AuctionResult:
    """Run the legal V2 auction from per-seat maximum bidding values.

    This is the deployment bridge for SkatZero B0, whose frozen BID interface
    returns a maximum bid value rather than a native action probability.
    Values below 18 mean pass at the first offer.
    """
    if len(max_bids) != 3:
        raise ValueError(f"EXPECTED_3_MAX_BIDS:{len(max_bids)}")
    checked = tuple(int(x) for x in max_bids)
    if any(x < 0 for x in checked):
        raise ValueError(f"NEGATIVE_MAX_BID:{checked}")

    from skatai.game.bidding import BID_VALUES

    def probability(
        hand: Sequence[str],
        actor: int,
        bidder: int,
        answerer: int,
        bid_index: int,
        decision_role: str,
    ) -> float:
        del hand, bidder, answerer, decision_role
        return 1.0 if checked[actor] >= BID_VALUES[bid_index] else 0.0

    return simulate_auction(
        hands, probability, threshold=0.5, max_actions=max_actions
    )
