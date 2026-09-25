"""Bounded observation-only endgame determinization for research.

This is a candidate treatment, not an accepted deployment policy. It samples
hidden cards uniformly from assignments consistent with public void evidence,
then scores legal actions with the exact small endgame solver.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import random

from skatai.evaluation.endgame_teacher import CONTRACTS, solve_endgame
from skatai.game.rules import category, game_type_from_contract, legal_cards, replay_tricks
from skatai.runtime.interface import CardplayObservation
from skatai.selfplay.cardplay import DECK

SCHEMA = "skatai.v2.observation-endgame-pimc.v1"
SUPPORTED_CONTRACTS = CONTRACTS | frozenset(base + "H" for base in CONTRACTS)


def _void_categories(observation: CardplayObservation, game_type: str) -> tuple[frozenset[str], ...]:
    voids = [set() for _ in range(3)]
    for ordinal, (seat, card) in enumerate(observation.played_cards):
        if ordinal % 3 == 0:
            lead = category(card, game_type)
        elif category(card, game_type) != lead:
            voids[seat].add(lead)
    return tuple(frozenset(row) for row in voids)


def sample_legal_worlds(
    observation: CardplayObservation, *, max_worlds: int = 64, seed: int = 0,
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Enumerate bounded compatible allocations, then sample without replacement."""
    if not 1 <= max_worlds <= 256:
        raise ValueError("PIMC_WORLD_LIMIT_INVALID")
    if (observation.contract not in SUPPORTED_CONTRACTS
            or observation.known_private_cards or observation.open_hand_cards):
        raise ValueError("PIMC_UNSUPPORTED_OBSERVATION")
    game_type = game_type_from_contract(observation.contract)
    replay = replay_tricks(
        observation.played_cards, game_type=game_type,
        declarer=observation.declarer,
    )
    if (replay["expected_actor"] != observation.seat
            or replay["current_trick"] != observation.current_trick
            or set(legal_cards(observation.hand, observation.current_trick, game_type))
            != set(observation.legal_cards)):
        raise ValueError("PIMC_OBSERVATION_HISTORY_MISMATCH")
    played = [card for _, card in observation.played_cards]
    own = tuple(observation.hand)
    known_skat = tuple(observation.skat_cards)
    seen = played + list(own) + list(known_skat)
    if (len(set(seen)) != len(seen) or not set(seen).issubset(DECK)
            or len(known_skat) not in (0, 2)
            or (observation.seat != observation.declarer and known_skat)
            or (observation.blind_hand and known_skat)):
        raise ValueError("PIMC_VISIBLE_CARD_STATE_INVALID")
    plays_by_seat = Counter(seat for seat, _ in observation.played_cards)
    counts = [10 - plays_by_seat[seat] for seat in range(3)]
    if (any(count < 0 or count > 3 for count in counts)
            or len(own) != counts[observation.seat]
            or len(observation.played_cards) < 21):
        raise ValueError("PIMC_ENDGAME_BOUND_INVALID")
    unknown = tuple(sorted(set(DECK) - set(seen)))
    slots = {seat: counts[seat] for seat in range(3) if seat != observation.seat}
    slots[3] = 2 - len(known_skat)  # 3 is the hidden skat.
    if sum(slots.values()) != len(unknown):
        raise ValueError("PIMC_CARD_COUNT_MISMATCH")
    voids = _void_categories(observation, game_type)
    if any(category(card, game_type) in voids[observation.seat] for card in own):
        raise ValueError("PIMC_OWN_HAND_CONTRADICTS_HISTORY")

    # Tiny endgames contain at most eight unknown cards. Enumerating all
    # compatible assignments removes rejection bias and makes failure exact.
    assigned: dict[int, list[str]] = {seat: [] for seat in slots}
    worlds: list[tuple[tuple[str, ...], ...]] = []

    def visit(index: int) -> None:
        if index == len(unknown):
            hands = [tuple(sorted(own)) if seat == observation.seat
                     else tuple(sorted(assigned[seat])) for seat in range(3)]
            worlds.append(tuple(hands))
            return
        card = unknown[index]
        for seat in sorted(slots):
            if len(assigned[seat]) == slots[seat]:
                continue
            if seat != 3 and category(card, game_type) in voids[seat]:
                continue
            assigned[seat].append(card)
            visit(index + 1)
            assigned[seat].pop()

    visit(0)
    if not worlds:
        raise ValueError("PIMC_NO_COMPATIBLE_WORLD")
    rng = random.Random(seed)
    if len(worlds) > max_worlds:
        worlds = rng.sample(worlds, max_worlds)
    return tuple(worlds)


def choose_endgame_card(
    observation: CardplayObservation, *, max_worlds: int = 64, seed: int = 0,
) -> dict:
    """Return one legal move and diagnostic action values from sampled worlds."""
    worlds = sample_legal_worlds(observation, max_worlds=max_worlds, seed=seed)
    replay = replay_tricks(
        observation.played_cards,
        game_type=game_type_from_contract(observation.contract),
        declarer=observation.declarer,
    )
    sums = {card: 0 for card in observation.legal_cards}
    for hands in worlds:
        answer = solve_endgame(
            hands, contract=observation.contract[0],
            declarer=observation.declarer, leader=replay["leader"],
            current_trick=observation.current_trick,
        )
        for card in sums:
            sums[card] += answer["action_values"][card]
    maximize = observation.seat == observation.declarer
    selected = sorted(sums, key=lambda card: ((-1 if maximize else 1) * sums[card], card))[0]
    identity = hashlib.sha256(json.dumps(
        {"played": observation.played_cards, "hand": observation.hand,
         "contract": observation.contract, "seat": observation.seat,
         "declarer": observation.declarer, "seed": seed, "max_worlds": max_worlds},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return {
        "schema": SCHEMA,
        "card": selected,
        "world_count": len(worlds),
        "action_mean_values": {card: sums[card] / len(worlds) for card in sorted(sums)},
        "observation_selection_sha256": identity,
        "research_only": True,
    }


class PIMCOverridePolicy:
    """Research wrapper that changes only supported late cardplay decisions."""

    def __init__(self, fallback, *, max_worlds: int = 32, seed: int = 0):
        if not 1 <= max_worlds <= 256:
            raise ValueError("PIMC_WORLD_LIMIT_INVALID")
        self.fallback = fallback
        self.max_worlds = max_worlds
        self.seed = seed
        self.override_count = 0
        self.fallback_count = 0

    def play_card(self, observation: CardplayObservation) -> str:
        supported = (
            observation.contract in SUPPORTED_CONTRACTS
            and len(observation.played_cards) >= 21
            and not observation.known_private_cards
            and not observation.open_hand_cards
        )
        if not supported:
            self.fallback_count += 1
            return self.fallback.play_card(observation)
        answer = choose_endgame_card(
            observation, max_worlds=self.max_worlds, seed=self.seed,
        )
        self.override_count += 1
        return answer["card"]
