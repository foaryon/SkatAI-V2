"""Deterministic, legal cardplay episodes for controlled V2 self-play.

This supplies a clean V2 self-play primitive. Auction and declaration can be
passed in as validated inputs. Full contract scoring and strength acceptance
are outside this episode type.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import random
from typing import Protocol, Sequence

from skatai.game.rules import (
    card_points,
    game_type_from_contract,
    legal_cards,
    trick_winner,
)
from skatai.runtime.interface import CardplayObservation

SCHEMA = "skatai.v2.selfplay.cardplay-hand.v1"
PICKUP_SCHEMA = "skatai.v2.selfplay.cardplay-pickup.v1"
DECK = tuple(suit + rank for suit in "CSHD" for rank in "ATKQJ987")


class CardplayPolicy(Protocol):
    def play_card(self, observation: CardplayObservation) -> str: ...


@dataclass(frozen=True)
class Deal:
    seed: int
    hands: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
    skat: tuple[str, str]
    identity_sha256: str


@dataclass(frozen=True)
class CardplayEpisode:
    schema: str
    deal_seed: int
    deal_sha256: str
    contract: str
    declarer: int
    plays: tuple[tuple[int, str], ...]
    trick_winners: tuple[int, ...]
    declarer_trick_points: int
    defender_trick_points: int
    skat_points: int
    declarer_final_points: int
    declarer_won: bool


def make_deal(seed: int) -> Deal:
    cards = list(DECK)
    random.Random(int(seed)).shuffle(cards)
    hands = (tuple(cards[:10]), tuple(cards[10:20]), tuple(cards[20:30]))
    skat = (cards[30], cards[31])
    payload = {"schema": SCHEMA, "seed": int(seed), "hands": hands, "skat": skat}
    identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return Deal(int(seed), hands, skat, identity)


class RandomLegalPolicy:
    def __init__(self, seed: int) -> None:
        self._rng = random.Random(int(seed))

    def play_card(self, observation: CardplayObservation) -> str:
        return self._rng.choice(observation.legal_cards)


def run_cardplay(
    deal: Deal,
    *,
    contract: str,
    declarer: int,
    policies: Sequence[CardplayPolicy],
    final_hand: Sequence[str] | None = None,
    final_skat: Sequence[str] | None = None,
    winning_bid: int = 0,
    max_accepted_bids_by_seat: Sequence[int] = (0, 0, 0),
) -> CardplayEpisode:
    """Play 10 legal tricks; policies see only their own cards and public state."""
    if len(policies) != 3:
        raise ValueError("THREE_POLICIES_REQUIRED")
    if declarer not in (0, 1, 2):
        raise ValueError("BAD_DECLARER")
    game_type = game_type_from_contract(contract)
    if len(DECK) != 32 or len(set(DECK)) != 32:
        raise ValueError("BAD_DECK_DEFINITION")
    dealt = [*deal.hands[0], *deal.hands[1], *deal.hands[2], *deal.skat]
    if len(dealt) != 32 or set(dealt) != set(DECK):
        raise ValueError("INVALID_DEAL")
    if deal != make_deal(deal.seed):
        raise ValueError("DEAL_IDENTITY_MISMATCH")

    if (final_hand is None) != (final_skat is None):
        raise ValueError("PICKUP_HAND_AND_SKAT_REQUIRED_TOGETHER")
    pickup = final_hand is not None
    skat = tuple(final_skat) if pickup else deal.skat
    hands = [list(hand) for hand in deal.hands]
    if pickup:
        replacement = tuple(final_hand)
        original = set((*deal.hands[declarer], *deal.skat))
        if (len(replacement) != 10 or len(skat) != 2
                or len(set((*replacement, *skat))) != 12
                or set((*replacement, *skat)) != original):
            raise ValueError("INVALID_POST_PICKUP_PARTITION")
        hands[declarer] = list(replacement)
    skat_points = sum(card_points(card) for card in skat)
    plays: list[tuple[int, str]] = []
    winners: list[int] = []
    current: list[tuple[int, str]] = []
    leader = 0
    declarer_points = 0
    defender_points = 0
    for _ in range(30):
        actor = (leader + len(current)) % 3
        legal = legal_cards(hands[actor], current, game_type)
        view = CardplayObservation.create(
            hands[actor],
            seat=actor,
            declarer=declarer,
            contract=contract,
            winning_bid=winning_bid,
            current_trick=current,
            played_cards=plays,
            legal_cards=legal,
            # Product/B0 point order is declarer, defenders for every seat.
            # Only the pickup declarer sees points in their own buried cards.
            points_self=declarer_points + (skat_points if pickup and actor == declarer else 0),
            points_other=defender_points,
            max_accepted_bids_by_seat=max_accepted_bids_by_seat,
            skat_cards=skat if pickup and actor == declarer else (),
            blind_hand=not pickup,
            open_hand_cards=tuple(hands[declarer]) if "O" in contract else (),
        )
        card = str(policies[actor].play_card(view))
        if card not in legal:
            raise ValueError(f"ILLEGAL_POLICY_CARD:{actor}:{card}")
        hands[actor].remove(card)
        move = (actor, card)
        plays.append(move)
        current.append(move)
        if len(current) == 3:
            winner = trick_winner(current, game_type)
            points = sum(card_points(c) for _, c in current)
            if winner == declarer:
                declarer_points += points
            else:
                defender_points += points
            winners.append(winner)
            leader = winner
            current = []

    if any(hands) or current or len(winners) != 10:
        raise ValueError("INCOMPLETE_CARDPLAY_EPISODE")
    final_points = declarer_points + skat_points
    if final_points + defender_points != 120:
        raise ValueError("CARD_POINT_CONSERVATION_FAILED")
    declarer_won = (
        declarer not in winners if game_type == "NULL" else final_points >= 61
    )
    return CardplayEpisode(
        schema=PICKUP_SCHEMA if pickup else SCHEMA,
        deal_seed=deal.seed,
        deal_sha256=deal.identity_sha256,
        contract=contract,
        declarer=declarer,
        plays=tuple(plays),
        trick_winners=tuple(winners),
        declarer_trick_points=declarer_points,
        defender_trick_points=defender_points,
        skat_points=skat_points,
        declarer_final_points=final_points,
        declarer_won=declarer_won,
    )
