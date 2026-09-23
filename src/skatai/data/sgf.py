from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from skatai.game.bidding import native_pairs, replay

SGF_SCHEMA = "skatai.v2.sgf-parse.v1"
SUITS = "CSHD"
RANKS = "789TJQKA"
VALID_CARDS = frozenset(s + r for s in SUITS for r in RANKS)
CONTRACT_RE = re.compile(
    r"^(NO|[CSHDGN])([HOSZ]*)(?:\.(.*))?$"
)


class SGFParseError(ValueError):
    pass


def parse_contract_token(token: str) -> tuple[str, str, list[str]] | None:
    """Return (base, modifiers, dot-separated extras) for a declaration token.

    Modifier letters are intentionally preserved as protocol data. Their full
    scoring semantics are validated separately rather than guessed here.
    """
    m = CONTRACT_RE.fullmatch(token)
    if not m:
        return None
    base = m.group(1)
    modifiers = m.group(2) or ""
    extras = m.group(3).split(".") if m.group(3) else []
    return base, modifiers, extras


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


def semantic_identity(obj: dict[str, Any]) -> str:
    """Source-agnostic game identity.

    Provenance fields (source, source record id, ratings) and derived scoring
    fields are deliberately excluded. The identity binds the observable game
    transcript itself so mirrored archive records deduplicate cleanly.
    """
    bidding_actions = [[actor, action] for actor, action in native_pairs(obj["bidding_history"])]
    if obj.get("all_pass"):
        core = {
            "players": obj["players"],
            "initial_hands": obj["initial_hands"],
            "skat_initial": obj["skat_initial"],
            "bidding_actions": bidding_actions,
            "all_pass": True,
        }
    else:
        keys = (
            "players",
            "initial_hands",
            "skat_initial",
            "declarer",
            "bid_level",
            "announcement",
            "game_type",
            "is_hand",
            "is_ouvert",
            "discards",
            "plays",
        )
        core = {k: obj[k] for k in keys}
        core["bidding_actions"] = bidding_actions
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _category(card: str, game_type: str) -> str:
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
    return suit


def _trick_strength(card: str, game_type: str, lead_category: str) -> tuple[int, int]:
    category = _category(card, game_type)
    suit, rank = card[0], card[1]

    if game_type == "NULL":
        null_order = {"7": 0, "8": 1, "9": 2, "T": 3, "J": 4, "Q": 5, "K": 6, "A": 7}
        return (1 if category == lead_category else 0, null_order[rank])

    if category == "TRUMP":
        if rank == "J":
            jack_order = {"D": 11, "H": 12, "S": 13, "C": 14}
            return (3, jack_order[suit])
        suit_order = {"7": 0, "8": 1, "9": 2, "Q": 3, "K": 4, "T": 5, "A": 6}
        return (3, suit_order[rank])

    normal_order = {"7": 0, "8": 1, "9": 2, "Q": 3, "K": 4, "T": 5, "A": 6}
    if rank == "J":
        raise SGFParseError("NONTRUMP_JACK_IMPOSSIBLE")
    return (2 if category == lead_category else 1, normal_order[rank])


def _trick_winner(trick: list[list[Any]], game_type: str) -> int:
    lead_category = _category(trick[0][1], game_type)
    best_actor = trick[0][0]
    best = _trick_strength(trick[0][1], game_type, lead_category)
    for actor, card in trick[1:]:
        strength = _trick_strength(card, game_type, lead_category)
        if strength > best:
            best = strength
            best_actor = actor
    return int(best_actor)


def _validate_play_legality(
    hands: list[list[str]],
    skat: list[str],
    declarer: int,
    is_hand: bool,
    discards: list[str] | None,
    plays: list[list[Any]],
    game_type: str,
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

    leader = 0
    seen: set[str] = set()
    trick: list[list[Any]] = []

    for ordinal, (actor, card) in enumerate(plays):
        expected_actor = (leader + len(trick)) % 3
        if actor != expected_actor:
            raise SGFParseError(
                f"TURN_ORDER:{ordinal}:{actor}!={expected_actor}"
            )
        if card in seen:
            raise SGFParseError(f"CARD_PLAYED_TWICE:{card}")
        if card not in current[actor]:
            raise SGFParseError(f"PLAY_NOT_OWNED:{actor}:{card}")

        if trick:
            lead_category = _category(trick[0][1], game_type)
            played_category = _category(card, game_type)
            if played_category != lead_category:
                if any(_category(c, game_type) == lead_category for c in current[actor]):
                    raise SGFParseError(
                        f"FOLLOW_VIOLATION:{actor}:{card}:must_follow={lead_category}"
                    )

        current[actor].remove(card)
        seen.add(card)
        trick.append([actor, card])

        if len(trick) == 3:
            leader = _trick_winner(trick, game_type)
            trick = []


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
    declaration = None
    for i, token in enumerate(tail):
        parsed = parse_contract_token(token)
        if parsed is not None:
            decl_i = i
            declaration = parsed
            break

    common = {
        "schema": SGF_SCHEMA,
        "source": source,
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "game_id": int(tags["ID"]) if tags.get("ID", "").isdigit() else None,
        "date": (tags.get("DT") or "")[:10],
        "timestamp_utc": tags.get("DT") or "",
        "players": [tags.get("P0", ""), tags.get("P1", ""), tags.get("P2", "")],
        "ratings": [_rating(tags.get("R0")), _rating(tags.get("R1")), _rating(tags.get("R2"))],
        "initial_hands": initial_hands,
        "skat_initial": skat,
    }

    if decl_i is None:
        # Preserve only explicit decisions. Complete all-pass auctions are
        # useful bidding evidence; abbreviated/aborted prefixes stay quarantined.
        try:
            widx = tail.index("w")
        except ValueError:
            widx = len(tail)
        bidding_prefix = tail[:widx]
        br = replay(bidding_prefix)
        if br.ok and br.all_pass:
            out = {
                **common,
                "classification": "VERIFIED_ALL_PASS",
                "bidding_history": bidding_prefix,
                "all_pass": True,
            }
            out["semantic_sha256"] = semantic_identity(out)
            return out

        common.update(
            {
                "classification": "QUARANTINED_NO_CONTRACT",
                "raw_bidding_prefix": bidding_prefix,
                "quarantine_reason": br.error or "NO_DECLARED_CONTRACT",
                "semantic_sha256": None,
            }
        )
        return common

    assert declaration is not None
    announcement = tail[decl_i]
    contract_base, contract_modifiers, extras = declaration

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
        is_hand = True

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
    }[contract_base]
    is_ouvert = contract_base == "NO" or "O" in contract_modifiers

    suffix = tail[decl_i + 1 :]
    plays: list[list[Any]] = []
    i = 0
    while i + 1 < len(suffix) and suffix[i] in {"0", "1", "2"}:
        card = suffix[i + 1]
        if card not in VALID_CARDS:
            break
        plays.append([int(suffix[i]), card])
        i += 2

    _validate_play_legality(
        initial_hands, skat, declarer, is_hand, discards, plays, game_type
    )

    rf = _result_fields(tags.get("R", ""))
    out = {
        **common,
        "classification": "PARSED_PLAYED_GAME",
        "bidding_history": bidding_history,
        "declarer": declarer,
        "bid_level": br.winning_bid,
        "announcement": announcement,
        "contract_base": contract_base,
        "contract_modifiers": contract_modifiers,
        "announcement_extras": extras,
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
    out["semantic_sha256"] = semantic_identity(out)
    return out
