from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import torch

from skatai.data.bidding_features import encode_hand
from skatai.game.bidding import BiddingState
from skatai.models.bidding import BiddingMLP, BiddingModelConfig, dense_features
from skatai.data.bidding_features import FEATURE_SCHEMA

TRAINING_SCHEMA = "skatai.v2.bidding-training.v1"


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


class NeuralBiddingPolicy:
    def __init__(self, model: BiddingMLP, *, device: str = "cpu") -> None:
        self.device = torch.device(device)
        self.model = model.to(self.device)
        self.model.eval()

    @classmethod
    def load(cls, path: Path, *, device: str = "cpu") -> "NeuralBiddingPolicy":
        artifact = torch.load(path, map_location=device)
        if artifact.get("training_schema") != TRAINING_SCHEMA:
            raise ValueError("MODEL_TRAINING_SCHEMA_MISMATCH")
        if artifact.get("feature_schema") != FEATURE_SCHEMA:
            raise ValueError("MODEL_FEATURE_SCHEMA_MISMATCH")
        cfg = artifact.get("model") or {}
        model = BiddingMLP(
            BiddingModelConfig(
                hidden_dim=int(cfg["hidden_dim"]),
                depth=int(cfg["depth"]),
                dropout=float(cfg["dropout"]),
            )
        )
        model.load_state_dict(artifact["state_dict"])
        return cls(model, device=device)

    @torch.no_grad()
    def probability_continue(
        self,
        hand: Sequence[str],
        actor: int,
        bidder: int,
        answerer: int,
        bid_index: int,
        decision_role: str,
    ) -> float:
        hand_mask = encode_hand(hand)
        role = 0 if decision_role == "BIDDER" else 1
        x = dense_features(
            torch.tensor([hand_mask], dtype=torch.int64, device=self.device),
            torch.tensor([actor], dtype=torch.int64, device=self.device),
            torch.tensor([bidder], dtype=torch.int64, device=self.device),
            torch.tensor([answerer], dtype=torch.int64, device=self.device),
            torch.tensor([bid_index], dtype=torch.int64, device=self.device),
            torch.tensor([role], dtype=torch.int64, device=self.device),
        )
        return float(torch.sigmoid(self.model(x))[0].item())

    def auction(
        self,
        hands: Sequence[Sequence[str]],
        *,
        threshold: float = 0.5,
    ) -> AuctionResult:
        return simulate_auction(
            hands, self.probability_continue, threshold=threshold
        )
