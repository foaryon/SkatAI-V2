#!/usr/bin/env python3
"""Evaluate a bounded PIMC research policy on identified train labels."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

from skatai.evaluation.endgame_pimc import choose_endgame_card
from skatai.runtime.interface import CardplayObservation

SCHEMA = "skatai.v2.endgame-pimc-train-label-pilot.v1"


def run_pilot(source: Path, *, expected_sha256: str, max_worlds: int, seed: int) -> dict:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("PIMC_PILOT_SOURCE_HASH_MISMATCH")
    if len(raw) > 1024 * 1024 or not 1 <= max_worlds <= 256:
        raise ValueError("PIMC_PILOT_RESOURCE_BOUND_INVALID")
    results = []
    counts: Counter[str] = Counter()
    for index, line in enumerate(raw.splitlines()):
        row = json.loads(line)
        if (row.get("schema") != "skatai.v2.offline-endgame-learner-row.v1"
                or row.get("training_approved") is not False):
            raise ValueError("PIMC_PILOT_LABEL_ROW_INVALID")
        observation = CardplayObservation.create(**row["observation"])
        # Inference receives only the decision-time observation. The teacher
        # target is read for comparison after the action has been chosen.
        started = time.perf_counter()
        answer = choose_endgame_card(
            observation, max_worlds=max_worlds, seed=seed,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        target = str(row["target_card"])
        if target not in observation.legal_cards:
            raise ValueError("PIMC_PILOT_TEACHER_TARGET_ILLEGAL")
        family = "SUIT" if observation.contract in {"C", "S", "H", "D"} else (
            "GRAND" if observation.contract == "G" else "NULL"
        )
        role = "DECLARER" if observation.seat == observation.declarer else "DEFENDER"
        counts["rows"] += 1
        counts[f"family:{family}"] += 1
        counts["pimc_teacher_agreement"] += int(answer["card"] == target)
        counts["first_legal_teacher_agreement"] += int(observation.legal_cards[0] == target)
        results.append({
            "index": index,
            "family": family,
            "role": role,
            "contract": observation.contract,
            "observation_selection_sha256": answer["observation_selection_sha256"],
            "target_card": target,
            "pimc_card": answer["card"],
            "first_legal_card": observation.legal_cards[0],
            "world_count": answer["world_count"],
            "latency_ms": latency_ms,
        })
    predictions = [{k: row[k] for k in (
        "index", "observation_selection_sha256", "target_card", "pimc_card",
        "first_legal_card", "world_count",
    )} for row in results]
    prediction_sha256 = hashlib.sha256(json.dumps(
        predictions, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return {
        "schema": SCHEMA,
        "source_sha256": expected_sha256,
        "max_worlds": max_worlds,
        "seed": seed,
        "counts": dict(sorted(counts.items())),
        "prediction_sha256": prediction_sha256,
        "rows": results,
        "training_approved": False,
        "strength_claim_authorized": False,
        "interpretation": "Offline teacher agreement on train rows is a proxy only; paired B0 gameplay is required for strength.",
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--expected-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--max-worlds", type=int, default=32)
    p.add_argument("--seed", type=int, default=20260925)
    args = p.parse_args()
    result = run_pilot(
        args.source, expected_sha256=args.expected_sha256,
        max_worlds=args.max_worlds, seed=args.seed,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
