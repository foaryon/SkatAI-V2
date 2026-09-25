#!/usr/bin/env python3
"""Exercise the four JSONL host decisions from a staged, validated B0 package."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from skatai.artifacts.b0_release import build_b0_release
from skatai.artifacts.release import sha256_file
from skatai.runtime.host_service import (
    PICKUP_PLAN_REQUEST_SCHEMA,
    PICKUP_PLAN_RESPONSE_SCHEMA,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
)
from skatai.selfplay.cardplay import make_deal


def run_smoke(repo: Path, upstream_source: Path, python: Path, scratch: Path) -> dict:
    scratch.mkdir(parents=True, exist_ok=False)
    package = scratch / "V2-B0-current-source.skatmodel"
    built = build_b0_release(repo=repo, upstream_source=upstream_source, output=package)
    release_id = built["release_id"]
    deal = make_deal(20260925)
    hand = list(deal.hands[0])
    hand12 = hand + list(deal.skat)
    requests = [
        {"decision_type": "BID", "observation": {
            "hand": hand, "actor": 0, "bidder": 0, "answerer": 1,
            "bid_index": 0, "decision_role": "BIDDER",
        }},
        {"decision_type": "DECLARATION", "observation": {
            "cards": hand12, "seat": 0, "winning_bid": 18,
            "picked_up_skat": True, "legal_contracts": ["C", "S", "H", "D", "G", "N"],
            "max_accepted_bids_by_seat": [18, 0, 0],
        }},
        {"decision_type": "DISCARD", "observation": {
            "hand12": hand12, "seat": 0, "winning_bid": 18,
            "max_accepted_bids_by_seat": [18, 0, 0],
        }},
        {"decision_type": "PLAY_CARD", "observation": {
            "hand": hand, "seat": 0, "declarer": 0, "contract": "GH",
            "winning_bid": 18, "current_trick": [], "played_cards": [],
            "legal_cards": hand, "blind_hand": True,
            "points_self": 0, "points_other": 0,
            "max_accepted_bids_by_seat": [18, 0, 0],
        }},
    ]
    payloads = [dict(schema=REQUEST_SCHEMA, game_id=f"host-smoke-{i}",
                     sequence_no=0, **row) for i, row in enumerate(requests)]
    payloads.append({
        "schema": PICKUP_PLAN_REQUEST_SCHEMA,
        "game_id": "host-smoke-pickup-plan", "sequence_no": 0,
        "hand12": hand12, "seat": 0, "winning_bid": 18,
        "max_accepted_bids_by_seat": [18, 0, 0],
        "legal_contracts": ["C", "S", "H", "D", "G", "N"],
    })
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "src")
    started = time.monotonic()
    proc = subprocess.run(
        [str(python), "-m", "skatai.runtime.host_service", "--package", str(package),
         "--materialize-to", str(scratch / "materialized"), "--python", str(python)],
        input="".join(json.dumps(x, separators=(",", ":")) + "\n" for x in payloads),
        text=True, capture_output=True, timeout=900, env=env,
    )
    if proc.returncode:
        raise RuntimeError(f"HOST_SERVICE_EXITED:{proc.returncode}:{proc.stderr[-500:]}")
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    if len(responses) != 5:
        raise RuntimeError("HOST_SMOKE_RESPONSE_COUNT")
    actions = []
    for index, response in enumerate(responses[:4]):
        if (response.get("schema") != RESPONSE_SCHEMA or response.get("ok") is not True
                or response["result"]["release_id"] != release_id):
            raise RuntimeError(f"HOST_SMOKE_RESPONSE_INVALID:{index}:{response.get('error_code')}")
        actions.append(response["result"]["action"])
    pickup_plan = responses[4]
    if (pickup_plan.get("schema") != PICKUP_PLAN_RESPONSE_SCHEMA
            or pickup_plan.get("ok") is not True
            or pickup_plan.get("release_id") != release_id
            or pickup_plan.get("contract") not in payloads[4]["legal_contracts"]
            or len(pickup_plan.get("discard", [])) != 2
            or len(set(pickup_plan["discard"])) != 2
            or set(pickup_plan["discard"]) - set(hand12)
            or len(pickup_plan.get("final_hand", [])) != 10
            or set(pickup_plan.get("final_hand", [])) != set(hand12) - set(pickup_plan["discard"])):
        raise RuntimeError(f"HOST_SMOKE_PICKUP_PLAN_INVALID:{pickup_plan.get('error_code')}")
    if actions[0] not in ("PASS", "CONTINUE") or actions[1] not in requests[1]["observation"]["legal_contracts"]:
        raise RuntimeError("HOST_SMOKE_BID_OR_DECLARATION_ILLEGAL")
    discard = actions[2].split(".")
    if len(discard) != 2 or len(set(discard)) != 2 or not set(discard).issubset(hand12):
        raise RuntimeError("HOST_SMOKE_DISCARD_ILLEGAL")
    if actions[3] not in hand:
        raise RuntimeError("HOST_SMOKE_CARDPLAY_ILLEGAL")
    package_sha = sha256_file(package)
    return {
        "schema": "skatai.v2.b0-host-release-smoke.v1",
        "source_commit": subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
        "release_id": release_id,
        "package_sha256": package_sha,
        "package_bytes": package.stat().st_size,
        "manifest_sha256": built["manifest_sha256"],
        "phases": [row["decision_type"] for row in requests] + ["PICKUP_PLAN"],
        "actions": actions,
        "response_count": len(responses),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "accepted_release_claim": False,
        "actual_user_host_integration_claim": False,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--upstream-source", type=Path, required=True)
    p.add_argument("--python", type=Path, required=True)
    p.add_argument("--scratch", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = run_smoke(args.repo, args.upstream_source, args.python, args.scratch)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"phases": result["phases"], "package_sha256": result["package_sha256"]}))


if __name__ == "__main__":
    main()
