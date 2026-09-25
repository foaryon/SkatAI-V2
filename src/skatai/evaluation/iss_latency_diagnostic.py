"""Decision latency summary from a verified ISS effect-journal snapshot.

This diagnostic never reads the game ledger, scores, or contract outcomes.
Only confirmed decisions contribute to latency estimates.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from skatai.iss.effects import ISSEffectJournal, EffectState


def _nearest_rank(values: list[float], fraction: float) -> float:
    values.sort()
    rank = max(1, math.ceil(fraction * len(values)))
    return values[min(rank - 1, len(values) - 1)]


def _metrics(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "p50_ms": _nearest_rank(ordered.copy(), 0.50),
        "p95_ms": _nearest_rank(ordered.copy(), 0.95),
        "p99_ms": _nearest_rank(ordered.copy(), 0.99),
        "max_ms": ordered[-1],
        "at_or_above_60000_ms": sum(x >= 60000 for x in ordered),
    }


def summarize(states: list[EffectState]) -> dict:
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_game: dict[tuple[str, str, str, int], list[EffectState]] = defaultdict(list)
    statuses = Counter(state.status for state in states)
    for state in states:
        if state.status == "CONFIRMED":
            groups[(state.release_id, state.decision_type)].append(
                float(state.latency_ms)
            )
            per_game[
                (state.release_id, state.decision_type, state.table_id, state.game_sequence)
            ].append(state)
    results = []
    for (release_id, decision_type), samples in sorted(groups.items()):
        results.append(
            {
                "release_id": release_id,
                "decision_type": decision_type,
                **_metrics(samples),
            }
        )
    cold_warm: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"first": [], "later": []}
    )
    for (release_id, decision_type, _, _), game_states in per_game.items():
        ordered = sorted(game_states, key=lambda state: state.protocol_sequence)
        cold_warm[(release_id, decision_type)]["first"].append(
            float(ordered[0].latency_ms)
        )
        cold_warm[(release_id, decision_type)]["later"].extend(
            float(state.latency_ms) for state in ordered[1:]
        )
    phase_order = []
    for (release_id, decision_type), split in sorted(cold_warm.items()):
        phase_order.append(
            {
                "release_id": release_id,
                "decision_type": decision_type,
                "first": _metrics(split["first"]),
                "later": _metrics(split["later"]) if split["later"] else None,
            }
        )
    return {
        "effect_status_counts": dict(sorted(statuses.items())),
        "groups": results,
        "first_vs_later_in_game": phase_order,
    }


def analyze(
    journal_path: Path, *, source_commit: str, analysis_source_commit: str
) -> dict:
    # A live journal can append while this diagnostic runs. Bind the hash and
    # parsed states to the same immutable snapshot, without retaining a copy.
    raw = journal_path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="skatai-iss-latency-") as tmp:
        snapshot = Path(tmp) / "effects.jsonl"
        snapshot.write_bytes(raw)
        os.chmod(snapshot, 0o600)
        states = list(ISSEffectJournal(snapshot).states().values())
    return {
        "schema": "skatai.v2.iss-latency-diagnostic.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "analysis_source_commit": analysis_source_commit,
        "effect_journal_sha256": hashlib.sha256(raw).hexdigest(),
        "effect_journal_bytes": len(raw),
        "scope": "Confirmed effect-journal decision timings only; no game outcomes or strength look.",
        "quantile_method": "nearest rank",
        "strength_look_opened": False,
        **summarize(states),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--analysis-source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = analyze(
        args.journal,
        source_commit=args.source_commit,
        analysis_source_commit=args.analysis_source_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, args.output)


if __name__ == "__main__":
    main()
