#!/usr/bin/env python3
"""Build bounded train-only endgame labels from an identified canonical sample."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from skatai.data.bidding import split_for_game
from skatai.data.cardplay import CardplayReconstructionError, reconstruct_cardplay_events
from skatai.evaluation.cardplay_weakness import contract_family
from skatai.evaluation.endgame_labels import label_endgame_observation

SCHEMA = "skatai.v2.train-endgame-teacher-pilot.v1"
MAX_SAMPLE_BYTES = 8 * 1024 * 1024


def build(manifest_path: Path, sample_path: Path, *, per_stratum: int, seed: int):
    if not 1 <= per_stratum <= 32:
        raise ValueError("ENDGAME_PILOT_STRATUM_LIMIT_INVALID")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("schema") != "skatai.v2.canonical-train-range-sample.v1"
            or manifest.get("source_asset_id") != "legacy-v1-canonical-corpus"):
        raise ValueError("ENDGAME_PILOT_SOURCE_MANIFEST_INVALID")
    if sample_path.stat().st_size > MAX_SAMPLE_BYTES:
        raise ValueError("ENDGAME_PILOT_SAMPLE_TOO_LARGE")
    raw = sample_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest.get("sample_sha256"):
        raise ValueError("ENDGAME_PILOT_SAMPLE_HASH_MISMATCH")

    groups = defaultdict(list)
    counts = Counter()
    for line in raw.splitlines():
        game = json.loads(line)
        counts["rows_seen"] += 1
        if split_for_game(game) != "train" or not game.get("cardplay_usable"):
            raise ValueError("ENDGAME_PILOT_NONTRAIN_OR_UNUSABLE_ROW")
        try:
            events = reconstruct_cardplay_events(game)
        except CardplayReconstructionError:
            counts["reconstruction_quarantined"] += 1
            continue
        counts["reconstructed_games"] += 1
        for event in events:
            view = event.observation
            if view.contract not in {"C", "S", "H", "D", "G", "N"}:
                counts["unsupported_contract_events"] += 1
                continue
            if (len(view.played_cards) < 21 or len(view.legal_cards) < 2
                    or view.known_private_cards or view.open_hand_cards):
                continue
            role = "DECLARER" if view.seat == view.declarer else "DEFENDER"
            stratum = (contract_family(view.contract), role)
            rank = hashlib.sha256(
                f"{seed}|{game['raw_sha256']}|{event.play_ordinal}".encode()
            ).hexdigest()
            groups[stratum].append((rank, game, event))

    selected = []
    for stratum in sorted(groups):
        seen = set()
        for rank, game, event in sorted(groups[stratum], key=lambda x: x[0]):
            if game["raw_sha256"] in seen:
                continue
            seen.add(game["raw_sha256"])
            selected.append((stratum, game, event))
            if len(seen) == per_stratum:
                break

    rows = []
    identities = []
    for stratum, game, event in selected:
        hands = [list(hand) for hand in game["initial_hands"]]
        declarer = int(game["declarer"])
        if not game["is_hand"]:
            hands[declarer].extend(game["skat_initial"])
            for card in game["discards"]:
                hands[declarer].remove(card)
        row = label_endgame_observation(
            event.observation, post_declaration_hands=hands,
        )
        rows.append(row)
        identities.append({
            "raw_sha256": game["raw_sha256"],
            "game_id": str(game["game_id"]),
            "play_ordinal": int(event.play_ordinal),
            "stratum": list(stratum),
        })
    output = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for row in rows
    )
    evidence = {
        "schema": SCHEMA,
        "source_asset_id": manifest["source_asset_id"],
        "source_sample_sha256": manifest["sample_sha256"],
        "selection_seed": seed,
        "per_stratum": per_stratum,
        "selection_rule": "late game, legal choice, no private or open-hand fields; one event per raw game per family/role stratum",
        "counts": dict(sorted(counts.items())),
        "labels": len(rows),
        "selected_identities": identities,
        "rows_sha256": hashlib.sha256(output).hexdigest(),
        "training_approved": False,
        "strength_claim_authorized": False,
    }
    return output, evidence


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--per-stratum", type=int, default=12)
    p.add_argument("--seed", type=int, default=20260925)
    args = p.parse_args()
    output, evidence = build(
        args.manifest, args.sample, per_stratum=args.per_stratum,
        seed=args.seed,
    )
    args.output.write_bytes(output)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"labels": evidence["labels"], "rows_sha256": evidence["rows_sha256"]}))


if __name__ == "__main__":
    main()
