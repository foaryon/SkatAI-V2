from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn
from torch.nn import functional as F

INPUT_DIM = 44
MODEL_SCHEMA = "skatai.v2.bidding-mlp.v1"


@dataclass(frozen=True)
class BiddingModelConfig:
    hidden_dim: int = 192
    depth: int = 3
    dropout: float = 0.05


class ResidualBlock(nn.Module):
    def __init__(self, width: int, dropout: float) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.fc1 = nn.Linear(width, width * 2)
        self.fc2 = nn.Linear(width * 2, width)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.norm(x)
        h = F.silu(self.fc1(h))
        h = self.dropout(self.fc2(h))
        return x + h


class BiddingMLP(nn.Module):
    def __init__(self, config: BiddingModelConfig = BiddingModelConfig()) -> None:
        super().__init__()
        self.config = config
        self.input = nn.Linear(INPUT_DIM, config.hidden_dim)
        self.blocks = nn.ModuleList(
            ResidualBlock(config.hidden_dim, config.dropout)
            for _ in range(config.depth)
        )
        self.norm = nn.LayerNorm(config.hidden_dim)
        self.output = nn.Linear(config.hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = F.silu(self.input(x))
        for block in self.blocks:
            h = block(h)
        return self.output(self.norm(h)).squeeze(-1)

    def artifact_config(self) -> dict:
        return {
            "model_schema": MODEL_SCHEMA,
            "input_dim": INPUT_DIM,
            **asdict(self.config),
        }


def dense_features(
    hand_mask: torch.Tensor,
    actor: torch.Tensor,
    bidder: torch.Tensor,
    answerer: torch.Tensor,
    bid_index: torch.Tensor,
    decision_role: torch.Tensor,
) -> torch.Tensor:
    mask = hand_mask.to(torch.int64)
    shifts = torch.arange(32, device=mask.device, dtype=torch.int64)
    hand = ((mask[:, None] >> shifts[None, :]) & 1).to(torch.float32)
    actor_oh = F.one_hot(actor.to(torch.int64), 3).to(torch.float32)
    bidder_oh = F.one_hot(bidder.to(torch.int64), 3).to(torch.float32)
    answerer_oh = F.one_hot(answerer.to(torch.int64), 3).to(torch.float32)
    bid = bid_index.to(torch.float32).unsqueeze(1) / 63.0
    role_oh = F.one_hot(decision_role.to(torch.int64), 2).to(torch.float32)
    return torch.cat((hand, actor_oh, bidder_oh, answerer_oh, bid, role_oh), dim=1)
