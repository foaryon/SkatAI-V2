from __future__ import annotations

from dataclasses import dataclass
import re

ISS_PROTOCOL_SCHEMA = "skatai.v2.iss-wire-primitives.v1"

PLAYER_ACTORS = {"0", "1", "2"}
WORLD_ACTOR = "w"
VALID_ACTORS = PLAYER_ACTORS | {WORLD_ACTOR}
SUITS = "CSHD"
RANKS = "AKQJT987"
CARD_RE = re.compile(r"^[CSHD][AKQJT987]$")
GAME_TYPE_RE = re.compile(r"^[GCSHDN](?:O)?(?:H)?(?:S)?(?:Z)?$")


class ISSProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class DealView:
    hands: tuple[
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]
    skat: tuple[str, str]


@dataclass(frozen=True)
class WireMove:
    actor: str
    action: str
    kind: str
    payload: object


def _card(token: str, *, allow_unknown: bool = False) -> str:
    if allow_unknown and token == "??":
        return token
    if not CARD_RE.fullmatch(token):
        raise ISSProtocolError(f"BAD_CARD:{token}")
    return token


def parse_deal(action: str) -> DealView:
    parts = action.split("|")
    if len(parts) != 4:
        raise ISSProtocolError(f"BAD_DEAL_SECTION_COUNT:{len(parts)}")
    groups = [tuple(x.split(".")) for x in parts]
    expected = (10, 10, 10, 2)
    for i, (group, n) in enumerate(zip(groups, expected)):
        if len(group) != n:
            raise ISSProtocolError(f"BAD_DEAL_GROUP_COUNT:{i}:{len(group)}!={n}")
        for token in group:
            _card(token, allow_unknown=True)
    return DealView(
        hands=(groups[0], groups[1], groups[2]),
        skat=(groups[3][0], groups[3][1]),
    )


def parse_game_declaration(action: str) -> dict:
    parts = action.split(".")
    game_type = parts[0]
    if not GAME_TYPE_RE.fullmatch(game_type):
        raise ISSProtocolError(f"BAD_GAME_TYPE:{game_type}")
    cards = tuple(_card(x) for x in parts[1:])
    if cards and len(cards) < 2:
        raise ISSProtocolError("DECLARATION_WITH_SINGLE_CARD")
    return {"game_type": game_type, "cards": cards}


def classify_action(actor: str, action: str) -> tuple[str, object]:
    if actor not in VALID_ACTORS:
        raise ISSProtocolError(f"BAD_ACTOR:{actor}")
    if not action:
        raise ISSProtocolError("EMPTY_ACTION")

    if "|" in action:
        if actor != WORLD_ACTOR:
            raise ISSProtocolError("INITIAL_DEAL_MUST_BE_WORLD")
        return "initial_deal", parse_deal(action)

    if action.isdigit():
        value = int(action)
        if value <= 0:
            raise ISSProtocolError(f"BAD_BID:{value}")
        return "bid", value

    if action in {"y", "p"}:
        if actor == WORLD_ACTOR:
            raise ISSProtocolError("WORLD_CANNOT_ANSWER_BID")
        return ("answer_yes" if action == "y" else "pass"), action

    if action == "s":
        if actor == WORLD_ACTOR:
            raise ISSProtocolError("WORLD_CANNOT_PICKUP_SKAT")
        return "skat_request", action

    parts = action.split(".")
    if len(parts) == 2 and all(CARD_RE.fullmatch(x) for x in parts):
        if actor != WORLD_ACTOR:
            raise ISSProtocolError("SKAT_DELIVERY_MUST_BE_WORLD")
        return "skat_delivery", tuple(parts)

    if GAME_TYPE_RE.fullmatch(parts[0]):
        if actor == WORLD_ACTOR:
            raise ISSProtocolError("WORLD_CANNOT_DECLARE")
        return "declaration", parse_game_declaration(action)

    if CARD_RE.fullmatch(action):
        if actor == WORLD_ACTOR:
            raise ISSProtocolError("WORLD_CANNOT_PLAY_CARD")
        return "cardplay", _card(action)

    raise ISSProtocolError(f"UNKNOWN_ACTION:{action}")


def parse_move_line(line: str) -> WireMove:
    stripped = line.strip()
    if not stripped:
        raise ISSProtocolError("EMPTY_LINE")
    try:
        actor, action = stripped.split(maxsplit=1)
    except ValueError as exc:
        raise ISSProtocolError("MOVE_REQUIRES_ACTOR_AND_ACTION") from exc
    kind, payload = classify_action(actor, action)
    return WireMove(actor=actor, action=action, kind=kind, payload=payload)


def format_move(actor: str, action: str) -> str:
    # Validate exactly the string that will be emitted.
    classify_action(actor, action)
    return f"{actor} {action}"
