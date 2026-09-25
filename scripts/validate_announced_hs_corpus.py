"""Validate a bounded, train-split-only historical Schneider-announced oracle.

This reads a registered Legacy canonical source as rules evidence. It never
selects frozen validation/test/external-holdout records or authorizes training.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from skatai.data.bidding import split_for_game
from skatai.data.sgf import parse_contract_token
from skatai.game.rules import card_points, legal_cards, replay_tricks
from skatai.selfplay.cardplay import DECK
from skatai.selfplay.scoring import score_basic_game

SCHEMA = "skatai.v2.evidence.announced-hs-train-oracle.v1"


def check_record(record: dict) -> dict:
    """Reconstruct a complete hand game before comparing the source result."""
    identity = str(record["semantic_sha256"])
    hands = [list(hand) for hand in record["initial_hands"]]
    skat = list(record["skat_initial"])
    declarer = int(record["declarer"])
    if (not record["is_hand"] or record["discards"] is not None
            or len(hands) != 3 or any(len(hand) != 10 for hand in hands)
            or len(skat) != 2 or set((*skat, *(c for h in hands for c in h))) != set(DECK)):
        raise ValueError("BAD_HAND_DEAL_OR_SKAT")
    if len(record["plays"]) != 30:
        raise ValueError("INCOMPLETE_PLAY")
    current = []
    for actor, card in record["plays"]:
        if card not in legal_cards(hands[actor], current, record["game_type"]):
            raise ValueError("ILLEGAL_CARD_OR_FOLLOW")
        hands[actor].remove(card)
        current.append((actor, card))
        if len(current) == 3:
            current = []
    if any(hands) or current:
        raise ValueError("HAND_NOT_EXHAUSTED")
    replay = replay_tricks(
        record["plays"], game_type=record["game_type"], declarer=declarer,
    )
    points = replay["declarer_trick_points"] + sum(card_points(c) for c in skat)
    if (points != int(record["card_points"])
            or points + replay["defender_trick_points"] != 120):
        raise ValueError("REPORTED_POINT_MISMATCH")
    score = score_basic_game(
        contract=record["announcement"], winning_bid=int(record["bid_level"]),
        declarer_cards=(*record["initial_hands"][declarer], *skat),
        declarer_points=points,
        declarer_tricks=sum(t["winner"] == declarer for t in replay["completed_tricks"]),
        research_announced_schneider=True,
    )
    return {
        "identity": identity, "contract": record["announcement"],
        "expected_value": int(record["game_value"]),
        "computed_value": score.signed_game_value,
        "expected_matadors": int(record["matadors"]),
        "computed_matadors": score.matadors,
        "status": "MATCH" if (
            score.signed_game_value == int(record["game_value"])
            and score.matadors == int(record["matadors"])
            and score.won == bool(record["won"])
        ) else "MISMATCH",
    }


def validate_window(source: Path, *, start: int, end: int, plan_sha256: str,
                    registered_source_sha256: str) -> dict:
    if not 0 <= start < end <= 200_000:
        raise ValueError("WINDOW_OUT_OF_BOUNDS")
    prefix = hashlib.sha256()
    counts = Counter()
    results = []
    with source.open("rb") as stream:
        for ordinal in range(end):
            raw = stream.readline()
            if not raw:
                raise ValueError(f"SOURCE_SHORTER_THAN_WINDOW:{ordinal}")
            prefix.update(raw)
            if ordinal < start:
                continue
            counts["window_rows"] += 1
            record = json.loads(raw)
            if split_for_game(record) != "train":
                counts["nontrain_excluded"] += 1
                continue
            counts["train_rows"] += 1
            if (record.get("play_count") != 30
                    or not record.get("cardplay_usable")):
                continue
            parsed = parse_contract_token(str(record.get("announcement", "")))
            if parsed is None or parsed[1] != "HS":
                continue
            counts["selected_hs"] += 1
            try:
                outcome = check_record(record)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                outcome = {"identity": str(record.get("semantic_sha256")),
                           "status": "INVALID", "reason": str(exc)}
            results.append(outcome)
            counts[outcome["status"].lower()] += 1
    return {
        "schema": SCHEMA,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "registered_source_sha256": registered_source_sha256,
        "source_prefix_through_end_sha256": prefix.hexdigest(),
        "plan_sha256": plan_sha256,
        "window_zero_based_lines": [start, end],
        "counts": dict(counts),
        "results": results,
        "status": "PASS" if (counts["selected_hs"] > 0
                            and counts["match"] == counts["selected_hs"]) else "FAIL",
        "restriction": "Train-split rules oracle only; no policy training, strength evaluation, or frozen R9 use.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--start-line", type=int, required=True)
    parser.add_argument("--end-line", type=int, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--registered-source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_window(
        args.source, start=args.start_line, end=args.end_line,
        plan_sha256=args.plan_sha256,
        registered_source_sha256=args.registered_source_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=args.output.name + ".", dir=args.output.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, args.output)
    print(json.dumps({"status": result["status"], "counts": result["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
