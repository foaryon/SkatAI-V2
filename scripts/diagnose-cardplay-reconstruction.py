#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from skatai.data.cardplay import reconstruct_cardplay_events


SCHEMA = "skatai.v2.cardplay-reconstruction-diagnostic.v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def diagnose(path: Path, *, max_rows: int | None = None) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    total_events = 0

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if max_rows is not None and counts["rows_seen"] >= max_rows:
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
                events = reconstruct_cardplay_events(game)
            except Exception as exc:
                counts["reconstruction_failed"] += 1
                errors[str(exc).split(":", 1)[0]] += 1
                continue

            counts["reconstruction_ok"] += 1
            total_events += len(events)
            if len(events) == 30:
                counts["complete_30"] += 1
            else:
                counts["partial"] += 1

    return {
        "schema": SCHEMA,
        "input": {
            "path": str(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "max_rows": max_rows,
        },
        "counts": dict(sorted(counts.items())),
        "events": total_events,
        "errors": dict(sorted(errors.items())),
        "interpretation": {
            "purpose": "bounded implementation/semantic reconstruction diagnostic",
            "training_authority": False,
            "strength_claim_authorized": False,
            "d4_usefulness_claim_authorized": False,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--max-rows", type=int)
    args = p.parse_args()

    payload = diagnose(args.input, max_rows=args.max_rows)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    main()
