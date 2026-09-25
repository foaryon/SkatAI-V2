#!/usr/bin/env python3
"""Describe the first B0/B1 auction action change in a frozen local paired result.

Aggregate only; this diagnostic neither selects training identities nor makes a
promotion claim. A deal, not its three candidate-seat replays, is the cluster.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

EXPECTED_INPUT_SHA256 = "836785a374437bc3417d9865a7a9ca22396765d2eb60fd930c72dad4e184d634"
SCHEMA = "skatai.v2.b1-first-auction-divergence.v1"
CONTEXT_FIELDS = ("actor", "bidder", "answerer", "bid_index", "current_offer", "decision_role")


def _first_change(baseline: list[dict], treatment: list[dict], candidate_seat: int) -> dict | None:
    for left, right in zip(baseline, treatment):
        if left["native_action"] == right["native_action"]:
            continue
        if any(left[field] != right[field] for field in CONTEXT_FIELDS):
            raise ValueError("FIRST_CHANGE_CONTEXT_MISMATCH")
        if int(left["actor"]) != candidate_seat:
            raise ValueError("FIRST_CHANGE_NOT_CANDIDATE_SEAT")
        baseline_pass = left["native_action"] == "p"
        treatment_pass = right["native_action"] == "p"
        if baseline_pass == treatment_pass:
            raise ValueError("FIRST_CHANGE_NOT_PASS_CONTINUE")
        return {
            "direction": "PASS_TO_CONTINUE" if baseline_pass else "CONTINUE_TO_PASS",
            "role": left["decision_role"],
            "offer": int(left["current_offer"]),
            "actor": candidate_seat,
        }
    if len(baseline) != len(treatment):
        raise ValueError("UNALIGNED_AUCTION_PREFIX")
    return None


def analyze(path: Path) -> dict:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_INPUT_SHA256:
        raise ValueError("LOCAL_PAIRED_RESULT_HASH_MISMATCH")
    source = json.loads(raw)
    if source.get("schema") != "skatai.v2.b0-vs-b1-local-paired-gameplay.v1":
        raise ValueError("LOCAL_PAIRED_RESULT_SCHEMA_MISMATCH")
    records = source["records"]
    if len(records) != 100 or sum(len(row["paired"]) for row in records) != 300:
        raise ValueError("LOCAL_PAIRED_RESULT_COVERAGE_MISMATCH")
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    counts = {"unchanged": 0, "changed": 0}
    seen_deals = set()
    for record in records:
        deal = record["deal_identity"]
        if deal in seen_deals:
            raise ValueError("DUPLICATE_DEAL_IDENTITY")
        seen_deals.add(deal)
        baseline = record["baseline_auction"]["decisions"]
        for paired in record["paired"]:
            change = _first_change(
                baseline, paired["treatment_auction"]["decisions"],
                int(paired["candidate_seat"]),
            )
            if change is None:
                counts["unchanged"] += 1
                continue
            counts["changed"] += 1
            delta = float(paired["delta"])
            offer_band = "18-24" if change["offer"] <= 24 else "25+"
            for key in (
                "overall", f"direction:{change['direction']}",
                f"role:{change['role']}", f"offer_band:{offer_band}",
                f"direction_role:{change['direction']}:{change['role']}",
            ):
                groups[key][deal].append(delta)
    if counts != {"unchanged": 229, "changed": 71}:
        raise ValueError("FIRST_CHANGE_COUNT_MISMATCH")
    summary = {}
    for key, by_deal in sorted(groups.items()):
        values = [sum(rows) / len(rows) for rows in by_deal.values()]
        summary[key] = {
            "pair_count": sum(len(rows) for rows in by_deal.values()),
            "independent_deal_count": len(values),
            "deal_cluster_mean_delta": sum(values) / len(values),
            "negative_deal_count": sum(x < 0 for x in values),
            "positive_deal_count": sum(x > 0 for x in values),
        }
    return {
        "schema": SCHEMA,
        "source_sha256": digest,
        "source_schema": source["schema"],
        "counts": counts,
        "summary": summary,
        "interpretation": "Exploratory local diagnostic only; no training identity selection, external holdout use, or strength decision.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(analyze(args.input), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
