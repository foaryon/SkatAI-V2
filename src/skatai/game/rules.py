from __future__ import annotations

from typing import Iterable, Sequence

RULES_SCHEMA = "skatai.v2.skat-rules.v1"

CARD_POINTS = {
    "A": 11,
    "T": 10,
    "K": 4,
    "Q": 3,
    "J": 2,
    "9": 0,
    "8": 0,
    "7": 0,
}

CONTRACT_TO_GAME_TYPE = {
    "C": "CLUBS",
    "S": "SPADES",
    "H": "HEARTS",
    "D": "DIAMONDS",
    "G": "GRAND",
    "N": "NULL",
    "NO": "NULL",
}


def game_type_from_contract(contract: str) -> str:
    token = str(contract).upper().split(".", 1)[0]
    base = "NO" if token.startswith("NO") else token[:1]
    try:
        return CONTRACT_TO_GAME_TYPE[base]
    except KeyError as exc:
        raise ValueError(f"UNSUPPORTED_CONTRACT:{contract}") from exc


def card_points(card: str) -> int:
    if len(card) != 2 or card[1] not in CARD_POINTS:
        raise ValueError(f"BAD_CARD:{card}")
    return CARD_POINTS[card[1]]


def category(card: str, game_type: str) -> str:
    suit, rank = card[0], card[1]
    if game_type == "GRAND":
        return "TRUMP" if rank == "J" else suit
    if game_type in {"CLUBS", "SPADES", "HEARTS", "DIAMONDS"}:
        trump_suit = {
            "CLUBS": "C",
            "SPADES": "S",
            "HEARTS": "H",
            "DIAMONDS": "D",
        }[game_type]
        return "TRUMP" if rank == "J" or suit == trump_suit else suit
    if game_type == "NULL":
        return suit
    raise ValueError(f"UNSUPPORTED_GAME_TYPE:{game_type}")


def trick_strength(card: str, game_type: str, lead_category: str) -> tuple[int, int]:
    cat = category(card, game_type)
    suit, rank = card[0], card[1]

    if game_type == "NULL":
        null_order = {"7": 0, "8": 1, "9": 2, "T": 3, "J": 4, "Q": 5, "K": 6, "A": 7}
        return (1 if cat == lead_category else 0, null_order[rank])

    if cat == "TRUMP":
        if rank == "J":
            jack_order = {"D": 11, "H": 12, "S": 13, "C": 14}
            return (3, jack_order[suit])
        suit_order = {"7": 0, "8": 1, "9": 2, "Q": 3, "K": 4, "T": 5, "A": 6}
        return (3, suit_order[rank])

    normal_order = {"7": 0, "8": 1, "9": 2, "Q": 3, "K": 4, "T": 5, "A": 6}
    if rank == "J":
        raise ValueError("NONTRUMP_JACK_IMPOSSIBLE")
    return (2 if cat == lead_category else 1, normal_order[rank])


def trick_winner(trick: Sequence[Sequence[object]], game_type: str) -> int:
    if len(trick) != 3:
        raise ValueError(f"TRICK_REQUIRES_THREE_CARDS:{len(trick)}")
    lead_category = category(str(trick[0][1]), game_type)
    best_actor = int(trick[0][0])
    best = trick_strength(str(trick[0][1]), game_type, lead_category)
    for actor, card in trick[1:]:
        strength = trick_strength(str(card), game_type, lead_category)
        if strength > best:
            best = strength
            best_actor = int(actor)
    return best_actor


def legal_cards(
    hand: Sequence[str],
    current_trick: Sequence[Sequence[object]],
    game_type: str,
) -> tuple[str, ...]:
    cards = tuple(str(c) for c in hand)
    if not cards:
        return ()
    if not current_trick:
        return cards
    lead_category = category(str(current_trick[0][1]), game_type)
    followers = tuple(c for c in cards if category(c, game_type) == lead_category)
    return followers if followers else cards


def replay_tricks(
    plays: Iterable[Sequence[object]],
    *,
    game_type: str,
    declarer: int,
) -> dict:
    leader = 0
    current: list[tuple[int, str]] = []
    completed: list[dict] = []
    declarer_points = 0
    defender_points = 0

    normalized = [(int(x[0]), str(x[1])) for x in plays]
    for ordinal, (actor, card) in enumerate(normalized):
        expected = (leader + len(current)) % 3
        if actor != expected:
            raise ValueError(f"TURN_ORDER:{ordinal}:{actor}!={expected}")
        current.append((actor, card))
        if len(current) == 3:
            winner = trick_winner(current, game_type)
            points = sum(card_points(c) for _, c in current)
            if winner == declarer:
                declarer_points += points
            else:
                defender_points += points
            completed.append(
                {
                    "cards": tuple(current),
                    "winner": winner,
                    "points": points,
                }
            )
            leader = winner
            current = []

    expected_actor = (leader + len(current)) % 3
    return {
        "leader": leader,
        "current_trick": tuple(current),
        "expected_actor": expected_actor,
        "completed_tricks": tuple(completed),
        "declarer_trick_points": declarer_points,
        "defender_trick_points": defender_points,
    }
