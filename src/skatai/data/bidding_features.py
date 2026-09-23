from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SUITS = ("C", "S", "H", "D")
RANKS = ("7", "8", "9", "T", "J", "Q", "K", "A")
CARDS = tuple(s + r for s in SUITS for r in RANKS)
CARD_INDEX = {card: i for i, card in enumerate(CARDS)}
FEATURE_SCHEMA = "skatai.v2.bidding-features.v1"


@dataclass(frozen=True)
class EncodedBiddingDecision:
    hand_mask: int
    actor: int
    bidder: int
    answerer: int
    bid_index: int
    decision_role: int
    target_continue: int

    def dense(self) -> list[float]:
        hand = [0.0] * 32
        for i in range(32):
            if self.hand_mask & (1 << i):
                hand[i] = 1.0

        # 32 hand bits + actor(3) + bidder(3) + answerer(3) +
        # normalized bid index + role(2) = 44 floats.
        out = hand
        out += [1.0 if self.actor == i else 0.0 for i in range(3)]
        out += [1.0 if self.bidder == i else 0.0 for i in range(3)]
        out += [1.0 if self.answerer == i else 0.0 for i in range(3)]
        out += [self.bid_index / 63.0]
        out += [1.0, 0.0] if self.decision_role == 0 else [0.0, 1.0]
        return out


def encode_hand(cards: Sequence[str]) -> int:
    if len(cards) != 10:
        raise ValueError(f"EXPECTED_10_CARDS:{len(cards)}")
    if len(set(cards)) != 10:
        raise ValueError("DUPLICATE_CARD_IN_HAND")
    mask = 0
    for card in cards:
        try:
            idx = CARD_INDEX[str(card)]
        except KeyError as exc:
            raise ValueError(f"UNKNOWN_CARD:{card}") from exc
        mask |= 1 << idx
    return mask


def encode_decision(row: Mapping[str, Any]) -> EncodedBiddingDecision:
    actor = int(row["actor"])
    bidder = int(row["bidder"])
    answerer = int(row["answerer"])
    bid_index = int(row["bid_index"])
    role = str(row["decision_role"])
    target = str(row["target"])

    if actor not in (0, 1, 2) or bidder not in (0, 1, 2) or answerer not in (0, 1, 2):
        raise ValueError("BAD_SEAT")
    if not 0 <= bid_index < 64:
        raise ValueError(f"BAD_BID_INDEX:{bid_index}")
    if role not in {"BIDDER", "ANSWERER"}:
        raise ValueError(f"BAD_ROLE:{role}")
    if target not in {"PASS", "CONTINUE"}:
        raise ValueError(f"BAD_TARGET:{target}")

    return EncodedBiddingDecision(
        hand_mask=encode_hand(row["hand"]),
        actor=actor,
        bidder=bidder,
        answerer=answerer,
        bid_index=bid_index,
        decision_role=0 if role == "BIDDER" else 1,
        target_continue=1 if target == "CONTINUE" else 0,
    )
