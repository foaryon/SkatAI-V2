from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "skatai.v2.bidding-weakness-diagnostic.v1"


def _stats(xs: Sequence[float]) -> dict[str, float | int | None]:
    vals = [float(x) for x in xs]
    n = len(vals)
    if not n:
        return {
            "n": 0,
            "mean": None,
            "positive_rate": None,
            "negative_rate": None,
            "zero_rate": None,
            "min": None,
            "max": None,
        }
    return {
        "n": n,
        "mean": sum(vals) / n,
        "positive_rate": sum(x > 0 for x in vals) / n,
        "negative_rate": sum(x < 0 for x in vals) / n,
        "zero_rate": sum(x == 0 for x in vals) / n,
        "min": min(vals),
        "max": max(vals),
    }


def _auction_identity(auction: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        auction.get("winner"),
        auction.get("winning_bid"),
        tuple(
            (
                d.get("actor"),
                d.get("current_offer"),
                d.get("decision_role"),
                d.get("native_action"),
            )
            for d in auction.get("decisions", ())
        ),
    )


def _contract(declaration: Mapping[str, Any] | None) -> str:
    if declaration is None:
        return "ALL_PASS"
    return str(declaration.get("actual_contract") or "UNKNOWN")


def _transition(
    baseline: Mapping[str, Any],
    treatment: Mapping[str, Any],
) -> str:
    bw = baseline.get("winner")
    tw = treatment.get("winner")
    bb = baseline.get("winning_bid")
    tb = treatment.get("winning_bid")
    if bw is None and tw is None:
        return "UNCHANGED_ALL_PASS"
    if bw is None and tw is not None:
        return "ALL_PASS_TO_GAME"
    if bw is not None and tw is None:
        return "GAME_TO_ALL_PASS"
    if bw != tw:
        return "DECLARER_CHANGED"
    if bb != tb:
        return "SAME_DECLARER_BID_CHANGED"
    if _auction_identity(baseline) != _auction_identity(treatment):
        return "SAME_OUTCOME_PATH_CHANGED"
    return "UNCHANGED"


def analyze_gate_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != "skatai.v2.b0-vs-b1-local-paired-gameplay.v1":
        raise ValueError("UNSUPPORTED_GATE_SCHEMA")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("MISSING_GATE_RECORDS")

    all_deltas: list[float] = []
    changed_deltas: list[float] = []
    unchanged_deltas: list[float] = []
    by_candidate_seat: dict[str, list[float]] = defaultdict(list)
    by_baseline_contract: dict[str, list[float]] = defaultdict(list)
    by_treatment_contract: dict[str, list[float]] = defaultdict(list)
    by_transition: dict[str, list[float]] = defaultdict(list)
    by_baseline_decl_seat_relation: dict[str, list[float]] = defaultdict(list)

    changed_pairs = 0
    treatment_declared_by_candidate = 0
    baseline_declared_by_candidate = 0
    candidate_became_declarer = 0
    candidate_lost_declarer = 0
    higher_winning_bid = 0
    lower_winning_bid = 0

    for record in records:
        baseline_auction = record["baseline_auction"]
        baseline_decl = record.get("baseline_declaration")
        baseline_contract = _contract(baseline_decl)
        bw = baseline_auction.get("winner")

        for pair in record["paired"]:
            seat = int(pair["candidate_seat"])
            delta = float(pair["delta"])
            treatment_auction = pair["treatment_auction"]
            treatment_decl = pair.get("treatment_declaration")
            treatment_contract = _contract(treatment_decl)
            tw = treatment_auction.get("winner")

            changed = _auction_identity(baseline_auction) != _auction_identity(
                treatment_auction
            )
            transition = _transition(baseline_auction, treatment_auction)

            all_deltas.append(delta)
            by_candidate_seat[str(seat)].append(delta)
            by_baseline_contract[baseline_contract].append(delta)
            by_treatment_contract[treatment_contract].append(delta)
            by_transition[transition].append(delta)
            (changed_deltas if changed else unchanged_deltas).append(delta)
            changed_pairs += int(changed)

            if bw == seat:
                baseline_declared_by_candidate += 1
                relation = "CANDIDATE_BASELINE_DECLARER"
            elif bw is None:
                relation = "BASELINE_ALL_PASS"
            else:
                relation = "CANDIDATE_BASELINE_DEFENDER"
            by_baseline_decl_seat_relation[relation].append(delta)

            treatment_declared_by_candidate += int(tw == seat)
            candidate_became_declarer += int(bw != seat and tw == seat)
            candidate_lost_declarer += int(bw == seat and tw != seat)

            bb = baseline_auction.get("winning_bid")
            tb = treatment_auction.get("winning_bid")
            if bb is not None and tb is not None:
                higher_winning_bid += int(int(tb) > int(bb))
                lower_winning_bid += int(int(tb) < int(bb))

    return {
        "schema": SCHEMA,
        "source_schema": payload.get("schema"),
        "scope": {
            "deals": len(records),
            "paired_candidate_seat_observations": len(all_deltas),
            "diagnostic_only": True,
            "promotion_evidence": False,
        },
        "overall": _stats(all_deltas),
        "auction_change": {
            "changed_pairs": changed_pairs,
            "unchanged_pairs": len(all_deltas) - changed_pairs,
            "changed": _stats(changed_deltas),
            "unchanged": _stats(unchanged_deltas),
            "baseline_candidate_declarer_count": baseline_declared_by_candidate,
            "treatment_candidate_declarer_count": treatment_declared_by_candidate,
            "candidate_became_declarer": candidate_became_declarer,
            "candidate_lost_declarer": candidate_lost_declarer,
            "higher_winning_bid": higher_winning_bid,
            "lower_winning_bid": lower_winning_bid,
        },
        "by_candidate_seat": {
            key: _stats(value) for key, value in sorted(by_candidate_seat.items())
        },
        "by_baseline_contract": {
            key: _stats(value) for key, value in sorted(by_baseline_contract.items())
        },
        "by_treatment_contract": {
            key: _stats(value) for key, value in sorted(by_treatment_contract.items())
        },
        "by_auction_transition": {
            key: _stats(value) for key, value in sorted(by_transition.items())
        },
        "by_baseline_role": {
            key: _stats(value)
            for key, value in sorted(by_baseline_decl_seat_relation.items())
        },
        "interpretation_constraints": [
            "This report is a diagnostic decomposition of the already-frozen local B0-vs-B1 pre-gate.",
            "It does not establish B1 improvement or regression.",
            "It must not alter the frozen external ISS decision rule or stopping boundaries.",
            "Small strata are descriptive only and must not be treated as independently powered tests.",
        ],
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb", buffering=1024 * 1024) as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("gate_result", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    payload = json.loads(args.gate_result.read_text(encoding="utf-8"))
    result = analyze_gate_result(payload)
    result["source_artifact"] = {
        "path": str(args.gate_result),
        "sha256": sha256_file(args.gate_result),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
