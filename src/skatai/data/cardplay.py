from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from skatai.data.sgf import semantic_identity
from skatai.game.bidding import replay
from skatai.game.rules import (
    card_points,
    game_type_from_contract,
    legal_cards,
    replay_tricks,
)
from skatai.runtime.interface import CardplayObservation


class CardplayReconstructionError(ValueError):
    pass


@dataclass(frozen=True)
class CardplayEvent:
    game_id: str
    source: str
    play_ordinal: int
    observation: CardplayObservation
    target_card: str
    actor_name: str = ""
    actor_rating: float | None = None
    actor_class: str = "unknown"
    source_semantic_sha256: str = ""
    raw_sha256: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "source": self.source,
            "play_ordinal": self.play_ordinal,
            "observation": asdict(self.observation),
            "target_card": self.target_card,
            "actor_name": self.actor_name,
            "actor_rating": self.actor_rating,
            "actor_class": self.actor_class,
            "source_semantic_sha256": self.source_semantic_sha256,
            "raw_sha256": self.raw_sha256,
        }


def _game_id(record: Mapping[str, Any]) -> str:
    # The migrated Legacy corpus carries a different semantic hash scheme.
    # Recompute with the clean V2 projection so cross-source split membership
    # and deterministic sampling use one transcript identity.
    try:
        return semantic_identity(dict(record))
    except (KeyError, TypeError, ValueError) as exc:
        raise CardplayReconstructionError("V2_SEMANTIC_IDENTITY_UNAVAILABLE") from exc


def _three_hands(record: Mapping[str, Any]) -> list[list[str]]:
    raw = record.get("initial_hands")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 3:
        raise CardplayReconstructionError("BAD_INITIAL_HANDS")
    hands = [list(map(str, hand)) for hand in raw]
    if any(len(hand) != 10 for hand in hands):
        raise CardplayReconstructionError("INITIAL_HAND_NOT_TEN")
    flat = [card for hand in hands for card in hand]
    if len(set(flat)) != 30:
        raise CardplayReconstructionError("DUPLICATE_INITIAL_HAND_CARD")
    return hands


def _skat(record: Mapping[str, Any]) -> tuple[str, str]:
    raw = tuple(str(x) for x in (record.get("skat_initial") or ()))
    if len(raw) != 2 or raw[0] == raw[1]:
        raise CardplayReconstructionError("BAD_INITIAL_SKAT")
    return raw[0], raw[1]


def _contract(record: Mapping[str, Any]) -> tuple[str, str]:
    raw = record.get("announcement")
    if raw is None:
        raw = record.get("contract")
    if raw is None or not str(raw):
        raise CardplayReconstructionError("MISSING_CONTRACT")
    contract = str(raw).split(".", 1)[0]
    game_type = game_type_from_contract(contract)
    recorded_game_type = record.get("game_type")
    if recorded_game_type is not None and str(recorded_game_type) != game_type:
        raise CardplayReconstructionError(
            f"GAME_TYPE_MISMATCH:{recorded_game_type}!={game_type}"
        )
    return contract, game_type


def max_accepted_bids_by_seat(
    bidding_history: Sequence[str],
    *,
    expected_declarer: int | None = None,
    expected_winning_bid: int | None = None,
) -> tuple[int, int, int]:
    result = replay(tuple(str(x) for x in bidding_history))
    if not result.ok or result.all_pass or result.winner is None:
        raise CardplayReconstructionError(
            f"BIDDING_REPLAY_FAILED:{result.error or 'NO_WINNER'}"
        )
    if expected_declarer is not None and int(result.winner) != int(expected_declarer):
        raise CardplayReconstructionError(
            f"DECLARER_MISMATCH:{result.winner}!={expected_declarer}"
        )
    if (
        expected_winning_bid is not None
        and result.winning_bid is not None
        and int(result.winning_bid) != int(expected_winning_bid)
    ):
        raise CardplayReconstructionError(
            f"WINNING_BID_MISMATCH:{result.winning_bid}!={expected_winning_bid}"
        )

    maxima = [0, 0, 0]
    for action in result.actions:
        if action["native_action"] == "p":
            continue
        actor = int(action["actor"])
        offer = int(action["before"]["current_offer"])
        maxima[actor] = max(maxima[actor], offer)
    return maxima[0], maxima[1], maxima[2]


def _final_hands(
    record: Mapping[str, Any],
    *,
    hands: list[list[str]],
    skat: tuple[str, str],
    declarer: int,
) -> tuple[list[list[str]], tuple[str, str] | None]:
    is_hand = bool(record.get("is_hand"))
    current = [list(hand) for hand in hands]
    if is_hand:
        return current, None

    raw_discards = record.get("discards")
    if raw_discards is None:
        raise CardplayReconstructionError("PICKUP_GAME_MISSING_DISCARDS")
    discards = tuple(str(x) for x in raw_discards)
    if len(discards) != 2 or discards[0] == discards[1]:
        raise CardplayReconstructionError("BAD_DISCARDS")

    current[declarer].extend(skat)
    for card in discards:
        if card not in current[declarer]:
            raise CardplayReconstructionError(f"DISCARD_NOT_OWNED:{card}")
        current[declarer].remove(card)
    if len(current[declarer]) != 10:
        raise CardplayReconstructionError("DECLARER_FINAL_HAND_NOT_TEN")
    return current, (discards[0], discards[1])


def _actor_metadata(
    record: Mapping[str, Any],
    actor: int,
) -> tuple[str, float | None, str]:
    raw_players = record.get("players") or ()
    raw_ratings = record.get("ratings") or ()
    raw_classes = record.get("actor_classes") or ()

    name = str(raw_players[actor]) if actor < len(raw_players) else ""
    rating: float | None = None
    if actor < len(raw_ratings):
        try:
            parsed = float(raw_ratings[actor])
        except (TypeError, ValueError):
            parsed = 0.0
        if parsed > 0.0:
            rating = parsed
    actor_class = (
        str(raw_classes[actor])
        if actor < len(raw_classes) and str(raw_classes[actor])
        else "unknown"
    )
    return name, rating, actor_class


def reconstruct_cardplay_events(record: Mapping[str, Any]) -> tuple[CardplayEvent, ...]:
    declarer = int(record.get("declarer", -1))
    if declarer not in (0, 1, 2):
        raise CardplayReconstructionError(f"BAD_DECLARER:{declarer}")

    hands = _three_hands(record)
    skat = _skat(record)
    if set(skat) & {card for hand in hands for card in hand}:
        raise CardplayReconstructionError("SKAT_OVERLAPS_INITIAL_HAND")
    if len({card for hand in hands for card in hand} | set(skat)) != 32:
        raise CardplayReconstructionError("DEAL_NOT_32_UNIQUE_CARDS")

    contract, game_type = _contract(record)
    winning_bid_raw = record.get("bid_level")
    if winning_bid_raw is None:
        raise CardplayReconstructionError("MISSING_WINNING_BID")
    winning_bid = int(winning_bid_raw)

    bidding_history = record.get("bidding_history")
    if not isinstance(bidding_history, Sequence) or isinstance(
        bidding_history, (str, bytes)
    ):
        raise CardplayReconstructionError("BAD_BIDDING_HISTORY")
    bid_maxima = max_accepted_bids_by_seat(
        tuple(str(x) for x in bidding_history),
        expected_declarer=declarer,
        expected_winning_bid=winning_bid,
    )

    remaining, discards = _final_hands(
        record,
        hands=hands,
        skat=skat,
        declarer=declarer,
    )
    is_hand = bool(record.get("is_hand"))
    is_ouvert = bool(record.get("is_ouvert"))

    raw_plays = record.get("plays") or ()
    plays = tuple((int(x[0]), str(x[1])) for x in raw_plays)
    game_id = _game_id(record)
    source = str(record.get("source") or "")
    prefix: list[tuple[int, str]] = []
    events: list[CardplayEvent] = []

    for ordinal, (actor, target) in enumerate(plays):
        if actor not in (0, 1, 2):
            raise CardplayReconstructionError(f"BAD_PLAY_ACTOR:{ordinal}:{actor}")

        try:
            trick_state = replay_tricks(
                prefix,
                game_type=game_type,
                declarer=declarer,
            )
        except ValueError as exc:
            raise CardplayReconstructionError(
                f"TRICK_REPLAY:{ordinal}:{exc}"
            ) from exc

        expected_actor = int(trick_state["expected_actor"])
        if actor != expected_actor:
            raise CardplayReconstructionError(
                f"TURN_ORDER:{ordinal}:{actor}!={expected_actor}"
            )

        hand = tuple(remaining[actor])
        legal = legal_cards(
            hand,
            trick_state["current_trick"],
            game_type,
        )
        if target not in hand:
            raise CardplayReconstructionError(
                f"TARGET_NOT_OWNED:{ordinal}:{actor}:{target}"
            )
        if target not in legal:
            raise CardplayReconstructionError(
                f"TARGET_ILLEGAL:{ordinal}:{actor}:{target}"
            )

        declarer_points = int(trick_state["declarer_trick_points"])
        defender_points = int(trick_state["defender_trick_points"])
        visible_skat: tuple[str, ...] = ()
        if actor == declarer and not is_hand:
            assert discards is not None
            visible_skat = discards
            declarer_points += sum(card_points(card) for card in discards)

        open_remaining: tuple[str, ...] = ()
        if is_ouvert:
            open_remaining = tuple(remaining[declarer])

        obs = CardplayObservation.create(
            hand,
            seat=actor,
            declarer=declarer,
            contract=contract,
            winning_bid=winning_bid,
            current_trick=trick_state["current_trick"],
            played_cards=prefix,
            legal_cards=legal,
            known_private_cards=(),
            points_self=declarer_points,
            points_other=defender_points,
            max_accepted_bids_by_seat=bid_maxima,
            skat_cards=visible_skat,
            blind_hand=is_hand,
            open_hand_cards=open_remaining,
        )
        actor_name, actor_rating, actor_class = _actor_metadata(record, actor)
        events.append(
            CardplayEvent(
                game_id=game_id,
                source=source,
                play_ordinal=ordinal,
                observation=obs,
                target_card=target,
                actor_name=actor_name,
                actor_rating=actor_rating,
                actor_class=actor_class,
                source_semantic_sha256=str(record.get("semantic_sha256") or ""),
                raw_sha256=str(record.get("raw_sha256") or ""),
            )
        )

        remaining[actor].remove(target)
        prefix.append((actor, target))

    if len(plays) == 30 and any(remaining):
        raise CardplayReconstructionError("COMPLETE_GAME_LEAVES_CARDS_IN_HAND")

    return tuple(events)
