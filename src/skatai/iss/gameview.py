from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from skatai.game.rules import (
    card_points,
    game_type_from_contract,
    legal_cards as legal_card_choices,
    replay_tricks,
)
from skatai.iss.protocol import DealView, UNKNOWN_CARD, WireMove


class ISSGameViewError(ValueError):
    pass


class ISSPhase(str, Enum):
    BID = "BID"
    ANSWER = "ANSWER"
    SKAT_OR_HAND_DECL = "SKAT_OR_HAND_DECL"
    GET_SKAT = "GET_SKAT"
    DISCARD_AND_DECL = "DISCARD_AND_DECL"
    CARDPLAY = "CARDPLAY"
    FINISHED = "FINISHED"


@dataclass(frozen=True)
class ISSNormalizedState:
    viewer_seat: int
    phase: ISSPhase
    to_move: int | None
    hand: tuple[str, ...]
    declarer: int | None
    winning_bid: int | None
    max_accepted_bids_by_seat: tuple[int, int, int]
    contract: str | None
    picked_up_skat: bool
    known_skat: tuple[str, ...]
    discarded_cards: tuple[str, ...]
    open_hand_cards: tuple[str, ...]
    played_cards: tuple[tuple[int, str], ...]
    current_trick: tuple[tuple[int, str], ...]
    legal_cards: tuple[str, ...]
    declarer_visible_points: int
    defender_points: int


def _deal(moves: Sequence[WireMove]) -> DealView:
    found = [m for m in moves if m.kind == "initial_deal"]
    if len(found) != 1:
        raise ISSGameViewError(f"EXPECTED_ONE_INITIAL_DEAL:{len(found)}")
    payload = found[0].payload
    if not isinstance(payload, DealView):
        raise ISSGameViewError("BAD_INITIAL_DEAL_PAYLOAD")
    return payload


def _viewer(deal: DealView) -> tuple[int, tuple[str, ...]]:
    known = [
        (seat, tuple(hand))
        for seat, hand in enumerate(deal.hands)
        if all(card != UNKNOWN_CARD for card in hand)
    ]
    if len(known) != 1:
        raise ISSGameViewError(f"EXPECTED_ONE_KNOWN_PLAYER_HAND:{len(known)}")
    return known[0]


def replay_player_view(moves: Sequence[WireMove]) -> ISSNormalizedState:
    deal = _deal(moves)
    viewer, initial_hand = _viewer(deal)

    phase = ISSPhase.BID
    to_move: int | None = 1
    bidder: int | None = 1
    asked: int | None = 0
    max_bid = 0
    max_bids = [0, 0, 0]
    declarer: int | None = None

    contract: str | None = None
    picked_up = False
    known_skat: tuple[str, ...] = ()
    discarded: tuple[str, ...] = ()
    open_hand: tuple[str, ...] = ()
    hand = list(initial_hand)
    played: list[tuple[int, str]] = []
    half_declared_contract: str | None = None
    resigned: set[int] = set()

    consumed_deal = False
    for move in moves:
        if move.kind == "initial_deal":
            if consumed_deal:
                raise ISSGameViewError("DUPLICATE_INITIAL_DEAL")
            consumed_deal = True
            continue

        actor = None if move.actor == "w" else int(move.actor)

        if phase == ISSPhase.FINISHED:
            # Terminal server annotations do not create another decision.
            continue

        if phase in {ISSPhase.BID, ISSPhase.ANSWER}:
            if actor is None or actor != to_move:
                raise ISSGameViewError(f"AUCTION_TURN_MISMATCH:{actor}!={to_move}")

            if phase == ISSPhase.BID:
                if move.kind == "pass":
                    if max_bids[actor] == 0:
                        max_bids[actor] = 1
                    if bidder == 0:
                        phase = ISSPhase.FINISHED
                        to_move = None
                    elif bidder == 1:
                        bidder, asked, to_move = 2, 0, 2
                    elif bidder == 2:
                        if max_bid < 18:
                            bidder, asked, to_move = 0, None, 0
                        else:
                            declarer = asked
                            phase = ISSPhase.SKAT_OR_HAND_DECL
                            to_move = declarer
                            bidder = asked = None
                    else:
                        raise ISSGameViewError("BAD_AUCTION_BIDDER")
                elif move.kind == "bid":
                    bid = int(move.payload)
                    if bid <= max_bid:
                        raise ISSGameViewError(f"NONINCREASING_BID:{bid}<={max_bid}")
                    max_bids[actor] = bid
                    max_bid = bid
                    phase = ISSPhase.ANSWER
                    to_move = asked
                    if actor == 0:
                        declarer = 0
                        phase = ISSPhase.SKAT_OR_HAND_DECL
                        to_move = 0
                        bidder = asked = None
                else:
                    raise ISSGameViewError(f"BAD_BID_MOVE:{move.kind}")
            else:
                if move.kind == "answer_yes":
                    max_bids[actor] = max_bid
                    phase = ISSPhase.BID
                    to_move = bidder
                elif move.kind == "pass":
                    if max_bids[actor] == 0:
                        max_bids[actor] = 1
                    if bidder == 1:
                        phase = ISSPhase.BID
                        bidder, asked, to_move = 2, 1, 2
                    else:
                        declarer = bidder
                        phase = ISSPhase.SKAT_OR_HAND_DECL
                        to_move = declarer
                        bidder = asked = None
                else:
                    raise ISSGameViewError(f"BAD_ANSWER_MOVE:{move.kind}")
            continue

        if move.kind in {"timeout", "leave"}:
            phase = ISSPhase.FINISHED
            to_move = None
            continue

        if phase == ISSPhase.SKAT_OR_HAND_DECL:
            if actor != declarer or actor != to_move:
                raise ISSGameViewError("DECLARER_TURN_MISMATCH")
            if move.kind == "skat_request":
                picked_up = True
                phase = ISSPhase.GET_SKAT
                to_move = None
            elif move.kind == "declaration":
                payload = dict(move.payload)
                contract = str(payload["game_type"])
                cards = tuple(payload["cards"])
                if "O" in contract and cards:
                    if len(cards) != 10:
                        raise ISSGameViewError(
                            f"HAND_OUVERT_EXPECTS_10_CARDS:{len(cards)}"
                        )
                    open_hand = tuple(c for c in cards if c != UNKNOWN_CARD)
                phase = ISSPhase.CARDPLAY
                to_move = 0
            else:
                raise ISSGameViewError(f"BAD_SKAT_OR_HAND_MOVE:{move.kind}")
            continue

        if phase == ISSPhase.GET_SKAT:
            if actor is not None or move.kind != "skat_delivery":
                raise ISSGameViewError(f"BAD_GET_SKAT_MOVE:{move.kind}")
            cards = tuple(move.payload)
            if all(c != UNKNOWN_CARD for c in cards):
                known_skat = cards
                if viewer == declarer:
                    hand.extend(cards)
            phase = ISSPhase.DISCARD_AND_DECL
            to_move = declarer
            continue

        if phase == ISSPhase.DISCARD_AND_DECL:
            if actor != declarer or actor != to_move:
                raise ISSGameViewError("DISCARD_TURN_MISMATCH")
            if move.kind == "declaration":
                payload = dict(move.payload)
                new_contract = str(payload["game_type"])
                cards = tuple(payload["cards"])
                contract = new_contract
                if not cards:
                    half_declared_contract = new_contract
                    continue
                if len(cards) not in (2, 12):
                    raise ISSGameViewError(
                        f"PICKUP_DECL_BAD_CARD_COUNT:{len(cards)}"
                    )
                discarded = tuple(c for c in cards[:2] if c != UNKNOWN_CARD)
                if viewer == declarer and len(discarded) == 2:
                    for card in discarded:
                        if card not in hand:
                            raise ISSGameViewError(
                                f"DISCARD_NOT_IN_VIEWER_HAND:{card}"
                            )
                        hand.remove(card)
                if len(cards) == 12 and "O" in new_contract:
                    open_hand = tuple(c for c in cards[2:] if c != UNKNOWN_CARD)
                phase = ISSPhase.CARDPLAY
                to_move = 0
                half_declared_contract = None
            elif move.kind == "discard_only":
                if half_declared_contract is None:
                    raise ISSGameViewError(
                        "DISCARD_ONLY_WITHOUT_HALF_DECLARATION"
                    )
                cards = tuple(move.payload)
                if len(cards) not in (2, 12):
                    raise ISSGameViewError(
                        f"DISCARD_ONLY_BAD_CARD_COUNT:{len(cards)}"
                    )
                discarded = tuple(
                    c for c in cards[:2] if c != UNKNOWN_CARD
                )
                if viewer == declarer and len(discarded) == 2:
                    for card in discarded:
                        if card not in hand:
                            raise ISSGameViewError(
                                f"DISCARD_NOT_IN_VIEWER_HAND:{card}"
                            )
                        hand.remove(card)
                contract = half_declared_contract
                if len(cards) == 12:
                    if "O" not in contract:
                        raise ISSGameViewError(
                            "OPEN_HAND_CARDS_WITHOUT_OUVERT_CONTRACT"
                        )
                    open_hand = tuple(
                        c for c in cards[2:] if c != UNKNOWN_CARD
                    )
                phase = ISSPhase.CARDPLAY
                to_move = 0
                half_declared_contract = None
            else:
                raise ISSGameViewError(
                    f"BAD_DISCARD_DECL_MOVE:{move.kind}"
                )
            continue

        if phase == ISSPhase.CARDPLAY:
            if move.kind == "show_cards":
                shown = tuple(move.payload)
                if shown:
                    open_hand = shown
                continue
            if move.kind == "resign":
                if actor is None:
                    raise ISSGameViewError("WORLD_RESIGN")
                resigned.add(actor)
                if actor == declarer or (
                    declarer is not None
                    and len([x for x in resigned if x != declarer]) >= 2
                ):
                    phase = ISSPhase.FINISHED
                    to_move = None
                continue
            if move.kind != "cardplay" or actor is None:
                raise ISSGameViewError(f"BAD_CARDPLAY_MOVE:{move.kind}")
            if actor != to_move:
                raise ISSGameViewError(
                    f"CARDPLAY_TURN_MISMATCH:{actor}!={to_move}"
                )
            card = str(move.payload)
            played.append((actor, card))
            if actor == viewer:
                if card not in hand:
                    raise ISSGameViewError(f"OWN_PLAY_NOT_IN_HAND:{card}")
                hand.remove(card)
            if contract is None or declarer is None:
                raise ISSGameViewError("CARDPLAY_WITHOUT_CONTRACT")
            replay = replay_tricks(
                played,
                game_type=game_type_from_contract(contract),
                declarer=declarer,
            )
            to_move = int(replay["expected_actor"])
            if len(replay["completed_tricks"]) == 10:
                phase = ISSPhase.FINISHED
                to_move = None
            continue

        raise ISSGameViewError(f"UNHANDLED_PHASE:{phase}")

    current_trick: tuple[tuple[int, str], ...] = ()
    legal: tuple[str, ...] = ()
    declarer_trick_points = 0
    defender_points = 0

    if contract is not None and declarer is not None:
        replay = replay_tricks(
            played,
            game_type=game_type_from_contract(contract),
            declarer=declarer,
        )
        current_trick = tuple(replay["current_trick"])
        declarer_trick_points = int(replay["declarer_trick_points"])
        defender_points = int(replay["defender_trick_points"])
        if phase == ISSPhase.CARDPLAY and to_move == viewer:
            legal = legal_card_choices(
                hand,
                current_trick,
                game_type_from_contract(contract),
            )

    declarer_visible_points = declarer_trick_points
    if viewer == declarer and picked_up and len(discarded) == 2:
        declarer_visible_points += sum(card_points(c) for c in discarded)

    return ISSNormalizedState(
        viewer_seat=viewer,
        phase=phase,
        to_move=to_move,
        hand=tuple(hand),
        declarer=declarer,
        winning_bid=max_bid if declarer is not None else None,
        max_accepted_bids_by_seat=(max_bids[0], max_bids[1], max_bids[2]),
        contract=contract,
        picked_up_skat=picked_up,
        known_skat=known_skat,
        discarded_cards=discarded,
        open_hand_cards=open_hand,
        played_cards=tuple(played),
        current_trick=current_trick,
        legal_cards=legal,
        declarer_visible_points=declarer_visible_points,
        defender_points=defender_points,
    )
