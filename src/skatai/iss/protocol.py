from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import socket
from typing import Iterable

ISS_PROTOCOL_SCHEMA = "skatai.v2.iss-wire-primitives.v2"

PLAYER_ACTORS = frozenset({"0", "1", "2"})
WORLD_ACTOR = "w"
VALID_ACTORS = PLAYER_ACTORS | {WORLD_ACTOR}
SUITS = "CSHD"
RANKS = "AKQJT987"
CARD_RE = re.compile(r"^[CSHD][AKQJT987]$")
GAME_TYPE_RE = re.compile(r"^[GCSHDN][OHSZ]*$")
UNKNOWN_CARD = "??"

BIDS = (
    18, 20, 22, 23, 24, 27, 30, 33, 35, 36, 40, 44, 45, 46, 48, 50, 54,
    55, 59, 60, 63, 66, 70, 72, 77, 80, 81, 84, 88, 90, 96, 99, 100, 108,
    110, 117, 120, 121, 126, 130, 132, 135, 140, 143, 144, 150, 153, 154,
    156, 160, 162, 165, 168, 170, 171, 176, 180, 187, 189, 190, 192, 198,
    204, 216, 240, 264,
)
BID_SET = frozenset(BIDS)


class ISSProtocolError(ValueError):
    pass


# Newer callers may use the shorter name; keep one exception identity.
ProtocolError = ISSProtocolError


class ActionKind(str, Enum):
    DEAL = "DEAL"
    BID = "BID"
    ANSWER_YES = "ANSWER_YES"
    PASS = "PASS"
    PICKUP_SKAT = "PICKUP_SKAT"
    WORLD_SKAT = "WORLD_SKAT"
    DECLARATION = "DECLARATION"
    DISCARD_ONLY = "DISCARD_ONLY"
    CARDPLAY = "CARDPLAY"
    RESIGN = "RESIGN"
    SHOW_CARDS = "SHOW_CARDS"
    TIMEOUT = "TIMEOUT"
    LEAVE = "LEAVE"


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


@dataclass(frozen=True)
class ISSMove:
    player: str
    action: str
    kind: ActionKind

    @property
    def seat(self) -> int | None:
        return int(self.player) if self.player in PLAYER_ACTORS else None


def is_card(token: str, *, allow_unknown: bool = False) -> bool:
    return bool(CARD_RE.fullmatch(token)) or (allow_unknown and token == UNKNOWN_CARD)


def _card(token: str, *, allow_unknown: bool = False) -> str:
    if not is_card(token, allow_unknown=allow_unknown):
        raise ISSProtocolError(f"BAD_CARD:{token}")
    return token


def split_cards(text: str, *, allow_unknown: bool = False) -> tuple[str, ...]:
    cards = tuple(text.split("."))
    if not cards:
        raise ISSProtocolError("EMPTY_CARD_LIST")
    return tuple(_card(c, allow_unknown=allow_unknown) for c in cards)


def parse_deal(action: str) -> DealView:
    parts = action.split("|")
    if len(parts) != 4:
        raise ISSProtocolError(f"BAD_DEAL_SECTION_COUNT:{len(parts)}")
    expected = (10, 10, 10, 2)
    groups: list[tuple[str, ...]] = []
    for i, (part, n) in enumerate(zip(parts, expected, strict=True)):
        group = split_cards(part, allow_unknown=True)
        if len(group) != n:
            raise ISSProtocolError(f"BAD_DEAL_GROUP_COUNT:{i}:{len(group)}!={n}")
        groups.append(group)

    known = [c for group in groups for c in group if c != UNKNOWN_CARD]
    if len(known) != len(set(known)):
        raise ISSProtocolError("DUPLICATE_KNOWN_CARD_IN_DEAL")

    return DealView(
        hands=(groups[0], groups[1], groups[2]),
        skat=(groups[3][0], groups[3][1]),
    )


def parse_game_declaration(action: str) -> dict[str, object]:
    parts = action.split(".")
    game_type = parts[0]
    if not GAME_TYPE_RE.fullmatch(game_type):
        raise ISSProtocolError(f"BAD_GAME_TYPE:{game_type}")

    base = game_type[0]
    modifiers = game_type[1:]
    if len(set(modifiers)) != len(modifiers):
        raise ISSProtocolError(f"DUPLICATE_GAME_MODIFIER:{game_type}")
    if base == "N" and any(x in modifiers for x in ("S", "Z")):
        raise ISSProtocolError(f"NULL_WITH_SCHNEIDER_OR_SCHWARZ:{game_type}")
    if base != "N" and "O" in modifiers:
        # ISS semantics: non-null ouvert implies Hand + Schneider + Schwarz,
        # regardless of whether those implied modifiers are written.
        pass
    if any(x in modifiers for x in ("S", "Z")) and "H" not in modifiers and "O" not in modifiers:
        raise ISSProtocolError(f"ANNOUNCEMENT_REQUIRES_HAND:{game_type}")

    cards = tuple(_card(x, allow_unknown=True) for x in parts[1:])
    if cards and len(cards) < 2:
        raise ISSProtocolError("DECLARATION_WITH_SINGLE_CARD")
    if cards and len(cards) not in (2, 10, 12):
        raise ISSProtocolError(f"BAD_DECLARATION_CARD_COUNT:{len(cards)}")
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
        if actor == WORLD_ACTOR:
            raise ISSProtocolError("WORLD_CANNOT_BID")
        value = int(action)
        if value not in BID_SET:
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

    if action == "RE":
        if actor == WORLD_ACTOR:
            raise ISSProtocolError("WORLD_CANNOT_RESIGN")
        return "resign", action

    if action == "SC" or action.startswith("SC."):
        if actor == WORLD_ACTOR:
            raise ISSProtocolError("WORLD_CANNOT_SHOW_CARDS")
        parts = action.split(".")
        cards = tuple(_card(x) for x in parts[1:]) if len(parts) > 1 else ()
        return "show_cards", cards

    if actor == WORLD_ACTOR and (
        action.startswith("TI.") or action.startswith("LE.")
    ):
        parts = action.split(".")
        if len(parts) != 2 or parts[1] not in PLAYER_ACTORS:
            raise ISSProtocolError(f"BAD_WORLD_TERMINAL:{action}")
        if parts[0] == "TI":
            return "timeout", int(parts[1])
        if parts[0] == "LE":
            return "leave", int(parts[1])

    parts = action.split(".")
    if len(parts) == 2 and all(is_card(x, allow_unknown=True) for x in parts):
        if actor == WORLD_ACTOR:
            return "skat_delivery", tuple(parts)
        return "discard_only", tuple(parts)

    # Split declaration/discard mode can append the 10-card ouvert hand.
    if (
        actor != WORLD_ACTOR
        and len(parts) == 12
        and all(is_card(x, allow_unknown=True) for x in parts[:2])
        and all(is_card(x) for x in parts[2:])
    ):
        return "discard_only", tuple(parts)

    # A historical malformed split-ouvert send can be echoed by ISS as
    # <discard-2>.<open-hand-10>.<the-same-open-hand-10>. Accept only that
    # tightly constrained redundant shape so archived/live recovery can
    # normalize it without broadening the action grammar.
    if (
        actor != WORLD_ACTOR
        and len(parts) == 22
        and all(is_card(x) for x in parts)
        and len(set(parts[:2])) == 2
        and len(set(parts[2:12])) == 10
        and len(set(parts[12:22])) == 10
        and set(parts[2:12]) == set(parts[12:22])
        and not (set(parts[:2]) & set(parts[12:22]))
    ):
        return "discard_only", tuple(parts[:12])

    # Official 2019 defender-view code can render a split hidden discard as
    # "<card>.??.??" and append ten ouvert cards. Normalize away the leaked
    # first token: V2 must not treat it as legitimate private information.
    if (
        actor != WORLD_ACTOR
        and len(parts) in (3, 13)
        and is_card(parts[0])
        and parts[1:3] == ["??", "??"]
        and all(is_card(x) for x in parts[3:])
    ):
        return "discard_only", ("??", "??", *parts[3:])

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
    classify_action(actor, action)
    return f"{actor} {action}"


_KIND_MAP = {
    "initial_deal": ActionKind.DEAL,
    "bid": ActionKind.BID,
    "answer_yes": ActionKind.ANSWER_YES,
    "pass": ActionKind.PASS,
    "skat_request": ActionKind.PICKUP_SKAT,
    "skat_delivery": ActionKind.WORLD_SKAT,
    "declaration": ActionKind.DECLARATION,
    "discard_only": ActionKind.DISCARD_ONLY,
    "cardplay": ActionKind.CARDPLAY,
    "resign": ActionKind.RESIGN,
    "show_cards": ActionKind.SHOW_CARDS,
    "timeout": ActionKind.TIMEOUT,
    "leave": ActionKind.LEAVE,
}


def parse_move(line: str) -> ISSMove:
    wire = parse_move_line(line)
    return ISSMove(
        player=wire.actor,
        action=wire.action,
        kind=_KIND_MAP[wire.kind],
    )


def parse_transcript(lines: Iterable[str]) -> tuple[ISSMove, ...]:
    return tuple(parse_move(line) for line in lines if line.strip())


class ISSLineConnection:
    """Minimal line-oriented ISS transport.

    This owns only TCP/login/framing. It does not automate table lifecycle or
    game decisions. Passwords are used for the handshake and never retained.
    """

    def __init__(self, sock: socket.socket, *, client_id: str) -> None:
        self._sock = sock
        self.client_id = client_id
        self._reader = sock.makefile("r", encoding="utf-8", newline="\n")
        self._writer = sock.makefile("w", encoding="utf-8", newline="\n")

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        client_id: str,
        password: str,
        *,
        timeout_s: float = 10.0,
    ) -> "ISSLineConnection":
        if not host or not client_id or not password:
            raise ValueError("host, client_id and password are required")
        sock = socket.create_connection((host, int(port)), timeout=timeout_s)
        sock.settimeout(timeout_s)
        reader = sock.makefile("r", encoding="utf-8", newline="\n")
        writer = sock.makefile("w", encoding="utf-8", newline="\n")
        try:
            writer.write(client_id + "\n")
            writer.flush()
            challenge = reader.readline()
            if challenge == "":
                raise ConnectionError("ISS_CLOSED_BEFORE_PASSWORD_CHALLENGE")
            if challenge.rstrip("\r\n") != "password:":
                raise ConnectionError("ISS_LOGIN_EXPECTED_PASSWORD_CHALLENGE")

            writer.write(password + "\n")
            writer.flush()
            welcome = reader.readline()
            if welcome == "":
                raise ConnectionError("ISS_CLOSED_BEFORE_WELCOME")
            welcome = welcome.rstrip("\r\n")
            if not welcome.startswith("Welcome"):
                raise ConnectionError("ISS_LOGIN_EXPECTED_WELCOME")

            words = welcome.split()
            effective_id = words[1] if len(words) > 1 else client_id

            obj = cls.__new__(cls)
            obj._sock = sock
            obj._reader = reader
            obj._writer = writer
            obj.client_id = effective_id
            return obj
        except BaseException:
            try:
                writer.close()
            finally:
                try:
                    reader.close()
                finally:
                    sock.close()
            raise

    def send_line(self, line: str) -> None:
        if "\n" in line or "\r" in line:
            raise ValueError("ISS line must not contain newline characters")
        self._writer.write(line + "\n")
        self._writer.flush()

    def read_line(self) -> str:
        line = self._reader.readline()
        if line == "":
            raise EOFError("ISS_CONNECTION_CLOSED")
        return line.rstrip("\r\n")

    def close(self) -> None:
        try:
            self._writer.close()
        finally:
            try:
                self._reader.close()
            finally:
                self._sock.close()

    def __enter__(self) -> "ISSLineConnection":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
