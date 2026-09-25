"""Frozen, seat-balanced positions and clustered summaries for cardplay research.

These helpers define comparison inputs and preserve the deal as the unit of
uncertainty when its positions are replayed. They do not choose a candidate
or make an acceptance decision.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from math import sqrt
from typing import Iterable

from skatai.evaluation.cardplay_gameplay_gate import CardplayPosition
from skatai.selfplay.cardplay import make_deal


HAND_CONTRACTS = ("CH", "SH", "HH", "DH", "GH", "NH")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def frozen_hand_positions(
    deal_seeds: Iterable[int], *, winning_bid: int = 18,
) -> dict:
    """Use each specified deal at every seat and basic hand contract."""
    seeds = tuple(int(seed) for seed in deal_seeds)
    if not seeds or len(seeds) != len(set(seeds)) or any(seed < 0 for seed in seeds):
        raise ValueError("INVALID_CARDPLAY_CAMPAIGN_SEEDS")
    if winning_bid < 18:
        raise ValueError("INVALID_CARDPLAY_CAMPAIGN_BID")
    positions = [
        CardplayPosition(seed, seat, contract, winning_bid)
        for seed in seeds for contract in HAND_CONTRACTS for seat in range(3)
    ]
    rows = [
        {**asdict(position), "deal_sha256": make_deal(position.deal_seed).identity_sha256}
        for position in positions
    ]
    return {
        "schema": "skatai.v2.cardplay-hand-position-set.v1",
        "unit": "deal_seed/contract/declarer position; three candidate seats share one control",
        "treatment": "cardplay policy at exactly one seat",
        "contracts": list(HAND_CONTRACTS),
        "deal_seeds": list(seeds),
        "winning_bid": winning_bid,
        "position_count": len(rows),
        "positions_sha256": _canonical_sha256(rows),
        "positions": rows,
    }


def summarize_position_results(position_set: dict, results: Iterable[dict]) -> dict:
    """Require exact coverage and summarize one delta per independent position."""
    if position_set.get("schema") != "skatai.v2.cardplay-hand-position-set.v1":
        raise ValueError("BAD_CARDPLAY_POSITION_SET_SCHEMA")
    expected_rows = position_set["positions"]
    if len(expected_rows) != int(position_set["position_count"]):
        raise ValueError("CARDPLAY_POSITION_SET_COUNT_MISMATCH")
    if _canonical_sha256(expected_rows) != position_set.get("positions_sha256"):
        raise ValueError("CARDPLAY_POSITION_SET_HASH_MISMATCH")
    expected = {
        (int(row["deal_seed"]), str(row["contract"]), int(row["declarer"])): row
        for row in expected_rows
    }
    if len(expected) != len(expected_rows):
        raise ValueError("DUPLICATE_CARDPLAY_POSITION")

    seen: dict[tuple[int, str, int], dict] = {}
    for result in results:
        key = (int(result["deal_seed"]), str(result["contract"]), int(result["declarer"]))
        if key not in expected or key in seen:
            raise ValueError("UNEXPECTED_OR_DUPLICATE_CARDPLAY_RESULT")
        if result["deal_sha256"] != expected[key]["deal_sha256"]:
            raise ValueError("CARDPLAY_RESULT_DEAL_MISMATCH")
        rows = result["rows"]
        if len(rows) != 3 or {int(row["candidate_seat"]) for row in rows} != {0, 1, 2}:
            raise ValueError("CARDPLAY_RESULT_SEAT_COVERAGE")
        baseline_scores = {int(row["control_signed_declarer_score"]) for row in rows}
        if len(baseline_scores) != 1:
            raise ValueError("CARDPLAY_RESULT_CONTROL_SCORE_MISMATCH")
        for row in rows:
            seat = int(row["candidate_seat"])
            role = "DECLARER" if seat == key[2] else "DEFENDER"
            if row["role"] != role:
                raise ValueError("CARDPLAY_RESULT_ROLE_MISMATCH")
            score_change = (
                int(row["treatment_signed_declarer_score"])
                - int(row["control_signed_declarer_score"])
            )
            signed_delta = score_change if role == "DECLARER" else -score_change
            if float(row["candidate_delta"]) != float(signed_delta):
                raise ValueError("CARDPLAY_RESULT_DELTA_MISMATCH")
        seen[key] = result
    if seen.keys() != expected.keys():
        raise ValueError("INCOMPLETE_CARDPLAY_RESULT_SET")

    # A deal is reused across seats and contracts. Aggregate within deal
    # before computing uncertainty so correlated replays are not counted as
    # independent observations.
    grouped: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for key in sorted(seen):
        result = seen[key]
        if (int(result["winning_bid"]) != int(expected[key]["winning_bid"])
                or bool(result["picked_up_skat"])):
            raise ValueError("CARDPLAY_RESULT_DECLARATION_MISMATCH")
        deltas = [float(row["candidate_delta"]) for row in result["rows"]]
        mean = sum(deltas) / 3
        if abs(mean - float(result["position_cluster_mean_delta"])) > 1e-9:
            raise ValueError("CARDPLAY_CLUSTER_MEAN_MISMATCH")
        grouped["overall"][key[0]].append(mean)
        grouped[f"contract:{key[1]}"][key[0]].append(mean)
        family = "SUIT" if key[1][0] in "CSHD" else "GRAND" if key[1][0] == "G" else "NULL"
        grouped[f"family:{family}"][key[0]].append(mean)
    summaries = {}
    for group, deal_values in sorted(grouped.items()):
        values = [sum(rows) / len(rows) for _, rows in sorted(deal_values.items())]
        n = len(values)
        mean = sum(values) / n
        variance = sum((value - mean) ** 2 for value in values) / (n - 1) if n > 1 else None
        summaries[group] = {
            "independent_deal_count": n,
            "position_count": sum(len(rows) for rows in deal_values.values()),
            "mean_candidate_delta": mean,
            "standard_error": sqrt(variance / n) if variance is not None else None,
        }
    return {
        "schema": "skatai.v2.cardplay-position-cluster-summary.v1",
        "positions_sha256": position_set["positions_sha256"],
        "summary": summaries,
        "acceptance_decision": "NOT_AUTHORIZED",
    }
