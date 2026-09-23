from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from skatai.evaluation.bidding_gameplay_gate import _atomic_write_json, paired_delta_stats

SCHEMA = "skatai.v2.local-bidding-pregate-clustered-decision.v1"


def deal_mean_deltas(result: Mapping[str, Any]) -> list[float]:
    records = result.get("records") or []
    means: list[float] = []
    for record in records:
        paired = record.get("paired") or []
        if len(paired) != 3:
            raise ValueError(
                f"EXPECTED_THREE_SEAT_TREATMENTS:{record.get('deal_identity')}:{len(paired)}"
            )
        deltas = [float(x["delta"]) for x in paired]
        means.append(sum(deltas) / 3.0)
    return means


def decide_clustered_local_stage(result: Mapping[str, Any], stage: str) -> dict[str, Any]:
    stage = stage.upper()
    expected = {
        "SCREEN": (30, 90),
        "LOCAL_CONFIRMATION": (100, 300),
    }
    if stage not in expected:
        raise ValueError(f"UNKNOWN_LOCAL_STAGE:{stage}")

    expected_deals, expected_pairs = expected[stage]
    config = result.get("configuration") or {}
    summary = result.get("summary") or {}
    actual_deals = int(config.get("deal_count") or 0)
    actual_pairs = int(config.get("paired_seat_observations") or 0)

    means = deal_mean_deltas(result)
    clustered = paired_delta_stats(means)
    seat_level = summary.get("paired_delta_stats") or {}

    if (
        actual_deals != expected_deals
        or actual_pairs != expected_pairs
        or len(means) != expected_deals
    ):
        status = "INCOMPLETE"
        next_action = "RESUME_STAGE"
    else:
        ci95_high = clustered.get("ci95_high")
        if ci95_high is None:
            status = "INCOMPLETE"
            next_action = "RESUME_STAGE"
        elif float(ci95_high) < 0.0:
            status = "LOCAL_REGRESSION"
            next_action = (
                "STOP_CANDIDATE"
                if stage == "SCREEN"
                else "REJECT_FOR_LOCAL_REGRESSION"
            )
        else:
            status = "LOCAL_GATE_NOT_REGRESSING"
            next_action = (
                "CONTINUE_LOCAL_CONFIRMATION"
                if stage == "SCREEN"
                else "REQUIRE_EXTERNAL_DEPLOYMENT_VALID_EVALUATION"
            )

    return {
        "schema": SCHEMA,
        "stage": stage,
        "status": status,
        "next_action": next_action,
        "strength_claim_authorized": False,
        "accept_authorized": False,
        "primary_statistical_unit": "deal_cluster_mean_of_three_candidate_seat_deltas",
        "primary_stats": clustered,
        "secondary_seat_level_stats": seat_level,
        "observed": {
            "deal_count": actual_deals,
            "paired_seat_observations": actual_pairs,
            "changed_auction_pairs": summary.get("changed_auction_pairs"),
            "elapsed_s": summary.get("elapsed_s"),
        },
        "rule": (
            "Use deal-clustered CI as primary local screen statistic. "
            "If clustered ci95_high < 0, stop/reject for clear local regression; "
            "otherwise continue. Local evidence can never ACCEPT."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("result", type=Path)
    p.add_argument(
        "--stage", required=True, choices=["SCREEN", "LOCAL_CONFIRMATION"]
    )
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()

    result = json.loads(args.result.read_text(encoding="utf-8"))
    decision = decide_clustered_local_stage(result, args.stage)
    _atomic_write_json(args.output, decision)
    print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
