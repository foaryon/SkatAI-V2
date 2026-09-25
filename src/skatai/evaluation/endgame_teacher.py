"""Exact, bounded, full-information endgame teacher for offline research.

All three hands are required. The result must never be used as a decision-time
input to a deployed policy: opponents' cards are hidden in real Skat.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from typing import Sequence

from skatai.game.rules import card_points, game_type_from_contract, legal_cards, trick_winner
from skatai.selfplay.cardplay import DECK

MAX_CARDS_PER_HAND = 3
CONTRACTS = frozenset("CSHDGN")


def solve_endgame(
    hands: Sequence[Sequence[str]], *, contract: str, declarer: int,
    leader: int, current_trick: Sequence[Sequence[object]] = (),
) -> dict:
    """Return a perfect-information minimax move and remaining utility.

    Suit/Grand utility is additional declarer trick points. Null utility is
    minus the number of future tricks won by declarer. Defenders cooperate.
    Lexically first legal card breaks equal-valued moves.
    """
    token = str(contract).upper()
    if token not in CONTRACTS:
        raise ValueError("ENDGAME_TEACHER_BASIC_CONTRACT_ONLY")
    if declarer not in (0, 1, 2) or leader not in (0, 1, 2):
        raise ValueError("ENDGAME_TEACHER_BAD_SEAT")
    if len(hands) != 3:
        raise ValueError("ENDGAME_TEACHER_THREE_HANDS_REQUIRED")
    normalized = tuple(tuple(sorted(str(card) for card in hand)) for hand in hands)
    trick = tuple((int(seat), str(card)) for seat, card in current_trick)
    if len(trick) > 2 or any(seat != (leader + i) % 3 for i, (seat, _) in enumerate(trick)):
        raise ValueError("ENDGAME_TEACHER_BAD_TRICK_ORDER")
    total = sum(map(len, normalized)) + len(trick)
    if total == 0 or total % 3 or total > 3 * MAX_CARDS_PER_HAND:
        raise ValueError("ENDGAME_TEACHER_CARD_BOUND")
    cards_per_seat = total // 3
    if any(len(hand) != cards_per_seat - int(any(s == seat for s, _ in trick))
           for seat, hand in enumerate(normalized)):
        raise ValueError("ENDGAME_TEACHER_HAND_COUNTS")
    cards = [card for hand in normalized for card in hand] + [card for _, card in trick]
    if len(set(cards)) != len(cards) or not set(cards).issubset(DECK):
        raise ValueError("ENDGAME_TEACHER_INVALID_CARDS")
    input_identity = hashlib.sha256(json.dumps(
        {"contract": token, "declarer": declarer, "leader": leader,
         "hands": normalized, "current_trick": trick},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()

    game_type = game_type_from_contract(token)
    visited = 0

    @lru_cache(maxsize=None)
    def search(state: tuple[tuple[str, ...], ...], lead: int,
               partial: tuple[tuple[int, str], ...]
               ) -> tuple[int, str | None, tuple[tuple[str, int], ...]]:
        nonlocal visited
        visited += 1
        if not any(state) and not partial:
            return 0, None, ()
        actor = (lead + len(partial)) % 3
        options = sorted(legal_cards(state[actor], partial, game_type))
        if not options:
            raise ValueError("ENDGAME_TEACHER_INVALID_STATE")
        best_value: int | None = None
        best_card: str | None = None
        action_values: list[tuple[str, int]] = []
        for card in options:
            next_hands = list(state)
            next_hands[actor] = tuple(c for c in state[actor] if c != card)
            played = partial + ((actor, card),)
            if len(played) == 3:
                winner = trick_winner(played, game_type)
                gain = (-1 if winner == declarer else 0) if token == "N" else (
                    sum(card_points(c) for _, c in played) if winner == declarer else 0
                )
                future = search(tuple(next_hands), winner, ())[0]
                value = gain + future
            else:
                value = search(tuple(next_hands), lead, played)[0]
            action_values.append((card, value))
            if best_value is None or (
                value > best_value if actor == declarer else value < best_value
            ):
                best_value, best_card = value, card
        assert best_value is not None
        return best_value, best_card, tuple(action_values)

    value, card, action_values = search(normalized, leader, trick)
    return {
        "schema": "skatai.v2.full-information-endgame-teacher.v1",
        "input_sha256": input_identity,
        "contract": token,
        "declarer": declarer,
        "actor": (leader + len(trick)) % 3,
        "card": card,
        "action_values": dict(action_values),
        "declarer_remaining_utility": value,
        "utility_kind": "minus_declarer_tricks" if token == "N" else "declarer_trick_points",
        "states_visited": visited,
        "offline_teacher_only": True,
    }
