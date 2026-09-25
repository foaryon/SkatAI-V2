"""Auditable basic Skat contract value for controlled self-play.

This scorer covers ordinary suit/Grand and Null variants. Announced hand
games require explicit research gates and are excluded from default scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from skatai.game.bidding import replay as replay_bidding
from skatai.game.rules import card_points, game_type_from_contract, legal_cards, replay_tricks
from skatai.selfplay.bidding import SCHEMA as BIDDING_SCHEMA
from skatai.selfplay.cardplay import DECK, PICKUP_SCHEMA, SCHEMA as HAND_SCHEMA, make_deal
from skatai.selfplay.declaration import HAND_CONTRACTS, PICKUP_CONTRACTS, SCHEMA as DECLARATION_SCHEMA
from skatai.selfplay.game import SCHEMA as GAME_SCHEMA, GameEpisode

SCHEMA = "skatai.v2.selfplay.basic-score.v3"
BASE_VALUES = {"C": 12, "S": 11, "H": 10, "D": 9, "G": 24}
NULL_VALUES = {"N": 23, "NH": 35, "NO": 46, "NHO": 59}
DEFAULT_BASIC_CONTRACTS = frozenset(NULL_VALUES) | frozenset(
    base + suffix for base in BASE_VALUES for suffix in ("", "H")
)
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
    return count if with_top else -count


def score_basic_game(
    *,
    contract: str,
    winning_bid: int,
    declarer_cards: Sequence[str],
    declarer_points: int,
    declarer_tricks: int,
    research_announced_schneider: bool = False,
    research_announced_schwarz_ouvert: bool = False,
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

    announced_schneider = len(token) == 3 and token.endswith("HS")
    announced_schwarz = len(token) == 3 and token.endswith("HZ")
    ouvert = len(token) == 3 and token.endswith("HO")
    if announced_schneider and not research_announced_schneider:
        raise ValueError("ANNOUNCED_SCHNEIDER_REQUIRES_RESEARCH_GATE")
    if (announced_schwarz or ouvert) and not research_announced_schwarz_ouvert:
        raise ValueError("ANNOUNCED_SCHWARZ_OUVERT_REQUIRES_RESEARCH_GATE")
    hand = (len(token) == 2 and token.endswith("H")) or announced_schneider or announced_schwarz or ouvert
    base = token[:-2] if len(token) == 3 else token[:-1] if hand else token
    if base not in BASE_VALUES or token not in (base, base + "H", base + "HS", base + "HZ", base + "HO"):
        raise ValueError(f"UNSUPPORTED_BASIC_CONTRACT:{contract}")
    matadors = _matadors(set(cards), base)
    if announced_schwarz or ouvert:
        # ISkO 2.5.1, 2.5.7–2.5.8: failed Schwarz/ouvert is lost at
        # least at the announced level, regardless of card points.
        won_by_points = declarer_tricks == 10
        level = abs(matadors) + (7 if ouvert else 6)
    else:
        won_by_points = declarer_points >= (90 if announced_schneider else 61)
        if won_by_points:
            schneider = declarer_points >= 90
            schwarz = declarer_tricks == 10
        else:
            schneider = declarer_points <= 30
            schwarz = declarer_tricks == 0
        schneider_levels = 2 if announced_schneider else int(schneider)
        level = abs(matadors) + 1 + int(hand) + schneider_levels + int(schwarz)
    natural = BASE_VALUES[base] * level
    overbid = winning_bid > natural
    if overbid:
        loss_value = BASE_VALUES[base] * ((winning_bid + BASE_VALUES[base] - 1) // BASE_VALUES[base])
        signed = -2 * loss_value
    else:
        signed = natural if won_by_points else -2 * natural
    return BasicScore(SCHEMA, token, winning_bid, matadors, level, natural,
                      overbid, won_by_points and not overbid, signed)


def score_basic_episode(
    episode: GameEpisode, *, research_announced_schwarz_ouvert: bool = False,
) -> BasicScore:
    """Reconcile the full episode before assigning a bounded basic value."""
    declaration, play = episode.declaration, episode.cardplay
    if declaration is None or play is None:
        raise ValueError("NO_PLAYED_GAME_TO_SCORE")
    deal = make_deal(episode.deal_seed)
    if (episode.schema != GAME_SCHEMA
            or episode.bidding.schema != BIDDING_SCHEMA
            or declaration.schema != DECLARATION_SCHEMA
            or episode.bidding.deal_seed != deal.seed
            or declaration.deal_seed != deal.seed
            or play.deal_seed != deal.seed
            or episode.bidding.deal_sha256 != deal.identity_sha256
            or episode.deal_sha256 != deal.identity_sha256
            or declaration.deal_sha256 != deal.identity_sha256
            or play.deal_sha256 != deal.identity_sha256
            or declaration.contract != play.contract
            or declaration.declarer != play.declarer
            or declaration.declarer != episode.bidding.winner
            or declaration.winning_bid != episode.bidding.winning_bid):
        raise ValueError("EPISODE_IDENTITY_MISMATCH")
    auction = replay_bidding(tuple(
        token for seat, action in episode.bidding.actions
        for token in (str(seat), action)
    ))
    bid_maxima = [0, 0, 0]
    for action in auction.actions:
        if action["native_action"] != "p":
            seat = action["actor"]
            bid_maxima[seat] = max(bid_maxima[seat], action["before"]["current_offer"])
    if (not auction.ok or auction.all_pass
            or len(auction.actions) != len(episode.bidding.actions)
            or auction.winner != episode.bidding.winner
            or auction.winning_bid != episode.bidding.winning_bid
            or tuple(bid_maxima) != episode.bidding.max_accepted_bids_by_seat):
        raise ValueError("EPISODE_BIDDING_REPLAY_MISMATCH")
    expected_play_schema = PICKUP_SCHEMA if declaration.picked_up_skat else HAND_SCHEMA
    if play.schema != expected_play_schema:
        raise ValueError("EPISODE_PLAY_MODE_MISMATCH")
    original = set((*deal.hands[declaration.declarer], *deal.skat))
    if declaration.picked_up_skat:
        if (declaration.contract not in PICKUP_CONTRACTS
                or len(declaration.discarded) != 2
                or tuple(declaration.final_skat) != tuple(declaration.discarded)):
            raise ValueError("EPISODE_PICKUP_CONTRACT_OR_DISCARD_MISMATCH")
    elif (declaration.contract not in HAND_CONTRACTS
          or declaration.discarded
          or declaration.final_hand != deal.hands[declaration.declarer]
          or declaration.final_skat != deal.skat):
        raise ValueError("EPISODE_HAND_CONTRACT_OR_SKAT_MISMATCH")
    if (len(declaration.final_hand) != 10 or len(declaration.final_skat) != 2
            or len(set((*declaration.final_hand, *declaration.final_skat))) != 12
            or set((*declaration.final_hand, *declaration.final_skat)) != original):
        raise ValueError("EPISODE_CARD_PARTITION_MISMATCH")
    if len(play.plays) != 30 or len(play.trick_winners) != 10:
        raise ValueError("EPISODE_INCOMPLETE_PLAY")
    played_cards = tuple(card for _, card in play.plays)
    if len(set(played_cards)) != 30 or set(played_cards) != set(DECK) - set(declaration.final_skat):
        raise ValueError("EPISODE_PLAYED_CARD_SET_MISMATCH")
    game_type = game_type_from_contract(play.contract)
    hands = [list(hand) for hand in deal.hands]
    hands[play.declarer] = list(declaration.final_hand)
    current: list[tuple[int, str]] = []
    for actor, card in play.plays:
        if actor not in (0, 1, 2):
            raise ValueError("EPISODE_BAD_PLAY_ACTOR")
        if card not in legal_cards(hands[actor], current, game_type):
            raise ValueError("EPISODE_ILLEGAL_OR_UNOWNED_PLAY")
        hands[actor].remove(card)
        current.append((actor, card))
        if len(current) == 3:
            current = []
    if any(hands):
        raise ValueError("EPISODE_HAND_NOT_EXHAUSTED")
    replay = replay_tricks(
        play.plays, game_type=game_type,
        declarer=play.declarer,
    )
    winners = tuple(trick["winner"] for trick in replay["completed_tricks"])
    skat_points = sum(card_points(c) for c in declaration.final_skat)
    if (winners != play.trick_winners
            or replay["declarer_trick_points"] != play.declarer_trick_points
            or replay["defender_trick_points"] != play.defender_trick_points
            or skat_points != play.skat_points
            or play.declarer_final_points != play.declarer_trick_points + skat_points
            or play.declarer_final_points + play.defender_trick_points != 120):
        raise ValueError("EPISODE_REPLAY_OR_POINT_MISMATCH")
    declarer_tricks = sum(w == play.declarer for w in winners)
    if game_type == "NULL":
        expected_play_win = declarer_tricks == 0
    elif "O" in play.contract[1:] or "Z" in play.contract[1:]:
        expected_play_win = declarer_tricks == 10
    elif "S" in play.contract[1:]:
        expected_play_win = play.declarer_final_points >= 90
    else:
        expected_play_win = play.declarer_final_points >= 61
    if play.declarer_won != expected_play_win:
        raise ValueError("EPISODE_PLAY_WIN_FLAG_MISMATCH")
    return score_basic_game(
        contract=play.contract, winning_bid=declaration.winning_bid,
        declarer_cards=(*declaration.final_hand, *declaration.final_skat),
        declarer_points=play.declarer_final_points,
        declarer_tricks=declarer_tricks,
        research_announced_schwarz_ouvert=research_announced_schwarz_ouvert,
    )
