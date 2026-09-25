from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Iterable

from skatai.iss.effects import ISSEffectJournal


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"BAD_JSONL:{path.name}:{line_no}") from exc
            if not isinstance(raw, dict):
                raise RuntimeError(f"NONOBJECT_JSONL:{path.name}:{line_no}")
            out.append(raw)
    return out


def _percentile(values: Iterable[float], p: float) -> float | None:
    xs = sorted(float(x) for x in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    index = (len(xs) - 1) * float(p)
    lo = math.floor(index)
    hi = math.ceil(index)
    if lo == hi:
        return xs[lo]
    frac = index - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _effect_game_id(event: dict[str, Any]) -> str | None:
    direct = event.get("game_id")
    if direct:
        return str(direct)
    table_id = event.get("table_id")
    game_sequence = event.get("game_sequence")
    if table_id is None or not isinstance(game_sequence, (int, float)):
        return None
    return f"iss:{table_id}:{int(game_sequence)}"


def _summary(values: Iterable[float]) -> dict[str, float | int | None]:
    xs = [float(x) for x in values]
    return {
        "n": len(xs),
        "min": min(xs) if xs else None,
        "median": statistics.median(xs) if xs else None,
        "p95": _percentile(xs, 0.95),
        "p99": _percentile(xs, 0.99),
        "max": max(xs) if xs else None,
    }


def _active_count(runtime: Path) -> int:
    path = runtime / "active-games.json"
    if not path.is_file():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    games = raw.get("games")
    if not isinstance(games, list):
        raise RuntimeError("ACTIVE_GAMES_NOT_LIST")
    return len(games)


def _table_slot_count(runtime: Path) -> int:
    path = runtime / "table-slots.json"
    if not path.is_file():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    slots = raw.get("slots")
    if not isinstance(slots, list):
        raise RuntimeError("TABLE_SLOTS_NOT_LIST")
    return len(slots)


def collect(runtime: Path, *, recent_games: int = 50) -> dict[str, Any]:
    runtime = Path(runtime)
    ledger = _jsonl(runtime / "gate-ledger.jsonl")
    scored = [
        row for row in ledger
        if (row.get("status") or row.get("classification")) == "SCORED"
        and isinstance(row.get("recorded_unix_ns"), (int, float))
    ]
    scored.sort(key=lambda row: int(row["recorded_unix_ns"]))
    recent = scored[-max(1, int(recent_games)) :]

    gaps: list[float] = []
    for left, right in zip(recent, recent[1:]):
        gap = (int(right["recorded_unix_ns"]) - int(left["recorded_unix_ns"])) / 1e9
        if gap >= 0:
            gaps.append(gap)
    recent_rate = None
    if len(recent) >= 2:
        span = (
            int(recent[-1]["recorded_unix_ns"])
            - int(recent[0]["recorded_unix_ns"])
        ) / 1e9
        if span > 0:
            recent_rate = (len(recent) - 1) * 3600.0 / span

    ids = [str(row.get("game_id")) for row in ledger if row.get("game_id")]
    duplicate_game_ids = len(ids) - len(set(ids))

    effects_path = runtime / "effects.jsonl"
    effect_events: list[dict[str, Any]] = []
    pending_effects = 0
    effect_chain_valid = True
    effect_error = None
    if effects_path.exists():
        journal = ISSEffectJournal(effects_path)
        try:
            effect_events = journal.events()
            pending_effects = len(journal.pending())
        except Exception as exc:
            effect_chain_valid = False
            effect_error = f"{type(exc).__name__}:{exc}"

    latencies: dict[str, list[float]] = {}
    recent_latencies: dict[str, list[float]] = {}
    recent_game_keys: set[tuple[str, int]] = set()
    recent_game_ids = {
        str(row.get("game_id")) for row in recent if row.get("game_id")
    }
    recent_evidence_missing = 0
    for row in recent:
        game_id = row.get("game_id")
        if not game_id:
            continue
        evidence_path = runtime / "games" / f"{game_id}.evidence.json"
        if evidence_path.is_file():
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            table_id = evidence.get("table_id")
            game_sequence = evidence.get("server_game_num")
            if table_id is not None and isinstance(game_sequence, int):
                recent_game_keys.add((str(table_id), int(game_sequence)))
                continue
        # Legacy R9 does not always have the newer per-game evidence file.
        # Its canonical game id already contains the same table/sequence
        # authority, so preserve a safe fallback instead of dropping latency.
        recent_evidence_missing += 1

    for event in effect_events:
        if event.get("event") != "INTENT":
            continue
        latency = event.get("latency_ms")
        if not isinstance(latency, (int, float)):
            continue
        decision_type = str(event.get("decision_type") or "UNKNOWN")
        latencies.setdefault(decision_type, []).append(float(latency))
        event_key = (
            str(event.get("table_id") or ""),
            int(event.get("game_sequence"))
            if isinstance(event.get("game_sequence"), int)
            else -1,
        )
        if (
            event_key in recent_game_keys
            or _effect_game_id(event) in recent_game_ids
        ):
            recent_latencies.setdefault(decision_type, []).append(
                float(latency)
            )

    receipts: list[dict[str, Any]] = []
    receipt_dir = runtime / "mirror-receipts"
    receipt_tracking_present = receipt_dir.is_dir()
    if receipt_tracking_present:
        for path in sorted(receipt_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                receipts.append(raw)
    mirror_lags = [
        float(row["lag_s"])
        for row in receipts
        if isinstance(row.get("lag_s"), (int, float))
    ]
    receipt_ids = {
        str(row.get("game_id")) for row in receipts if row.get("game_id")
    }
    scored_ids = {
        str(row.get("game_id")) for row in scored if row.get("game_id")
    }

    mirror_status = None
    mirror_status_path = runtime / "mirror-status.json"
    if mirror_status_path.is_file():
        mirror_status = json.loads(
            mirror_status_path.read_text(encoding="utf-8")
        )

    connection_events = _jsonl(runtime / "connection-events.jsonl")
    connection_event_counts: dict[str, int] = {}
    for row in connection_events:
        name = str(row.get("event") or "UNKNOWN")
        connection_event_counts[name] = connection_event_counts.get(name, 0) + 1

    epoch = None
    epoch_path = runtime / "deployment-epoch.json"
    if epoch_path.is_file():
        epoch = json.loads(epoch_path.read_text(encoding="utf-8"))

    arm_counts: dict[str, int] = {}
    for row in scored:
        arm = str(row.get("arm") or "UNKNOWN")
        arm_counts[arm] = arm_counts.get(arm, 0) + 1

    status_counts: dict[str, int] = {}
    source_commits: set[str] = set()
    for row in ledger:
        status = str(
            row.get("status") or row.get("classification") or "UNKNOWN"
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        source_commit = row.get("source_commit")
        if source_commit:
            source_commits.add(str(source_commit))

    return {
        "schema": "skatai.v2.iss-throughput-metrics.v1",
        "captured_unix_ns": time.time_ns(),
        "runtime": str(runtime),
        "source_commits": sorted(source_commits),
        "deployment_epoch": epoch,
        "games": {
            "ledger_rows": len(ledger),
            "scored": len(scored),
            "scored_by_arm": arm_counts,
            "status_counts": status_counts,
            "duplicate_game_ids": duplicate_game_ids,
            "recent_window": len(recent),
            "recent_games_per_hour": recent_rate,
            "recent_completion_gap_s": _summary(gaps),
        },
        "decision_latency_ms": {
            key: _summary(values) for key, values in sorted(latencies.items())
        },
        "recent_decision_latency_ms": {
            "recent_game_keys": len(recent_game_keys),
            "recent_evidence_missing": recent_evidence_missing,
            "by_type": {
                key: _summary(values)
                for key, values in sorted(recent_latencies.items())
            },
        },
        "effects": {
            "chain_valid": effect_chain_valid,
            "error": effect_error,
            "pending": pending_effects,
            "events": len(effect_events),
        },
        "authority": {
            "active_games": _active_count(runtime),
            "table_slots": _table_slot_count(runtime),
        },
        "mirror": {
            "receipt_tracking_present": receipt_tracking_present,
            "receipts": len(receipts),
            "lag_s": _summary(mirror_lags),
            "scored_games_without_receipt": (
                len(scored_ids - receipt_ids)
                if receipt_tracking_present
                else None
            ),
            "status": mirror_status,
        },
        "connection_events": connection_event_counts,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--recent-games", type=int, default=50)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    result = collect(args.runtime, recent_games=args.recent_games)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.output.with_suffix(args.output.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(args.output)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
