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

from skatai.game.rules import replay_tricks
from skatai.selfplay.scoring import score_basic_game

SCHEMA = "skatai.v2.evidence.basic-score-corpus-oracle.v1"


def compare_played_record(record: dict) -> dict:
    identity = str(record["semantic_sha256"])
    if not identity or record.get("play_count") != 30:
        return {"identity": identity, "status": "INCOMPLETE"}
    contract = str(record["contract_base"]) + str(record["contract_modifiers"])
    declarer = int(record["declarer"])
    replay = replay_tricks(
        record["plays"], game_type=str(record["game_type"]), declarer=declarer,
    )
    cards = (*record["initial_hands"][declarer], *record["skat_initial"])
    tricks = sum(t["winner"] == declarer for t in replay["completed_tricks"])
    try:
        score = score_basic_game(
            contract=contract, winning_bid=int(record["bid_level"]),
            declarer_cards=cards, declarer_points=int(record["card_points"]),
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
              "unsupported": 0, "incomplete": 0}
    details = []
    for raw in stream:
        digest.update(raw)
        counts["total"] += 1
        record = json.loads(raw)
        if record.get("classification") != "PARSED_PLAYED_GAME":
            continue
        counts["played"] += 1
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
        "use_restriction": "Scoring-rules research only; keep semantic identities and result targets out of policy training and strength evaluation.",
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
