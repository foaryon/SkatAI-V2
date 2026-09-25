#!/usr/bin/env python3
"""Bounded paired B0 cardplay screen for an observation-only endgame treatment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from skatai.evaluation.cardplay_gameplay_gate import CardplayPosition, evaluate_cardplay_position
from skatai.evaluation.endgame_pimc import PIMCOverridePolicy
from skatai.runtime.skatzero_backend import FrozenB0CardplayPolicy
from skatai.runtime.skatzero_pool import PersistentSkatZeroPool
from skatai.selfplay.cardplay import make_deal


def canonical_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_screen(task: dict, *, b0_root: Path, b0_python: Path) -> dict:
    if task.get("schema") != "skatai.v2.endgame-pimc-paired-screen-task.v1":
        raise ValueError("PIMC_SCREEN_TASK_SCHEMA")
    positions = task["positions"]
    if not 1 <= len(positions) <= 12 or canonical_sha(positions) != task["positions_sha256"]:
        raise ValueError("PIMC_SCREEN_POSITION_IDENTITY")
    if int(task["max_worlds"]) not in range(1, 257):
        raise ValueError("PIMC_SCREEN_WORLD_LIMIT")
    if len({(p["deal_seed"], p["contract"], p["declarer"]) for p in positions}) != len(positions):
        raise ValueError("PIMC_SCREEN_DUPLICATE_POSITION")
    for row in positions:
        if make_deal(int(row["deal_seed"])).identity_sha256 != row["deal_sha256"]:
            raise ValueError("PIMC_SCREEN_DEAL_IDENTITY")
    started = time.monotonic()
    with PersistentSkatZeroPool(b0_root, b0_python, workers=1) as pool:
        def control(_seat: int):
            return FrozenB0CardplayPolicy(b0_root, b0_python, runner=pool)

        def candidate(seat: int):
            return PIMCOverridePolicy(
                control(seat), max_worlds=int(task["max_worlds"]), seed=int(task["seed"]),
            )

        results = [evaluate_cardplay_position(
            CardplayPosition(int(row["deal_seed"]), int(row["declarer"]),
                             str(row["contract"]), int(row["winning_bid"])),
            control_factory=control, candidate_factory=candidate,
        ) for row in positions]
    return {
        "schema": "skatai.v2.endgame-pimc-paired-screen-result.v1",
        "task_id": task["task_id"],
        "positions_sha256": task["positions_sha256"],
        "max_worlds": task["max_worlds"], "seed": task["seed"],
        "b0_upstream_commit": task["b0_upstream_commit"],
        "results": results,
        "position_count": len(results),
        "candidate_seat_deltas": [row["candidate_delta"] for result in results for row in result["rows"]],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "strength_claim_authorized": False,
        "interpretation": "Bounded paired gameplay screen on six fixed positions; no acceptance or general strength claim.",
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--b0-root", type=Path, default=Path("/tmp/skatai-v2-b0"))
    p.add_argument("--b0-python", type=Path, default=Path("/tmp/skatai-v2-b0-venv/bin/python"))
    args = p.parse_args()
    task = json.loads(args.task.read_text())
    result = run_screen(task, b0_root=args.b0_root, b0_python=args.b0_python)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"position_count": result["position_count"],
                      "candidate_seat_deltas": result["candidate_seat_deltas"]}))


if __name__ == "__main__":
    main()
