"""Offline endgame labels with legal observation fields as the only inputs.

The complete post-declaration hands are privileged teacher material. Callers
must enforce dataset lineage and split membership before using any label.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Sequence

from skatai.evaluation.endgame_teacher import solve_endgame
from skatai.game.rules import (
    card_points, game_type_from_contract, legal_cards, trick_winner,
)
from skatai.runtime.interface import CardplayObservation
from skatai.selfplay.cardplay import DECK


def label_endgame_observation(
    observation: CardplayObservation,
    *,
    post_declaration_hands: Sequence[Sequence[str]],
) -> dict:
    """Replay a private deal, then emit a teacher target without hidden inputs."""
    if len(post_declaration_hands) != 3:
        raise ValueError("ENDGAME_LABEL_THREE_HANDS_REQUIRED")
    if (observation.known_private_cards or observation.open_hand_cards
            or (observation.seat != observation.declarer and observation.skat_cards)
            or (observation.blind_hand and observation.skat_cards)):
        raise ValueError("ENDGAME_LABEL_UNSUPPORTED_PRIVATE_FIELDS")
    hands = [list(map(str, hand)) for hand in post_declaration_hands]
    flat = [card for hand in hands for card in hand]
    if (any(len(hand) != 10 for hand in hands) or len(set(flat)) != 30
            or not set(flat).issubset(DECK)):
        raise ValueError("ENDGAME_LABEL_INVALID_POST_DECLARATION_HANDS")
    game_type = game_type_from_contract(observation.contract)
    leader = 0
    current: list[tuple[int, str]] = []
    declarer_points = 0
    defender_points = 0
    for ordinal, (seat, card) in enumerate(observation.played_cards):
        actor = (leader + len(current)) % 3
        if seat != actor or card not in legal_cards(hands[actor], current, game_type):
            raise ValueError(f"ENDGAME_LABEL_HISTORY_INVALID:{ordinal}")
        hands[actor].remove(card)
        current.append((actor, card))
        if len(current) == 3:
            winner = trick_winner(current, game_type)
            points = sum(card_points(c) for _, c in current)
            if winner == observation.declarer:
                declarer_points += points
            else:
                defender_points += points
            leader = winner
            current = []
    actor = (leader + len(current)) % 3
    if (actor != observation.seat or tuple(current) != observation.current_trick
            or set(hands[actor]) != set(observation.hand)
            or set(legal_cards(hands[actor], current, game_type))
            != set(observation.legal_cards)):
        raise ValueError("ENDGAME_LABEL_OBSERVATION_MISMATCH")
    if observation.points_other is not None and observation.points_other != defender_points:
        raise ValueError("ENDGAME_LABEL_POINT_MISMATCH")
    known_skat_points = (
        sum(card_points(card) for card in observation.skat_cards)
        if actor == observation.declarer and not observation.blind_hand else 0
    )
    if (observation.points_self is not None
            and observation.points_self != declarer_points + known_skat_points):
        raise ValueError("ENDGAME_LABEL_POINT_MISMATCH")
    if set(observation.skat_cards) & set(flat):
        raise ValueError("ENDGAME_LABEL_SKAT_OVERLAPS_PLAY_HANDS")

    answer = solve_endgame(
        hands, contract=observation.contract, declarer=observation.declarer,
        leader=leader, current_trick=current,
    )
    # No teacher input hash or full hands appear in the learner row. A hash of
    # a tiny hidden endgame could itself reveal the deal by enumeration.
    return {
        "schema": "skatai.v2.offline-endgame-learner-row.v1",
        "observation": asdict(observation),
        "target_card": answer["card"],
        "target_action_values": answer["action_values"],
        "target_remaining_utility": answer["declarer_remaining_utility"],
        "teacher_schema": answer["schema"],
        "training_approved": False,
    }
