"""Bounded train-only research oracle for hand Schwarz and ouvert scoring.

This compares a preregistered candidate rule with source results. It does not
enable these modes in product scoring or qualify a policy dataset.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from skatai.data.bidding import split_for_game
from skatai.data.sgf import parse_contract_token
from skatai.game.rules import card_points, legal_cards, replay_tricks
from skatai.selfplay.cardplay import DECK

SCHEMA = "skatai.v2.announced-hz-ouvert-train-oracle.v1"
BASE = {"C": 12, "S": 11, "H": 10, "D": 9, "G": 24}
GAME_TYPES = {"C": "CLUBS", "S": "SPADES", "H": "HEARTS", "D": "DIAMONDS", "G": "GRAND"}
JACKS = ("CJ", "SJ", "HJ", "DJ")


def candidate_value(*, base: str, mode: str, cards: set[str],
                    bid: int, tricks: int) -> tuple[int, int, int, bool]:
    """Research hypothesis: hand + announced Schneider/Schwarz + ouvert."""
    if base not in BASE or mode not in {"HZ", "HO", "O"}:
        raise ValueError("UNSUPPORTED_RESEARCH_MODE")
    if len(cards) != 12 or not cards.issubset(DECK) or bid < 18 or not 0 <= tricks <= 10:
        raise ValueError("INVALID_RESEARCH_INPUT")
    trumps = JACKS if base == "G" else JACKS + tuple(base + rank for rank in "ATKQ987")
    with_top = trumps[0] in cards
    count = 0
    for card in trumps:
        if (card in cards) != with_top:
            break
        count += 1
    matadors = count if with_top else -count
    level = abs(matadors) + 6 + int(mode in {"HO", "O"})
    natural = BASE[base] * level
    overbid = bid > natural
    signed = (
        -2 * BASE[base] * ((bid + BASE[base] - 1) // BASE[base])
        if overbid else natural if tricks == 10 else -2 * natural
    )
    return signed, matadors, natural, tricks == 10 and not overbid


def check_complete_record(record: dict, *, base: str, mode: str) -> dict:
    identity = str(record["semantic_sha256"])
    hands = [list(hand) for hand in record["initial_hands"]]
    skat = tuple(record["skat_initial"])
    declarer = int(record["declarer"])
    deal = (*skat, *(card for hand in hands for card in hand))
    if (not record["is_hand"] or record["discards"] is not None
            or record["game_type"] != GAME_TYPES[base]
            or declarer not in (0, 1, 2) or len(hands) != 3
            or any(len(hand) != 10 for hand in hands) or len(skat) != 2
            or len(deal) != 32 or set(deal) != set(DECK)):
        raise ValueError("BAD_HAND_DEAL")
    current = []
    for actor, card in record["plays"]:
        if actor not in (0, 1, 2) or card not in legal_cards(
            hands[actor], current, record["game_type"]
        ):
            raise ValueError("ILLEGAL_SOURCE_PLAY")
        hands[actor].remove(card)
        current.append((actor, card))
        if len(current) == 3:
            current = []
    if current or any(hands):
        raise ValueError("INCOMPLETE_SOURCE_PLAY")
    replay = replay_tricks(
        record["plays"], game_type=record["game_type"], declarer=declarer,
    )
    points = replay["declarer_trick_points"] + sum(card_points(c) for c in skat)
    if points != int(record["card_points"]) or points + replay["defender_trick_points"] != 120:
        raise ValueError("SOURCE_POINT_MISMATCH")
    tricks = sum(x["winner"] == declarer for x in replay["completed_tricks"])
    signed, matadors, natural, won = candidate_value(
        base=base, mode=mode,
        cards=set((*record["initial_hands"][declarer], *skat)),
        bid=int(record["bid_level"]), tricks=tricks,
    )
    if not isinstance(record["won"], bool):
        raise ValueError("SOURCE_RESULT_FLAG_INVALID")
    expected = (int(record["game_value"]), int(record["matadors"]), record["won"])
    computed = (signed, matadors, won)
    return {
        "identity": identity, "base": base, "mode": mode,
        "expected": expected, "computed": computed,
        "natural_value": natural, "declarer_tricks": tricks,
        "status": "MATCH" if expected == computed else "MISMATCH",
    }


def evaluate_prefix(source: Path, *, end: int, expected_prefix_sha256: str,
                    plan_sha256: str) -> dict:
    if end != 400_000:
        raise ValueError("UNREGISTERED_WINDOW")
    digest = hashlib.sha256()
    counts = Counter()
    results = []
    with source.open("rb") as stream:
        for ordinal in range(end):
            raw = stream.readline()
            if not raw:
                raise ValueError(f"SOURCE_SHORTER_THAN_WINDOW:{ordinal}")
            digest.update(raw)
            counts["rows"] += 1
            record = json.loads(raw)
            if split_for_game(record) != "train":
                counts["nontrain_excluded"] += 1
                continue
            counts["train"] += 1
            parsed = parse_contract_token(str(record.get("announcement") or ""))
            if (not parsed or parsed[0] not in BASE or parsed[1] not in {"HZ", "HO", "O"}
                    or not record.get("is_hand")):
                continue
            counts["selected"] += 1
            if record.get("play_count") != 30 or not record.get("cardplay_usable"):
                counts["incomplete"] += 1
                continue
            try:
                result = check_complete_record(record, base=parsed[0], mode=parsed[1])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                result = {"identity": str(record.get("semantic_sha256")),
                          "base": parsed[0], "mode": parsed[1],
                          "status": "INVALID", "reason": str(exc)}
            results.append(result)
            counts[result["status"].lower()] += 1
    if digest.hexdigest() != expected_prefix_sha256:
        raise ValueError("REGISTERED_PREFIX_HASH_MISMATCH")
    return {
        "schema": SCHEMA,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_prefix_sha256": digest.hexdigest(),
        "window_zero_based_lines": [0, end],
        "plan_sha256": plan_sha256,
        "counts": dict(counts),
        "results": results,
        "status": "MATCH_OBSERVED_CELLS" if (
            counts["match"] > 0 and not counts["mismatch"] and not counts["invalid"]
        ) else "INCONCLUSIVE_OR_MISMATCH",
        "restriction": "Train-only rules research; no product scorer enablement, training, heldout or strength claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if str(args.source) != plan["source"]:
        raise ValueError("PLAN_SOURCE_MISMATCH")
    outcome = evaluate_prefix(
        args.source, end=400_000,
        expected_prefix_sha256=plan["registered_prefix_400k_sha256"],
        plan_sha256=hashlib.sha256(args.plan.read_bytes()).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=args.output.name + ".", dir=args.output.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(outcome, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, args.output)
    print(json.dumps({"counts": outcome["counts"], "status": outcome["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
