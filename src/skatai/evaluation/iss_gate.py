from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Iterable, Mapping, Sequence

SCHEMA = "skatai.v2.external-iss-gate-analysis.v1"
PROTOCOL_SCHEMA = "skatai.v2.external-iss-gate-decision-rule.v1"
ARMS = ("B0", "B1")
LOOKS_PER_ARM = (300, 1000, 3000)
FAMILY_ALPHA = 0.05
PER_LOOK_ALPHA = FAMILY_ALPHA / len(LOOKS_PER_ARM)
CONFIDENCE = 1.0 - PER_LOOK_ALPHA
DEFAULT_BOOTSTRAP_REPLICATES = 20000
DEFAULT_SEED = 20260923


@dataclass(frozen=True)
class ISSGameOutcome:
    arm: str
    opponent: str
    seat: int
    score: float
    game_id: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ISSGameOutcome":
        arm = str(row["arm"])
        if arm not in ARMS:
            raise ValueError(f"BAD_ARM:{arm}")
        seat = int(row["seat"])
        if seat not in (0, 1, 2):
            raise ValueError(f"BAD_SEAT:{seat}")
        opponent = str(row["opponent"]).strip()
        game_id = str(row["game_id"]).strip()
        if not opponent or not game_id:
            raise ValueError("EMPTY_OPPONENT_OR_GAME_ID")
        score = float(row["score"])
        if not math.isfinite(score):
            raise ValueError("NONFINITE_SCORE")
        return cls(arm=arm, opponent=opponent, seat=seat, score=score, game_id=game_id)


def _validate_unique_games(games: Sequence[ISSGameOutcome]) -> None:
    seen: set[str] = set()
    dup: list[str] = []
    for g in games:
        if g.game_id in seen:
            dup.append(g.game_id)
        seen.add(g.game_id)
    if dup:
        raise ValueError(f"DUPLICATE_GAME_IDS:{sorted(set(dup))[:5]}")


def _arm_counts(games: Sequence[ISSGameOutcome]) -> dict[str, int]:
    return {arm: sum(g.arm == arm for g in games) for arm in ARMS}


def current_look(games: Sequence[ISSGameOutcome]) -> tuple[int | None, int | None]:
    counts = _arm_counts(games)
    completed = [n for n in LOOKS_PER_ARM if all(counts[a] >= n for a in ARMS)]
    if not completed:
        return None, LOOKS_PER_ARM[0]
    look = max(completed)
    next_look = next((n for n in LOOKS_PER_ARM if n > look), None)
    return look, next_look


def _strata(games: Iterable[ISSGameOutcome]):
    by = defaultdict(lambda: {"B0": [], "B1": []})
    for g in games:
        by[(g.opponent, g.seat)][g.arm].append(g.score)
    return by


def _eligible_strata(games: Sequence[ISSGameOutcome]):
    by = _strata(games)
    eligible = {k: v for k, v in by.items() if v["B0"] and v["B1"]}
    if not eligible:
        raise ValueError("NO_MATCHED_OPPONENT_SEAT_STRATA")
    return eligible


def stratified_difference(games: Sequence[ISSGameOutcome]) -> dict:
    eligible = _eligible_strata(games)
    total = sum(len(v["B0"]) + len(v["B1"]) for v in eligible.values())
    weighted = 0.0
    rows = []
    for (opponent, seat), v in sorted(eligible.items()):
        mean0 = sum(v["B0"]) / len(v["B0"])
        mean1 = sum(v["B1"]) / len(v["B1"])
        n = len(v["B0"]) + len(v["B1"])
        weight = n / total
        delta = mean1 - mean0
        weighted += weight * delta
        rows.append({
            "opponent": opponent,
            "seat": seat,
            "n_b0": len(v["B0"]),
            "n_b1": len(v["B1"]),
            "mean_b0": mean0,
            "mean_b1": mean1,
            "delta_b1_minus_b0": delta,
            "weight": weight,
        })
    return {
        "estimate": weighted,
        "strata": rows,
        "matched_games": total,
    }


def stratified_bootstrap_interval(
    games: Sequence[ISSGameOutcome],
    *,
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_SEED,
    confidence: float = CONFIDENCE,
) -> dict:
    if replicates < 1000:
        raise ValueError("BOOTSTRAP_REPLICATES_TOO_SMALL")
    eligible = _eligible_strata(games)
    total = sum(len(v["B0"]) + len(v["B1"]) for v in eligible.values())
    rng = random.Random(seed)
    samples: list[float] = []

    strata = list(sorted(eligible.items()))
    for _ in range(replicates):
        estimate = 0.0
        for _, v in strata:
            n0, n1 = len(v["B0"]), len(v["B1"])
            weight = (n0 + n1) / total
            m0 = sum(v["B0"][rng.randrange(n0)] for _ in range(n0)) / n0
            m1 = sum(v["B1"][rng.randrange(n1)] for _ in range(n1)) / n1
            estimate += weight * (m1 - m0)
        samples.append(estimate)

    samples.sort()
    alpha = 1.0 - confidence

    def quantile(p: float) -> float:
        idx = p * (len(samples) - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return samples[lo]
        frac = idx - lo
        return samples[lo] * (1.0 - frac) + samples[hi] * frac

    return {
        "replicates": replicates,
        "seed": seed,
        "confidence": confidence,
        "alpha": alpha,
        "low": quantile(alpha / 2.0),
        "high": quantile(1.0 - alpha / 2.0),
    }


def decide_external_gate(
    games: Sequence[ISSGameOutcome],
    *,
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_SEED,
) -> dict:
    _validate_unique_games(games)
    counts = _arm_counts(games)
    look, next_look = current_look(games)

    if look is None:
        return {
            "schema": SCHEMA,
            "status": "INCOMPLETE",
            "decision": None,
            "counts": counts,
            "current_look_per_arm": None,
            "next_look_per_arm": next_look,
            "accept_authorized": False,
        }

    # Use only the prefix through the current frozen look for each arm.
    selected: list[ISSGameOutcome] = []
    arm_used = {a: 0 for a in ARMS}
    for g in games:
        if arm_used[g.arm] < look:
            selected.append(g)
            arm_used[g.arm] += 1

    estimate = stratified_difference(selected)
    interval = stratified_bootstrap_interval(
        selected, replicates=replicates, seed=seed, confidence=CONFIDENCE
    )

    if interval["low"] > 0.0:
        status = "COMPLETE"
        decision = "ACCEPT"
    elif interval["high"] < 0.0:
        status = "COMPLETE"
        decision = "REJECT"
    elif look == LOOKS_PER_ARM[-1]:
        status = "COMPLETE"
        decision = "INCONCLUSIVE"
    else:
        status = "CONTINUE"
        decision = None

    return {
        "schema": SCHEMA,
        "status": status,
        "decision": decision,
        "counts": counts,
        "games_used": len(selected),
        "current_look_per_arm": look,
        "next_look_per_arm": None if status == "COMPLETE" else next_look,
        "estimate": estimate,
        "interval": interval,
        "multiplicity_control": {
            "family_alpha": FAMILY_ALPHA,
            "looks_per_arm": list(LOOKS_PER_ARM),
            "per_look_alpha": PER_LOOK_ALPHA,
            "two_sided_confidence": CONFIDENCE,
            "method": "Bonferroni across three pre-specified looks",
        },
        "accept_authorized": decision == "ACCEPT",
    }


def load_jsonl(path: Path) -> list[ISSGameOutcome]:
    games = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                games.append(ISSGameOutcome.from_mapping(json.loads(line)))
    return games


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("games_jsonl", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--bootstrap-replicates", type=int, default=DEFAULT_BOOTSTRAP_REPLICATES)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = p.parse_args()
    result = decide_external_gate(
        load_jsonl(args.games_jsonl),
        replicates=args.bootstrap_replicates,
        seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
