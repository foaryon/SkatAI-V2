from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BID_VALUES = (
    18, 20, 22, 23, 24, 27, 30, 33, 35, 36, 40, 44, 45, 46, 48, 50,
    54, 55, 59, 60, 63, 66, 70, 72, 77, 80, 81, 84, 88, 90, 96, 99,
    100, 108, 110, 117, 120, 121, 126, 130, 132, 135, 140, 143,
    144, 150, 153, 154, 156, 160, 162, 165, 168, 170, 176, 180,
    187, 192, 198, 204, 216, 240, 264,
)


@dataclass
class Replay:
    ok: bool
    actions: list[dict[str, Any]] = field(default_factory=list)
    winner: int | None = None
    winning_bid: int | None = None
    error: str | None = None


class BiddingState:
    """Protocol state for the two-stage Skat bidding duel."""

    def __init__(self) -> None:
        self.bidder = 1
        self.answerer = 0
        self.bid_index = 0
        self.bidder_turn = True
        self.finished = False
        self.winner: int | None = None
        self.winning_bid: int | None = None

    @property
    def offer(self) -> int:
        return BID_VALUES[self.bid_index]

    @property
    def expected_actor(self) -> int:
        return self.bidder if self.bidder_turn else self.answerer

    @property
    def decision_role(self) -> str:
        return "BIDDER" if self.bidder_turn else "ANSWERER"

    def legal_native_actions(self) -> tuple[str, str]:
        if self.bidder_turn:
            return str(self.offer), "p"
        return "y", "p"

    def apply(self, actor: int, action: str) -> dict[str, Any]:
        if self.finished:
            raise ValueError("ACTION_AFTER_TERMINAL")
        if actor != self.expected_actor:
            raise ValueError(f"ACTOR_MISMATCH:{actor}!={self.expected_actor}")

        legal = self.legal_native_actions()
        if action not in legal:
            raise ValueError(f"ILLEGAL_ACTION:{actor}:{action}:legal={','.join(legal)}")

        before = {
            "current_offer": self.offer,
            "bid_index": self.bid_index,
            "bidder": self.bidder,
            "answerer": self.answerer,
            "decision_role": self.decision_role,
            "legal_native_actions": list(legal),
        }

        if self.bidder_turn:
            if action == "p":
                if self.bidder == 2:
                    if self.bid_index == 0:
                        self.bidder = self.answerer
                        self.bidder_turn = True
                    else:
                        self.winner = self.answerer
                        self.winning_bid = BID_VALUES[self.bid_index - 1]
                        self.finished = True
                elif self.bidder == self.answerer:
                    self.finished = True
                else:
                    self.bidder = 2
                    self.bidder_turn = True
            else:
                if self.bidder == self.answerer:
                    self.winner = self.bidder
                    self.winning_bid = self.offer
                    self.finished = True
                else:
                    self.bidder_turn = False
        else:
            if action == "y":
                self.bid_index += 1
                if self.bid_index >= len(BID_VALUES):
                    raise ValueError("OFFER_EXHAUSTED")
                self.bidder_turn = True
            else:
                if self.bidder == 2:
                    self.winner = self.bidder
                    self.winning_bid = self.offer
                    self.finished = True
                else:
                    old_bidder = self.bidder
                    self.answerer = old_bidder
                    self.bidder = 2
                    self.bid_index += 1
                    if self.bid_index >= len(BID_VALUES):
                        raise ValueError("OFFER_EXHAUSTED")
                    self.bidder_turn = True

        return before


def native_pairs(tokens: list[str] | tuple[str, ...]) -> list[tuple[int, str]]:
    tokens = [str(x) for x in tokens]
    pairs: list[tuple[int, str]] = []
    i = 0
    while i + 1 < len(tokens) and tokens[i] in {"0", "1", "2"}:
        action = tokens[i + 1]
        if action == "s":
            break
        pairs.append((int(tokens[i]), action))
        i += 2
    return pairs


def replay(tokens: list[str] | tuple[str, ...]) -> Replay:
    state = BiddingState()
    actions: list[dict[str, Any]] = []
    for ordinal, (actor, action) in enumerate(native_pairs(tokens)):
        try:
            before = state.apply(actor, action)
        except (ValueError, IndexError) as exc:
            return Replay(False, actions=actions, error=str(exc))
        actions.append(
            {
                "ordinal": ordinal,
                "actor": actor,
                "native_action": action,
                "target": "PASS" if action == "p" else "CONTINUE",
                "before": before,
            }
        )
    if not state.finished:
        return Replay(False, actions=actions, error="NONTERMINAL_PREFIX")
    if state.winner is None:
        return Replay(False, actions=actions, error="ALL_PASS_UNPLAYED")
    return Replay(True, actions=actions, winner=state.winner, winning_bid=state.winning_bid)
