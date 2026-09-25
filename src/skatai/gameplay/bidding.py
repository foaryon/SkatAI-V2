from __future__ import annotations

from pathlib import Path
from typing import Sequence

import torch

from skatai.data.bidding_features import encode_hand, FEATURE_SCHEMA
from skatai.gameplay.auction import (
    AuctionDecision, AuctionResult, DecisionProbability,
    simulate_auction, simulate_max_bid_auction,
)
from skatai.models.bidding import BiddingMLP, BiddingModelConfig, dense_features

TRAINING_SCHEMA = "skatai.v2.bidding-training.v1"


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
