from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from skatai.evaluation.bidding_gameplay_gate import _atomic_write_json

SCHEMA = "skatai.v2.local-bidding-pregate-decision.v1"


def decide_local_stage(result: Mapping[str, Any], stage: str) -> dict[str, Any]:
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
    stats = summary.get("paired_delta_stats") or {}
    actual_deals = int(config.get("deal_count") or 0)
    actual_pairs = int(config.get("paired_seat_observations") or 0)
    n = int(stats.get("n") or 0)

    if actual_deals != expected_deals or actual_pairs != expected_pairs or n != expected_pairs:
        status = "INCOMPLETE"
        next_action = "RESUME_STAGE"
    else:
        ci95_high = stats.get("ci95_high")
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
        "observed": {
            "deal_count": actual_deals,
            "paired_observations": actual_pairs,
            "paired_stats": stats,
            "changed_auction_pairs": summary.get("changed_auction_pairs"),
            "elapsed_s": summary.get("elapsed_s"),
        },
        "rule": (
            "If ci95_high < 0, stop/reject for clear local regression; "
            "otherwise continue. Local pre-gate evidence can never ACCEPT."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("result", type=Path)
    p.add_argument("--stage", required=True, choices=["SCREEN", "LOCAL_CONFIRMATION"])
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()

    result = json.loads(args.result.read_text(encoding="utf-8"))
    decision = decide_local_stage(result, args.stage)
    _atomic_write_json(args.output, decision)
    print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
