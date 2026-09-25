#!/usr/bin/env python3
"""Apply the frozen deal-clustered decision rule to a PIMC campaign result."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from skatai.evaluation.cardplay_campaign import frozen_hand_positions, summarize_position_results

T_CRITICAL_95_DF29 = 2.045229642


def _same_summary(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_summary(left[key], right[key]) for key in left
        )
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)
    return left == right


def _verified_json(path: Path, expected_sha256: str) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("PIMC_CAMPAIGN_INPUT_HASH_MISMATCH")
    return json.loads(raw)


def conclude(task: dict, result: dict, *, task_sha256: str, result_sha256: str) -> dict:
    if (task.get("task_id") != "ENDGAME_PIMC_PAIRED_CAMPAIGN_30_DEALS"
            or result.get("schema") != "skatai.v2.endgame-pimc-paired-screen-result.v1"
            or result.get("task_id") != task["task_id"]):
        raise ValueError("PIMC_CAMPAIGN_TASK_RESULT_MISMATCH")
    position_set = frozen_hand_positions(task["deal_seeds"], winning_bid=task["winning_bid"])
    if (position_set["positions_sha256"] != task["positions_sha256"]
            or result["positions_sha256"] != task["positions_sha256"]
            or result["position_count"] != position_set["position_count"]
            or result["max_worlds"] != task["max_worlds"]
            or result["seed"] != task["seed"]
            or result["b0_upstream_commit"] != task["b0_upstream_commit"]):
        raise ValueError("PIMC_CAMPAIGN_IDENTITY_MISMATCH")
    summary = summarize_position_results(position_set, result["results"])
    if not _same_summary(result["cluster_summary"], summary):
        raise ValueError("PIMC_CAMPAIGN_SUMMARY_MISMATCH")
    rows = [row for position in result["results"] for row in position["rows"]]
    if result["candidate_seat_deltas"] != [row["candidate_delta"] for row in rows]:
        raise ValueError("PIMC_CAMPAIGN_DELTA_LIST_MISMATCH")
    changed = 0
    for row in rows:
        divergence = row["first_divergence_play_index"]
        equal_hash = row["control_play_sha256"] == row["treatment_play_sha256"]
        if (divergence is None and not equal_hash) or (
            divergence is not None and (not 21 <= divergence < 30 or equal_hash)
        ):
            raise ValueError("PIMC_CAMPAIGN_PLAY_TRACE_MISMATCH")
        changed += divergence is not None
    if changed != result["changed_game_count"]:
        raise ValueError("PIMC_CAMPAIGN_CHANGED_COUNT_MISMATCH")
    overall = summary["summary"]["overall"]
    if overall["independent_deal_count"] != 30 or len(rows) != 1620:
        raise ValueError("PIMC_CAMPAIGN_COVERAGE_MISMATCH")
    mean = overall["mean_candidate_delta"]
    half_width = T_CRITICAL_95_DF29 * overall["standard_error"]
    lower, upper = mean - half_width, mean + half_width
    decision = (
        "NO_TREATMENT_EXPOSURE" if changed == 0 else
        "ADVANCE_TO_DISJOINT_CONFIRMATION" if mean > 0 and lower > 0 else
        "REJECT_DIRECT_PIMC_TREATMENT" if upper < 0 else
        "INCONCLUSIVE"
    )
    return {
        "schema": "skatai.v2.endgame-pimc-campaign-conclusion.v1",
        "task_id": task["task_id"],
        "task_sha256": task_sha256,
        "result_sha256": result_sha256,
        "positions_sha256": task["positions_sha256"],
        "independent_deal_count": 30,
        "position_count": position_set["position_count"],
        "candidate_game_count": len(rows),
        "changed_game_count": changed,
        "mean_candidate_delta": mean,
        "standard_error": overall["standard_error"],
        "approximate_95pct_t_interval": [lower, upper],
        "decision": decision,
        "acceptance_decision": "NOT_AUTHORIZED",
        "strength_claim_authorized": False,
        "limits": "One frozen research campaign with correlated positions clustered by deal; any favorable result requires disjoint confirmation and deployment gates.",
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", type=Path, required=True)
    p.add_argument("--task-sha256", required=True)
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--result-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    task = _verified_json(args.task, args.task_sha256)
    result = _verified_json(args.result, args.result_sha256)
    conclusion = conclude(task, result, task_sha256=args.task_sha256,
                          result_sha256=args.result_sha256)
    args.output.write_text(json.dumps(conclusion,sort_keys=True,indent=2) + "\n")
    print(conclusion["decision"])


if __name__ == "__main__":
    main()
