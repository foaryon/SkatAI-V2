from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence
import re

CARD_RE = re.compile(r"^[CSHD][AKQJT987]$")
DECISION_INTERFACE_SCHEMA = "skatai.v2.product-interface.v1"


class SkatAIInterfaceError(ValueError):
    pass


def _cards(cards: Sequence[str], *, exact: int | None = None) -> tuple[str, ...]:
    out = tuple(str(c) for c in cards)
    if exact is not None and len(out) != exact:
        raise SkatAIInterfaceError(f"BAD_CARD_COUNT:{len(out)}!={exact}")
    if any(not CARD_RE.fullmatch(c) for c in out):
        raise SkatAIInterfaceError("BAD_CARD_TOKEN")
    if len(out) != len(set(out)):
        raise SkatAIInterfaceError("DUPLICATE_CARD")
    return out


def _bids(values: Sequence[int] | None) -> tuple[int, int, int] | None:
    if values is None:
        return None
    out = tuple(int(x) for x in values)
    if len(out) != 3:
        raise SkatAIInterfaceError(f"BAD_BID_VECTOR_LENGTH:{len(out)}")
    if any(x < 0 for x in out):
        raise SkatAIInterfaceError("NEGATIVE_BID")
    return (out[0], out[1], out[2])


def _optional_exact_cards(cards: Sequence[str], exact: int) -> tuple[str, ...]:
    if not cards:
        return ()
    return _cards(cards, exact=exact)


def _seat(x: int) -> int:
    x = int(x)
    if x not in (0, 1, 2):
        raise SkatAIInterfaceError(f"BAD_SEAT:{x}")
    return x


@dataclass(frozen=True)
class BiddingObservation:
    hand: tuple[str, ...]
    actor: int
    bidder: int
    answerer: int
    bid_index: int
    decision_role: str

    @classmethod
    def create(
        cls,
        hand: Sequence[str],
        *,
        actor: int,
        bidder: int,
        answerer: int,
        bid_index: int,
        decision_role: str,
    ) -> "BiddingObservation":
        role = str(decision_role)
        if role not in {"BIDDER", "ANSWERER"}:
            raise SkatAIInterfaceError(f"BAD_BIDDING_ROLE:{role}")
        idx = int(bid_index)
        if not 0 <= idx < 64:
            raise SkatAIInterfaceError(f"BAD_BID_INDEX:{idx}")
        return cls(
            hand=_cards(hand, exact=10),
            actor=_seat(actor),
            bidder=_seat(bidder),
            answerer=_seat(answerer),
            bid_index=idx,
            decision_role=role,
        )


@dataclass(frozen=True)
class DeclarationObservation:
    cards: tuple[str, ...]
    seat: int
    winning_bid: int
    picked_up_skat: bool
    legal_contracts: tuple[str, ...]
    max_accepted_bids_by_seat: tuple[int, int, int] | None = None

    @classmethod
    def create(
        cls,
        cards: Sequence[str],
        *,
        seat: int,
        winning_bid: int,
        picked_up_skat: bool,
        legal_contracts: Sequence[str],
        max_accepted_bids_by_seat: Sequence[int] | None = None,
    ) -> "DeclarationObservation":
        expected = 12 if picked_up_skat else 10
        legal = tuple(str(x) for x in legal_contracts)
        if not legal or any(not x for x in legal):
            raise SkatAIInterfaceError("EMPTY_OR_BAD_LEGAL_CONTRACTS")
        return cls(
            cards=_cards(cards, exact=expected),
            seat=_seat(seat),
            winning_bid=int(winning_bid),
            picked_up_skat=bool(picked_up_skat),
            legal_contracts=legal,
            max_accepted_bids_by_seat=_bids(max_accepted_bids_by_seat),
        )


@dataclass(frozen=True)
class DiscardObservation:
    hand12: tuple[str, ...]
    seat: int
    winning_bid: int
    max_accepted_bids_by_seat: tuple[int, int, int] | None = None

    @classmethod
    def create(
        cls,
        hand12: Sequence[str],
        *,
        seat: int,
        winning_bid: int,
        max_accepted_bids_by_seat: Sequence[int] | None = None,
    ) -> "DiscardObservation":
        return cls(
            hand12=_cards(hand12, exact=12),
            seat=_seat(seat),
            winning_bid=int(winning_bid),
            max_accepted_bids_by_seat=_bids(max_accepted_bids_by_seat),
        )


@dataclass(frozen=True)
class CardplayObservation:
    """Decision-time view; point fields are declarer then defenders for every seat."""
    hand: tuple[str, ...]
    seat: int
    declarer: int
    contract: str
    winning_bid: int
    current_trick: tuple[tuple[int, str], ...]
    played_cards: tuple[tuple[int, str], ...]
    legal_cards: tuple[str, ...]
    known_private_cards: tuple[str, ...] = ()
    points_self: int | None = None
    points_other: int | None = None
    max_accepted_bids_by_seat: tuple[int, int, int] | None = None
    skat_cards: tuple[str, ...] = ()
    blind_hand: bool = False
    open_hand_cards: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        hand: Sequence[str],
        *,
        seat: int,
        declarer: int,
        contract: str,
        winning_bid: int,
        current_trick: Sequence[tuple[int, str]],
        played_cards: Sequence[tuple[int, str]],
        legal_cards: Sequence[str],
        known_private_cards: Sequence[str] = (),
        points_self: int | None = None,
        points_other: int | None = None,
        max_accepted_bids_by_seat: Sequence[int] | None = None,
        skat_cards: Sequence[str] = (),
        blind_hand: bool = False,
        open_hand_cards: Sequence[str] = (),
    ) -> "CardplayObservation":
        h = _cards(hand)
        legal = _cards(legal_cards)
        if not legal:
            raise SkatAIInterfaceError("NO_LEGAL_CARDS")
        if not set(legal).issubset(set(h)):
            raise SkatAIInterfaceError("LEGAL_CARD_NOT_IN_HAND")

        def history(items: Sequence[tuple[int, str]]) -> tuple[tuple[int, str], ...]:
            out = tuple((_seat(s), _cards([c], exact=1)[0]) for s, c in items)
            return out

        trick = history(current_trick)
        if len(trick) > 2:
            raise SkatAIInterfaceError("CURRENT_TRICK_TOO_LONG")
        if len(open_hand_cards) > 10:
            raise SkatAIInterfaceError("OPEN_HAND_TOO_LONG")

        return cls(
            hand=h,
            seat=_seat(seat),
            declarer=_seat(declarer),
            contract=str(contract),
            winning_bid=int(winning_bid),
            current_trick=trick,
            played_cards=history(played_cards),
            legal_cards=legal,
            known_private_cards=_cards(known_private_cards),
            points_self=None if points_self is None else int(points_self),
            points_other=None if points_other is None else int(points_other),
            max_accepted_bids_by_seat=_bids(max_accepted_bids_by_seat),
            skat_cards=_optional_exact_cards(skat_cards, 2),
            blind_hand=bool(blind_hand),
            open_hand_cards=_cards(open_hand_cards) if open_hand_cards else (),
        )


class BiddingPolicy(Protocol):
    def probability_continue(self, observation: BiddingObservation) -> float: ...


class DeclarationPolicy(Protocol):
    def choose_contract(self, observation: DeclarationObservation) -> str: ...


class DiscardPolicy(Protocol):
    def choose_discard(self, observation: DiscardObservation) -> tuple[str, str]: ...


class CardplayPolicy(Protocol):
    def play_card(self, observation: CardplayObservation) -> str: ...


@dataclass
class SkatAI:
    bidding: BiddingPolicy
    declaration: DeclarationPolicy
    discard: DiscardPolicy
    cardplay: CardplayPolicy
    bidding_threshold: float = 0.5

    def decide_bid(self, observation: BiddingObservation) -> str:
        p = float(self.bidding.probability_continue(observation))
        if not 0.0 <= p <= 1.0:
            raise SkatAIInterfaceError(f"BAD_BIDDING_PROBABILITY:{p}")
        return "CONTINUE" if p >= self.bidding_threshold else "PASS"

    def choose_contract(self, observation: DeclarationObservation) -> str:
        contract = str(self.declaration.choose_contract(observation))
        if contract not in observation.legal_contracts:
            raise SkatAIInterfaceError(f"ILLEGAL_CONTRACT:{contract}")
        return contract

    def choose_discard(self, observation: DiscardObservation) -> tuple[str, str]:
        cards = tuple(self.discard.choose_discard(observation))
        if len(cards) != 2 or len(set(cards)) != 2:
            raise SkatAIInterfaceError("DISCARD_MUST_BE_TWO_DISTINCT_CARDS")
        if not set(cards).issubset(set(observation.hand12)):
            raise SkatAIInterfaceError("DISCARD_NOT_OWNED")
        return cards[0], cards[1]

    def play_card(self, observation: CardplayObservation) -> str:
        card = str(self.cardplay.play_card(observation))
        if card not in observation.legal_cards:
            raise SkatAIInterfaceError(f"ILLEGAL_CARDPLAY:{card}")
        return card
