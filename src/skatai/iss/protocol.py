from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import socket
from typing import Iterable

ISS_PROTOCOL_SCHEMA = "skatai.v2.iss-move-protocol.v1"

BIDS = (
    18, 20, 22, 23, 24, 27, 30, 33, 35, 36, 40, 44, 45, 46, 48, 50, 54,
    55, 59, 60, 63, 66, 70, 72, 77, 80, 81, 84, 88, 90, 96, 99, 100, 108,
    110, 117, 120, 121, 126, 130, 132, 135, 140, 143, 144, 150, 153, 154,
    156, 160, 162, 165, 168, 170, 171, 176, 180, 187, 189, 190, 192, 198,
    204, 216, 240, 264,
)
BID_SET = frozenset(BIDS)

CARD_RE = re.compile(r"^[CSHD][AKQJT987]$")
UNKNOWN_CARD = "??"
GAME_TYPE_RE = re.compile(r"^(?:G|C|S|H|D|N)(?:O|H|S|Z)*$")
PLAYER_TOKENS = frozenset({"w", "0", "1", "2"})


class ProtocolError(ValueError):
    pass


class ActionKind(str, Enum):
    DEAL = "DEAL"
    BID = "BID"
    ANSWER_YES = "ANSWER_YES"
    PASS = "PASS"
    PICKUP_SKAT = "PICKUP_SKAT"
    WORLD_SKAT = "WORLD_SKAT"
    DECLARATION = "DECLARATION"
    CARDPLAY = "CARDPLAY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ISSMove:
    player: str
    action: str
    kind: ActionKind

    @property
    def seat(self) -> int | None:
        return int(self.player) if self.player in {"0", "1", "2"} else None


def is_card(token: str, *, allow_unknown: bool = False) -> bool:
    return bool(CARD_RE.fullmatch(token)) or (allow_unknown and token == UNKNOWN_CARD)


def split_cards(text: str, *, allow_unknown: bool = False) -> tuple[str, ...]:
    cards = tuple(text.split("."))
    if not cards or any(not is_card(c, allow_unknown=allow_unknown) for c in cards):
        raise ProtocolError(f"INVALID_CARD_LIST:{text}")
    return cards


def parse_deal(action: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    groups = action.split("|")
    if len(groups) != 4:
        raise ProtocolError(f"BAD_DEAL_GROUP_COUNT:{len(groups)}")
    expected = (10, 10, 10, 2)
    out = []
    for group, n in zip(groups, expected, strict=True):
        cards = split_cards(group, allow_unknown=True)
        if len(cards) != n:
            raise ProtocolError(f"BAD_DEAL_CARD_COUNT:{len(cards)}!={n}")
        out.append(cards)

    known = [c for group in out for c in group if c != UNKNOWN_CARD]
    if len(known) != len(set(known)):
        raise ProtocolError("DUPLICATE_KNOWN_CARD_IN_DEAL")
    return out[0], out[1], out[2], out[3]


def _declaration_parts(action: str) -> tuple[str, tuple[str, ...]] | None:
    parts = action.split(".")
    if not parts or not GAME_TYPE_RE.fullmatch(parts[0]):
        return None
    cards = tuple(parts[1:])
    if any(not is_card(c) for c in cards):
        raise ProtocolError(f"INVALID_DECLARATION_CARDS:{action}")
    return parts[0], cards


def classify_action(player: str, action: str) -> ActionKind:
    if player not in PLAYER_TOKENS:
        raise ProtocolError(f"INVALID_PLAYER_TOKEN:{player}")
    if not action:
        raise ProtocolError("EMPTY_ACTION")

    if player == "w":
        if "|" in action:
            parse_deal(action)
            return ActionKind.DEAL
        cards = split_cards(action)
        if len(cards) == 2:
            return ActionKind.WORLD_SKAT
        return ActionKind.UNKNOWN

    if action == "y":
        return ActionKind.ANSWER_YES
    if action == "p":
        return ActionKind.PASS
    if action == "s":
        return ActionKind.PICKUP_SKAT
    if action.isdigit():
        value = int(action)
        if value not in BID_SET:
            raise ProtocolError(f"INVALID_BID:{value}")
        return ActionKind.BID
    if is_card(action):
        return ActionKind.CARDPLAY
    if _declaration_parts(action) is not None:
        return ActionKind.DECLARATION
    return ActionKind.UNKNOWN


def parse_move(line: str) -> ISSMove:
    line = line.strip()
    if not line:
        raise ProtocolError("EMPTY_MOVE_LINE")
    try:
        player, action = line.split(maxsplit=1)
    except ValueError as exc:
        raise ProtocolError(f"BAD_MOVE_LINE:{line}") from exc
    return ISSMove(player=player, action=action, kind=classify_action(player, action))


class ISSLineConnection:
    """Minimal line-oriented ISS transport.

    This layer deliberately knows nothing about table automation. It only owns
    the TCP connection, login handshake, UTF-8 line framing, and secret-safe
    lifecycle. Higher-level service/table parsing belongs in separate code.
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

            # Reuse the already-created streams without ever storing password.
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


def parse_transcript(lines: Iterable[str]) -> tuple[ISSMove, ...]:
    return tuple(parse_move(line) for line in lines if line.strip())
