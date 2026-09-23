from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

ARMS = ("B0", "B1")
DEFAULT_OPPONENTS = ("kermit", "zoot", "theCount")
SEATS = (0, 1, 2)
LOOKS_PER_ARM = (300, 1000, 3000)
SCHEMA = "skatai.v2.external-iss-campaign-quota.v1"


@dataclass(frozen=True, order=True)
class Stratum:
    arm: str
    opponent: str
    seat: int


def target_quotas(
    per_arm: int,
    *,
    opponents: Sequence[str] = DEFAULT_OPPONENTS,
) -> dict[Stratum, int]:
    if per_arm <= 0:
        raise ValueError("PER_ARM_MUST_BE_POSITIVE")
    opponents = tuple(str(x) for x in opponents)
    if not opponents or len(set(opponents)) != len(opponents):
        raise ValueError("BAD_OPPONENT_SET")
    base_strata = [(opponent, seat) for opponent in opponents for seat in SEATS]
    q, r = divmod(per_arm, len(base_strata))
    out: dict[Stratum, int] = {}
    for arm in ARMS:
        for i, (opponent, seat) in enumerate(base_strata):
            out[Stratum(arm, opponent, seat)] = q + (1 if i < r else 0)
    return out


def observed_counts(rows: Iterable[Mapping[str, object]]) -> Counter[Stratum]:
    c: Counter[Stratum] = Counter()
    seen: set[str] = set()
    for row in rows:
        game_id = str(row["game_id"])
        if game_id in seen:
            raise ValueError(f"DUPLICATE_GAME_ID:{game_id}")
        seen.add(game_id)
        arm = str(row["arm"])
        opponent = str(row["opponent"])
        seat = int(row["seat"])
        if arm not in ARMS:
            raise ValueError(f"BAD_ARM:{arm}")
        if seat not in SEATS:
            raise ValueError(f"BAD_SEAT:{seat}")
        c[Stratum(arm, opponent, seat)] += 1
    return c


def quota_status(
    rows: Iterable[Mapping[str, object]],
    *,
    per_arm: int,
    opponents: Sequence[str] = DEFAULT_OPPONENTS,
) -> dict:
    targets = target_quotas(per_arm, opponents=opponents)
    counts = observed_counts(rows)
    items = []
    complete = True
    for stratum in sorted(targets):
        target = targets[stratum]
        observed = counts[stratum]
        remaining = max(0, target - observed)
        complete &= remaining == 0
        items.append({
            "arm": stratum.arm,
            "opponent": stratum.opponent,
            "seat": stratum.seat,
            "target": target,
            "observed": observed,
            "remaining": remaining,
        })
    arm_counts = {
        arm: sum(counts[s] for s in counts if s.arm == arm)
        for arm in ARMS
    }
    return {
        "schema": SCHEMA,
        "per_arm_target": per_arm,
        "complete": complete,
        "arm_counts": arm_counts,
        "strata": items,
    }


def next_targets(
    rows: Iterable[Mapping[str, object]],
    *,
    per_arm: int,
    opponents: Sequence[str] = DEFAULT_OPPONENTS,
) -> list[dict]:
    """Return underfilled strata in deterministic priority order.

    Lower arm total is prioritized first, then larger relative quota deficit,
    then B0 before B1 and the canonical opponent/seat order. The caller may
    choose the first target that is actually available on ISS.
    """
    rows = list(rows)
    targets = target_quotas(per_arm, opponents=opponents)
    counts = observed_counts(rows)
    arm_totals = {arm: sum(v for s, v in counts.items() if s.arm == arm) for arm in ARMS}
    opponent_rank = {name: i for i, name in enumerate(opponents)}
    candidates = []
    for s, target in targets.items():
        observed = counts[s]
        if observed >= target:
            continue
        deficit_fraction = (target - observed) / target
        candidates.append((
            arm_totals[s.arm],
            -deficit_fraction,
            ARMS.index(s.arm),
            opponent_rank[s.opponent],
            s.seat,
            s,
            target,
            observed,
        ))
    candidates.sort()
    return [
        {
            "arm": s.arm,
            "opponent": s.opponent,
            "seat": s.seat,
            "target": target,
            "observed": observed,
            "remaining": target - observed,
        }
        for *_, s, target, observed in candidates
    ]
