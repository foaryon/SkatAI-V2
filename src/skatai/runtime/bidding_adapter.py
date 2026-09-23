from __future__ import annotations

from pathlib import Path

from skatai.gameplay.bidding import NeuralBiddingPolicy
from skatai.runtime.interface import BiddingObservation


class LearnedBiddingAdapter:
    """Product-interface adapter for a registered learned bidding artifact."""

    def __init__(self, policy: NeuralBiddingPolicy) -> None:
        self.policy = policy

    @classmethod
    def load(cls, path: Path, *, device: str = "cpu") -> "LearnedBiddingAdapter":
        return cls(NeuralBiddingPolicy.load(path, device=device))

    def probability_continue(self, observation: BiddingObservation) -> float:
        return self.policy.probability_continue(
            observation.hand,
            observation.actor,
            observation.bidder,
            observation.answerer,
            observation.bid_index,
            observation.decision_role,
        )
