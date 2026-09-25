from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Iterable, Sequence

from skatai.game.rules import game_type_from_contract


def contract_family(contract: str) -> str:
    game_type = game_type_from_contract(contract)
    if game_type in {"CLUBS", "SPADES", "HEARTS", "DIAMONDS"}:
        return "SUIT"
    if game_type == "GRAND":
        return "GRAND"
    if game_type == "NULL":
        return "NULL"
    raise ValueError(f"UNSUPPORTED_CARDPLAY_FAMILY:{game_type}")


def play_phase(play_ordinal: int) -> str:
    ordinal = int(play_ordinal)
    if ordinal < 0:
        raise ValueError(f"NEGATIVE_PLAY_ORDINAL:{ordinal}")
    if ordinal < 10:
        return "EARLY"
    if ordinal < 20:
        return "MIDDLE"
    return "END"


def lead_position(event: Any) -> str:
    return "LEAD" if len(event.observation.current_trick) == 0 else "FOLLOW"


def choice_type(event: Any) -> str:
    return "FORCED" if len(event.observation.legal_cards) == 1 else "CHOICE"


def event_stratum(event: Any) -> tuple[str, str, str]:
    observation = event.observation
    role = "DECLARER" if int(observation.seat) == int(observation.declarer) else "DEFENDER"
    return (
        contract_family(str(observation.contract)),
        role,
        play_phase(int(event.play_ordinal)),
    )


def _event_rank(event: Any, *, seed: int) -> tuple[str, str, int]:
    identity = f"{int(seed)}|{event.game_id}|{int(event.play_ordinal)}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return digest, str(event.game_id), int(event.play_ordinal)


def deterministic_balanced_sample(
    events: Sequence[Any] | Iterable[Any],
    *,
    per_stratum: int,
    seed: int,
) -> tuple[Any, ...]:
    limit = int(per_stratum)
    if limit < 1:
        raise ValueError("PER_STRATUM_MUST_BE_POSITIVE")

    grouped: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for event in events:
        grouped[event_stratum(event)].append(event)

    selected: list[tuple[tuple[str, str, str], tuple[str, str, int], Any]] = []
    for stratum in sorted(grouped):
        ranked = sorted(
            grouped[stratum],
            key=lambda event: _event_rank(event, seed=seed),
        )
        distinct_games: set[str] = set()
        for event in ranked:
            game_id = str(event.game_id)
            if game_id in distinct_games:
                continue
            distinct_games.add(game_id)
            selected.append((stratum, _event_rank(event, seed=seed), event))
            if len(distinct_games) == limit:
                break

    selected.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in selected)



def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(fraction)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _summarize_group(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    agreed = sum(bool(row["agreement"]) for row in rows)
    latencies = [float(row["latency_ms"]) for row in rows]
    legal_counts = [int(row["legal_count"]) for row in rows]
    return {
        "n": n,
        "agreement_count": agreed,
        "agreement_rate": (agreed / n) if n else None,
        "mean_legal_count": (sum(legal_counts) / n) if n else None,
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
    }


def _group_summary(
    rows: Sequence[dict[str, Any]],
    key: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key, "UNKNOWN"))].append(row)
    return {
        value: _summarize_group(grouped[value])
        for value in sorted(grouped)
    }


def summarize_agreement(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    records = list(rows)
    compound: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        key = "|".join(
            (
                str(row["family"]),
                str(row["role"]),
                str(row["phase"]),
            )
        )
        compound[key].append(row)
    return {
        "overall": _summarize_group(records),
        "by_family": _group_summary(records, "family"),
        "by_role": _group_summary(records, "role"),
        "by_phase": _group_summary(records, "phase"),
        "by_seat": _group_summary(records, "seat"),
        "by_lead_position": _group_summary(records, "lead_position"),
        "by_choice_type": _group_summary(records, "choice_type"),
        "by_actor_class": _group_summary(records, "actor_class"),
        "by_stratum": {
            key: _summarize_group(compound[key])
            for key in sorted(compound)
        },
    }
