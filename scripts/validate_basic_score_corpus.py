"""Compare the bounded basic scorer with immutable parsed public results.

Reads a single canonical JSONL artifact from stdin. This is scoring-rules
validation only; records and result targets must stay out of policy training.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from skatai.data.bidding import split_for_game
from skatai.game.rules import card_points, legal_cards, replay_tricks
from skatai.selfplay.cardplay import DECK
from skatai.selfplay.scoring import score_basic_game

SCHEMA = "skatai.v2.evidence.basic-score-corpus-oracle.v4"


def _check_play_legality(record: dict) -> None:
    """Check the source game independently of the SGF parser's legal-play flag."""
    hands = [list(hand) for hand in record["initial_hands"]]
    skat = tuple(record["skat_initial"])
    declarer = int(record["declarer"])
    if (declarer not in (0, 1, 2) or len(hands) != 3
            or any(len(hand) != 10 for hand in hands) or len(skat) != 2
            or len(set((*skat, *(card for hand in hands for card in hand)))) != 32
            or set((*skat, *(card for hand in hands for card in hand))) != set(DECK)):
        raise ValueError("INVALID_SOURCE_DEAL")
    if record["is_hand"]:
        if record["discards"] is not None:
            raise ValueError("HAND_WITH_DISCARDS")
    else:
        discards = tuple(record["discards"] or ())
        if len(discards) != 2 or len(set(discards)) != 2:
            raise ValueError("INVALID_PICKUP_DISCARDS")
        hands[declarer].extend(skat)
        for card in discards:
            if card not in hands[declarer]:
                raise ValueError("UNOWNED_PICKUP_DISCARD")
            hands[declarer].remove(card)
    current = []
    for actor, card in record["plays"]:
        if actor not in (0, 1, 2) or card not in legal_cards(
            hands[actor], current, record["game_type"]
        ):
            raise ValueError("ILLEGAL_OR_UNOWNED_SOURCE_PLAY")
        hands[actor].remove(card)
        current.append((actor, card))
        if len(current) == 3:
            current = []
    if current or any(hands):
        raise ValueError("INCOMPLETE_SOURCE_PLAY")


def compare_played_record(record: dict) -> dict:
    identity = str(record["semantic_sha256"])
    if not identity or record.get("play_count") != 30:
        return {"identity": identity, "status": "INCOMPLETE"}
    try:
        _check_play_legality(record)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return {"identity": identity, "status": "INVALID", "reason": str(exc)}
    contract = str(record["contract_base"]) + str(record["contract_modifiers"])
    declarer = int(record["declarer"])
    try:
        replay = replay_tricks(
            record["plays"], game_type=str(record["game_type"]), declarer=declarer,
        )
    except (TypeError, ValueError) as exc:
        return {"identity": identity, "status": "INVALID", "reason": str(exc)}
    final_skat = record["discards"] if record["discards"] is not None else record["skat_initial"]
    reconstructed_points = replay["declarer_trick_points"] + sum(card_points(card) for card in final_skat)
    reported_points = int(record["card_points"])
    if reconstructed_points != reported_points:
        return {
            "identity": identity, "status": "POINT_MISMATCH",
            "reported_points": reported_points,
            "reconstructed_points": reconstructed_points,
        }
    cards = (*record["initial_hands"][declarer], *record["skat_initial"])
    tricks = sum(t["winner"] == declarer for t in replay["completed_tricks"])
    try:
        score = score_basic_game(
            contract=contract, winning_bid=int(record["bid_level"]),
            declarer_cards=cards, declarer_points=reconstructed_points,
            declarer_tricks=tricks,
        )
    except ValueError as exc:
        return {"identity": identity, "status": "UNSUPPORTED", "reason": str(exc)}
    expected = int(record["game_value"])
    status = "MATCH" if score.signed_game_value == expected and score.matadors == int(record["matadors"]) else "MISMATCH"
    return {
        "identity": identity, "status": status, "contract": contract,
        "expected_value": expected, "computed_value": score.signed_game_value,
        "expected_matadors": int(record["matadors"]), "computed_matadors": score.matadors,
    }


def validate_stream(stream, expected_sha256: str) -> dict:
    digest = hashlib.sha256()
    counts = {"total": 0, "played": 0, "match": 0, "mismatch": 0,
              "point_mismatch": 0, "excluded_split": 0,
              "unsupported": 0, "incomplete": 0, "invalid": 0}
    details = []
    for raw in stream:
        digest.update(raw)
        counts["total"] += 1
        record = json.loads(raw)
        if record.get("classification") != "PARSED_PLAYED_GAME":
            continue
        counts["played"] += 1
        if split_for_game(record) != "train":
            counts["excluded_split"] += 1
            continue
        outcome = compare_played_record(record)
        counts[outcome["status"].lower()] += 1
        details.append(outcome)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"SOURCE_SHA256_MISMATCH:{actual}")
    return {
        "schema": SCHEMA,
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_sha256": actual,
        "counts": counts,
        "played_results": details,
        "use_restriction": "Train-split scoring-rules research only; keep semantic identities and result targets out of policy training and strength evaluation. Earlier mixed-split v2 oracle is quarantined.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = validate_stream(sys.stdin.buffer, args.expected_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=args.output.name + ".", dir=args.output.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(evidence, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, args.output)
    print(json.dumps(evidence["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
