#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from skatai.data.cardplay import CardplayReconstructionError, reconstruct_cardplay_events
from skatai.evaluation.cardplay_weakness import (
    deterministic_balanced_sample,
    event_stratum,
    summarize_agreement,
)
from skatai.runtime.skatzero_backend import FrozenB0CardplayPolicy


SCHEMA = "skatai.v2.b0-historical-cardplay-agreement-diagnostic.v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_events(path: Path, *, max_rows: int) -> tuple[list[Any], dict[str, Any]]:
    events: list[Any] = []
    counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if counts["rows_seen"] >= max_rows:
                break
            counts["rows_seen"] += 1
            try:
                game = json.loads(line)
            except json.JSONDecodeError:
                counts["json_error"] += 1
                errors["JSON_DECODE"] += 1
                continue
            if not game.get("cardplay_usable"):
                counts["not_cardplay_usable"] += 1
                continue

            counts["candidate_games"] += 1
            try:
                reconstructed = reconstruct_cardplay_events(game)
            except CardplayReconstructionError as exc:
                counts["reconstruction_failed"] += 1
                errors[str(exc).split(":", 1)[0]] += 1
                continue
            counts["reconstruction_ok"] += 1
            events.extend(reconstructed)

    return events, {
        "counts": dict(sorted(counts.items())),
        "reconstruction_errors": dict(sorted(errors.items())),
        "event_count": len(events),
    }


def run_diagnostic(
    *,
    input_path: Path,
    max_rows: int,
    per_stratum: int,
    seed: int,
    skatzero_root: Path,
    skatzero_python: Path,
) -> dict[str, Any]:
    events, reconstruction = load_events(input_path, max_rows=max_rows)
    selected = deterministic_balanced_sample(
        events,
        per_stratum=per_stratum,
        seed=seed,
    )
    selection_identity = [
        {
            "game_id": str(event.game_id),
            "play_ordinal": int(event.play_ordinal),
            "stratum": list(event_stratum(event)),
        }
        for event in selected
    ]

    policy = FrozenB0CardplayPolicy(skatzero_root, skatzero_python)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for event in selected:
        family, role, phase = event_stratum(event)
        observation = event.observation
        started = time.perf_counter()
        try:
            prediction = policy.play_card(observation)
        except Exception as exc:
            failures.append(
                {
                    "game_id": str(event.game_id),
                    "play_ordinal": int(event.play_ordinal),
                    "family": family,
                    "role": role,
                    "phase": phase,
                    "seat": int(observation.seat),
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000.0
        if prediction not in observation.legal_cards:
            raise RuntimeError(
                f"B0_DIAGNOSTIC_ILLEGAL_PREDICTION:{event.game_id}:"
                f"{event.play_ordinal}:{prediction}"
            )
        rows.append(
            {
                "game_id": str(event.game_id),
                "source": str(event.source),
                "play_ordinal": int(event.play_ordinal),
                "family": family,
                "role": role,
                "phase": phase,
                "seat": int(observation.seat),
                "declarer": int(observation.declarer),
                "contract": str(observation.contract),
                "target_card": str(event.target_card),
                "prediction": str(prediction),
                "agreement": prediction == event.target_card,
                "legal_count": len(observation.legal_cards),
                "latency_ms": latency_ms,
            }
        )

    return {
        "schema": SCHEMA,
        "input": {
            "path": str(input_path),
            "sha256": sha256_file(input_path),
            "bytes": input_path.stat().st_size,
            "max_rows": max_rows,
        },
        "selection": {
            "seed": seed,
            "per_stratum": per_stratum,
            "selected_events": len(selected),
            "identity_sha256": canonical_sha256(selection_identity),
            "identities": selection_identity,
        },
        "reconstruction": reconstruction,
        "inference": {
            "successful_events": len(rows),
            "failed_events": len(failures),
            "failures": failures,
        },
        "agreement": summarize_agreement(rows),
        "rows": rows,
        "interpretation": {
            "purpose": "bounded B0 cardplay weakness diagnostic against historical actions",
            "historical_agreement_is_strength_metric": False,
            "historical_action_is_ground_truth": False,
            "training_authority": False,
            "strength_claim_authorized": False,
            "d4_usefulness_claim_authorized": False,
            "selection_is_balanced_diagnostic_not_population_estimate": True,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--max-rows", type=int, default=5000)
    p.add_argument("--per-stratum", type=int, default=4)
    p.add_argument("--seed", type=int, default=20260924)
    p.add_argument("--skatzero-root", type=Path, default=Path("/tmp/skatai-v2-b0"))
    p.add_argument(
        "--skatzero-python",
        type=Path,
        default=Path("/tmp/skatai-v2-b0-venv/bin/python"),
    )
    args = p.parse_args()

    payload = run_diagnostic(
        input_path=args.input,
        max_rows=args.max_rows,
        per_stratum=args.per_stratum,
        seed=args.seed,
        skatzero_root=args.skatzero_root,
        skatzero_python=args.skatzero_python,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
