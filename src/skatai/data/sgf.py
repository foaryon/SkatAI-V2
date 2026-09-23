from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from skatai.game.bidding import replay

SGF_SCHEMA = "skatai.v2.sgf-parse.v1"
SUITS = "CSHD"
RANKS = "789TJQKA"
VALID_CARDS = frozenset(s + r for s in SUITS for r in RANKS)
CONTRACT_RE = re.compile(
    r"^(C|S|H|D|G|N|NO|CH|SH|HH|DH|GH|NH|NOH)(?:\.(.*))?$"
)


class SGFParseError(ValueError):
    pass


def parse_properties(line: str) -> dict[str, str]:
    """Parse one compact SGF node, preserving escaped bracket characters."""
    out: dict[str, str] = {}
    i = 0
    n = len(line)
    while i < n:
        if not ("A" <= line[i] <= "Z"):
            i += 1
            continue
        j = i
        while j < n and (line[j].isupper() or line[j].isdigit()):
            j += 1
        key = line[i:j]
        if j >= n or line[j] != "[":
            i = j
            continue
        j += 1
        chars: list[str] = []
        escaped = False
        while j < n:
            ch = line[j]
            if escaped:
                chars.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "]":
                break
            else:
                chars.append(ch)
            j += 1
        if j >= n:
            raise SGFParseError(f"UNCLOSED_PROPERTY:{key}")
        out[key] = "".join(chars)
        i = j + 1
    return out


def _rating(value: str | None) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _result_fields(value: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in value.split():
        if ":" in token:
            k, v = token.split(":", 1)
            fields[k] = v
        elif token in {"win", "loss"}:
            fields["outcome"] = token
    return fields


def _int_field(fields: dict[str, str], name: str) -> int | None:
    try:
        return int(fields[name])
    except (KeyError, ValueError):
        return None


def _semantic_identity(obj: dict[str, Any]) -> str:
    core = {
        k: obj[k]
        for k in (
            "source",
            "game_id",
            "date",
            "players",
            "ratings",
            "initial_hands",
            "skat_initial",
            "bidding_history",
            "declarer",
            "bid_level",
            "announcement",
            "game_type",
            "is_hand",
            "is_ouvert",
            "discards",
            "plays",
            "won",
            "game_value",
            "card_points",
        )
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_play_ownership(
    hands: list[list[str]],
    skat: list[str],
    declarer: int,
    is_hand: bool,
    discards: list[str] | None,
    plays: list[list[Any]],
) -> None:
    current = [set(h) for h in hands]
    if not is_hand:
        current[declarer].update(skat)
        if discards is None or len(discards) != 2:
            raise SGFParseError("PICKUP_GAME_REQUIRES_TWO_DISCARDS")
        for card in discards:
            if card not in current[declarer]:
                raise SGFParseError(f"DISCARD_NOT_OWNED:{card}")
            current[declarer].remove(card)

    seen: set[str] = set()
    for actor, card in plays:
        if card in seen:
            raise SGFParseError(f"CARD_PLAYED_TWICE:{card}")
        if card not in current[actor]:
            raise SGFParseError(f"PLAY_NOT_OWNED:{actor}:{card}")
        current[actor].remove(card)
        seen.add(card)


def parse_sgf_line(source: str, raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw_bytes = raw.strip()
        try:
            line = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SGFParseError("BAD_UTF8") from exc
    else:
        line = raw.strip()
        raw_bytes = line.encode("utf-8")

    tags = parse_properties(line)
    if tags.get("GM") != "Skat":
        raise SGFParseError("NOT_SKAT")

    mv = tags.get("MV")
    if not mv:
        raise SGFParseError("MISSING_MV")
    tokens = mv.split()
    if len(tokens) < 2 or tokens[0] != "w":
        raise SGFParseError("BAD_MV_PREFIX")

    deck = tokens[1].split(".")
    if len(deck) != 32:
        raise SGFParseError(f"BAD_DEAL_COUNT:{len(deck)}")
    if len(set(deck)) != 32 or any(card not in VALID_CARDS for card in deck):
        raise SGFParseError("INVALID_DEAL")

    initial_hands = [deck[0:10], deck[10:20], deck[20:30]]
    skat = deck[30:32]
    tail = tokens[2:]

    decl_i = None
    decl_match = None
    for i, token in enumerate(tail):
        m = CONTRACT_RE.fullmatch(token)
        if m:
            decl_i = i
            decl_match = m
            break

    common = {
        "schema": SGF_SCHEMA,
        "source": source,
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "game_id": int(tags["ID"]) if tags.get("ID", "").isdigit() else None,
        "date": (tags.get("DT") or "")[:10],
        "players": [tags.get("P0", ""), tags.get("P1", ""), tags.get("P2", "")],
        "ratings": [_rating(tags.get("R0")), _rating(tags.get("R1")), _rating(tags.get("R2"))],
        "initial_hands": initial_hands,
        "skat_initial": skat,
    }

    if decl_i is None:
        # ISS/SkatGame public archives contain many passed-in/aborted records
        # where the raw log does not expose a complete three-player bidding
        # sequence. Preserve exactly what is present and quarantine rather
        # than inventing implicit actions.
        try:
            widx = tail.index("w")
        except ValueError:
            widx = len(tail)
        common.update(
            {
                "classification": "QUARANTINED_NO_CONTRACT",
                "raw_bidding_prefix": tail[:widx],
                "semantic_sha256": None,
            }
        )
        return common

    assert decl_match is not None
    announcement = tail[decl_i]
    contract = decl_match.group(1)
    extras = decl_match.group(2).split(".") if decl_match.group(2) else []

    pre = tail[:decl_i]
    if not pre or pre[-1] not in {"0", "1", "2"}:
        raise SGFParseError("MISSING_DECLARER_MARKER")
    declarer = int(pre[-1])

    # Pickup syntax: ... <declarer> s w <original-skat> <declarer> <announcement>
    pickup = False
    pickup_marker_i = None
    for i in range(len(pre) - 1):
        if pre[i] in {"0", "1", "2"} and i + 1 < len(pre) and pre[i + 1] == "s":
            pickup = True
            pickup_marker_i = i
            break

    if pickup:
        assert pickup_marker_i is not None
        bidding_history = pre[: pickup_marker_i + 2]
        after = pre[pickup_marker_i + 2 :]
        if len(after) != 3 or after[0] != "w" or after[2] != str(declarer):
            raise SGFParseError(f"BAD_PICKUP_MARKER:{after}")
        if after[1].split(".") != skat:
            raise SGFParseError("SKAT_MARKER_MISMATCH")
        bidding_for_replay = pre[:pickup_marker_i]
        discards = extras[:2] if len(extras) >= 2 else None
        is_hand = False
    else:
        bidding_history = pre[:-1]
        bidding_for_replay = pre[:-1]
        discards = None
        is_hand = contract.endswith("H")

    br = replay(bidding_for_replay)
    if not br.ok:
        raise SGFParseError(f"BIDDING_REPLAY:{br.error}")
    if br.winner != declarer:
        raise SGFParseError(f"DECLARER_MISMATCH:{br.winner}!={declarer}")

    game_type = {
        "C": "CLUBS",
        "S": "SPADES",
        "H": "HEARTS",
        "D": "DIAMONDS",
        "G": "GRAND",
        "N": "NULL",
        "NO": "NULL",
    }[contract[:2] if contract.startswith("NO") else contract[0]]
    is_ouvert = "O" in contract

    suffix = tail[decl_i + 1 :]
    plays: list[list[Any]] = []
    i = 0
    while i + 1 < len(suffix) and suffix[i] in {"0", "1", "2"}:
        card = suffix[i + 1]
        if card not in VALID_CARDS:
            break
        plays.append([int(suffix[i]), card])
        i += 2

    _validate_play_ownership(
        initial_hands, skat, declarer, is_hand, discards, plays
    )

    rf = _result_fields(tags.get("R", ""))
    out = {
        **common,
        "classification": "PARSED_PLAYED_GAME",
        "bidding_history": bidding_history,
        "declarer": declarer,
        "bid_level": br.winning_bid,
        "announcement": announcement,
        "game_type": game_type,
        "is_hand": is_hand,
        "is_ouvert": is_ouvert,
        "discards": discards,
        "plays": plays,
        "play_count": len(plays),
        "play_complete": len(plays) == 30,
        "won": rf.get("outcome") == "win",
        "game_value": _int_field(rf, "v"),
        "card_points": _int_field(rf, "p"),
        "matadors": _int_field(rf, "m"),
    }
    out["semantic_sha256"] = _semantic_identity(out)
    return out
