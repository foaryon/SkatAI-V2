"""Freeze a small, learner-facing split without publishing private deal keys.

Private seed JSON arrives on stdin. The output contains row ordinals only and
does not approve the exploratory pilot for policy training or strength tests.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from skatai.selfplay.cardplay import make_deal
from skatai.selfplay.learner_dataset import load_bounded_learner_pilot

SCHEMA = "skatai.v2.selfplay.private-seed-split.v1"
DOMAIN = b"skatai.v2.private-pilot-split.v1\0"


def assign_split(games: list[dict], rows: list[dict]) -> dict[str, list[int]]:
    """Give each of twelve eight-row contract cells a 6/1/1 split."""
    if len(games) != len(rows) or len(rows) != 96:
        raise ValueError("SPLIT_REQUIRES_96_ALIGNED_ROWS")
    groups: dict[str, list[tuple[bytes, int]]] = defaultdict(list)
    seeds, rng_seeds, deal_ids = set(), set(), set()
    for ordinal, (game, row) in enumerate(zip(games, rows)):
        seed, rng = game.get("deal_seed"), game.get("cardplay_rng_seed")
        if (game.get("ordinal") != ordinal or type(seed) is not int
                or type(rng) is not int or not 2**96 <= seed < 2**128
                or not 2**96 <= rng < 2**128):
            raise ValueError("PRIVATE_SPLIT_INPUT_IDENTITY_INVALID")
        deal_id = make_deal(seed).identity_sha256
        if seed in seeds or rng in rng_seeds or deal_id in deal_ids:
            raise ValueError("PRIVATE_SPLIT_DUPLICATE_DEAL_OR_RNG")
        seeds.add(seed)
        rng_seeds.add(rng)
        deal_ids.add(deal_id)
        rank = hashlib.sha256(DOMAIN + seed.to_bytes(16, "big")).digest()
        groups[row["contract"]].append((rank, ordinal))
    if len(groups) != 12 or any(len(group) != 8 for group in groups.values()):
        raise ValueError("PRIVATE_SPLIT_CONTRACT_BALANCE_INVALID")
    parts: dict[str, list[int]] = {"train": [], "validation": [], "test": []}
    for contract in sorted(groups):
        ordered = [ordinal for _, ordinal in sorted(groups[contract])]
        parts["train"].extend(ordered[:6])
        parts["validation"].append(ordered[6])
        parts["test"].append(ordered[7])
    parts = {name: sorted(indices) for name, indices in parts.items()}
    if ([len(parts[name]) for name in ("train", "validation", "test")] != [72, 12, 12]
            or sorted(index for indices in parts.values() for index in indices) != list(range(96))):
        raise ValueError("PRIVATE_SPLIT_PARTITION_INVALID")
    return parts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-manifest", type=Path, required=True)
    parser.add_argument("--learner-manifest", type=Path, required=True)
    parser.add_argument("--learner-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private_manifest = json.loads(args.private_manifest.read_text())
    learner_manifest = json.loads(args.learner_manifest.read_text())
    private_bytes = sys.stdin.buffer.read(1_000_001)
    if len(private_bytes) > 1_000_000:
        raise ValueError("PRIVATE_SPLIT_INPUT_TOO_LARGE")
    if (private_manifest.get("schema") != "skatai.v2.selfplay.private-seed-pilot-manifest.v1"
            or hashlib.sha256(private_bytes).hexdigest() != private_manifest.get("private_seed_sha256")
            or private_manifest.get("learner_sha256") != learner_manifest.get("learner_sha256")
            or private_manifest.get("source_commit") != learner_manifest.get("source_commit")
            or private_manifest.get("count") != 96
            or learner_manifest.get("count") != 96):
        raise ValueError("PRIVATE_SPLIT_MANIFEST_IDENTITY_MISMATCH")
    private = json.loads(private_bytes)
    if (private.get("schema") != "skatai.v2.selfplay.private-pilot-input.v1"
            or private.get("source_commit") != private_manifest["source_commit"]
            or private.get("script_sha256") != private_manifest["script_sha256"]):
        raise ValueError("PRIVATE_SPLIT_PRODUCER_IDENTITY_MISMATCH")
    rows = load_bounded_learner_pilot(args.learner_manifest, args.learner_data)
    parts = assign_split(private["games"], rows)
    result = {
        "schema": SCHEMA,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "splitter_source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "producer_source_commit": private_manifest["source_commit"],
        "private_manifest_sha256": hashlib.sha256(args.private_manifest.read_bytes()).hexdigest(),
        "private_seed_sha256": private_manifest["private_seed_sha256"],
        "learner_manifest_sha256": hashlib.sha256(args.learner_manifest.read_bytes()).hexdigest(),
        "learner_sha256": learner_manifest["learner_sha256"],
        "partition": parts,
        "counts": {name: len(indices) for name, indices in parts.items()},
        "contract_balance": "12 observed contract cells, eight records each; 6 train, 1 validation, 1 test per cell",
        "private_uniqueness_verified": "96 distinct high-entropy deal seeds, RNG seeds and deal identities",
        "selection": "Within each contract cell, rank SHA256(domain || private 128-bit deal seed); publish only sorted learner row ordinals",
        "trust_level": "D1_EXPLORATORY_ONLY",
        "restrictions": ["No policy training or strength evaluation approval",
                         "Private seeds, RNG seeds, deal hashes and ranking digests stay outside learner artifacts"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=args.output.name + ".", dir=args.output.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, args.output)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print(json.dumps({"counts": result["counts"], "trust_level": result["trust_level"]}, sort_keys=True))


if __name__ == "__main__":
    main()
