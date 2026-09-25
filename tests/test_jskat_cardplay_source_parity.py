"""Static comparison against pinned JSkat 9ef3cd6 rule predicates.

The translated predicates come from Card.isTrump, SuitGrandRamschRule,
and NullRule at the source hashes in the JSkat legality provenance record.
This does not run the Java adapter or establish runtime parity.
"""

from itertools import combinations

from skatai.game.rules import legal_cards
from skatai.selfplay.cardplay import DECK


def _jskat_trump(card: str, game_type: str) -> bool:
    if game_type == "NULL":
        return False
    if card[1] == "J":
        return True
    trump_suit = {
        "CLUBS": "C", "SPADES": "S", "HEARTS": "H", "DIAMONDS": "D",
    }.get(game_type)
    return trump_suit is not None and card[0] == trump_suit


def _jskat_allowed(lead: str, hand: tuple[str, str], card: str, game_type: str) -> bool:
    if game_type == "NULL":
        return card[0] == lead[0] or not any(x[0] == lead[0] for x in hand)
    if _jskat_trump(lead, game_type):
        return _jskat_trump(card, game_type) or not any(
            _jskat_trump(x, game_type) for x in hand
        )
    return (card[0] == lead[0] and not _jskat_trump(card, game_type)) or not any(
        x[0] == lead[0] and not _jskat_trump(x, game_type) for x in hand
    )


def test_pinned_jskat_source_follow_suit_translation_matches_v2_pair_hands() -> None:
    checked = 0
    for game_type in ("CLUBS", "SPADES", "HEARTS", "DIAMONDS", "GRAND", "NULL"):
        for lead in DECK:
            for hand in combinations((card for card in DECK if card != lead), 2):
                source_allowed = tuple(
                    card for card in hand if _jskat_allowed(lead, hand, card, game_type)
                )
                assert legal_cards(hand, ((0, lead),), game_type) == source_allowed
                checked += 1
    assert checked == 89280
