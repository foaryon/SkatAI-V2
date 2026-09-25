"""Auditable basic Skat contract value for controlled self-play.

This scorer covers ordinary suit/Grand and Null variants. Announced
Schneider/Schwarz and suit/Grand ouvert are rejected until separately
validated against an external rules oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from skatai.selfplay.cardplay import DECK

SCHEMA = "skatai.v2.selfplay.basic-score.v1"
BASE_VALUES = {"C": 12, "S": 11, "H": 10, "D": 9, "G": 24}
NULL_VALUES = {"N": 23, "NH": 35, "NO": 46, "NHO": 59}
JACKS = ("CJ", "SJ", "HJ", "DJ")


@dataclass(frozen=True)
class BasicScore:
    schema: str
    contract: str
    winning_bid: int
    matadors: int | None
    game_level: int | None
    natural_value: int
    overbid: bool
    won: bool
    signed_game_value: int


def _matadors(cards: set[str], base: str) -> int:
    trumps = JACKS if base == "G" else JACKS + tuple(
        base + rank for rank in "ATKQ987"
    )
    with_top = trumps[0] in cards
    count = 0
    for card in trumps:
        if (card in cards) != with_top:
            break
        count += 1
    return count


def score_basic_game(
    *,
    contract: str,
    winning_bid: int,
    declarer_cards: Sequence[str],
    declarer_points: int,
    declarer_tricks: int,
) -> BasicScore:
    """Score a validated completed game; `declarer_cards` includes final skat."""
    token = str(contract).upper()
    cards = tuple(str(c) for c in declarer_cards)
    if (len(cards) != 12 or len(set(cards)) != 12
            or any(c not in DECK for c in cards)):
        raise ValueError("INVALID_DECLARER_TWELVE_CARDS")
    if winning_bid < 18 or not 0 <= declarer_points <= 120 or not 0 <= declarer_tricks <= 10:
        raise ValueError("INVALID_COMPLETED_GAME_TOTALS")
    if token in NULL_VALUES:
        value = NULL_VALUES[token]
        if winning_bid > value:
            raise ValueError("NULL_OVERBID_REQUIRES_EXTERNAL_RULE_VALIDATION")
        won = declarer_tricks == 0
        return BasicScore(SCHEMA, token, winning_bid, None, None, value, False,
                          won, value if won else -2 * value)

    hand = len(token) == 2 and token.endswith("H")
    base = token[:-1] if hand else token
    if base not in BASE_VALUES or token not in (base, base + "H"):
        raise ValueError(f"UNSUPPORTED_BASIC_CONTRACT:{contract}")
    matadors = _matadors(set(cards), base)
    won_by_points = declarer_points >= 61
    if won_by_points:
        schneider = declarer_points >= 90
        schwarz = declarer_tricks == 10
    else:
        schneider = declarer_points <= 30
        schwarz = declarer_tricks == 0
    level = matadors + 1 + int(hand) + int(schneider) + int(schwarz)
    natural = BASE_VALUES[base] * level
    overbid = winning_bid > natural
    if overbid:
        loss_value = BASE_VALUES[base] * ((winning_bid + BASE_VALUES[base] - 1) // BASE_VALUES[base])
        signed = -2 * loss_value
    else:
        signed = natural if won_by_points else -2 * natural
    return BasicScore(SCHEMA, token, winning_bid, matadors, level, natural,
                      overbid, won_by_points and not overbid, signed)
