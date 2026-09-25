"""Bounded, deterministic D1 self-play trajectory pilot; no strength claim."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from skatai.game.bidding import BID_VALUES
from skatai.selfplay.cardplay import RandomLegalPolicy
from skatai.selfplay.trajectory import PHASES, capture_basic_game

CONTRACTS = ("C", "S", "H", "D", "G", "N")


class Bid18:
    def probability_continue(self, observation) -> float:
        return float(BID_VALUES[observation.bid_index] <= 18)


class Declare:
    def __init__(self, contract: str, pickup: bool) -> None:
        self.contract, self.pickup = contract, pickup

    def choose_contract(self, observation) -> str:
        if self.pickup and not observation.picked_up_skat:
            return "PICKUP"
        return self.contract if self.pickup else self.contract + "H"


class DiscardFirstTwo:
    def choose_discard(self, observation) -> tuple[str, str]:
        return observation.hand12[:2]


def one(seed: int, commit: str, script_sha256: str) -> dict:
    contract = CONTRACTS[seed % len(CONTRACTS)]
    pickup = bool((seed // len(CONTRACTS)) % 2)
    ids = {
        "BID": tuple(f"pilot:{script_sha256}:bid18:seat{seat}" for seat in range(3)),
        "DECLARATION": tuple(f"pilot:{script_sha256}:contract{contract}:pickup{int(pickup)}:seat{seat}" for seat in range(3)),
        "DISCARD": tuple(f"pilot:{script_sha256}:first-two:seat{seat}" for seat in range(3)),
        "CARDPLAY": tuple(f"pilot:{script_sha256}:random-legal:seed{1000 * seed + seat}" for seat in range(3)),
    }
    assert set(ids) == set(PHASES)
    trajectory = capture_basic_game(
        seed, source_commit=commit, policy_ids=ids,
        bidding_policies=[Bid18() for _ in range(3)],
        declaration_policies=[Declare(contract, pickup) for _ in range(3)],
        discard_policies=[DiscardFirstTwo() for _ in range(3)],
        cardplay_policies=[RandomLegalPolicy(1000 * seed + seat) for seat in range(3)],
        legal_contracts=(contract, contract + "H"),
    )
    return asdict(trajectory)


def _rows(count: int, commit: str, script_sha256: str):
    for seed in range(count):
        yield (json.dumps(one(seed, commit, script_sha256), sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--count", type=int, default=96)
    args = parser.parse_args()
    if not 1 <= args.count <= 256:
        raise ValueError("COUNT_OUT_OF_BOUNDS")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    script_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output = args.output_prefix.with_suffix(".jsonl")
    manifest = args.output_prefix.with_suffix(".manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    digest = hashlib.sha256()
    with os.fdopen(fd, "wb") as stream:
        for row in _rows(args.count, commit, script_sha256):
            stream.write(row)
            digest.update(row)
        stream.flush()
        os.fsync(stream.fileno())
    second = hashlib.sha256()
    for row in _rows(args.count, commit, script_sha256):
        second.update(row)
    if digest.digest() != second.digest():
        os.unlink(tmp)
        raise RuntimeError("PILOT_NOT_DETERMINISTIC")
    os.replace(tmp, output)
    record = {
        "schema": "skatai.v2.selfplay.trajectory-pilot-manifest.v2",
        "source_commit": commit, "script_sha256": script_sha256,
        "trajectory_schema": "skatai.v2.selfplay.decision-trajectory.v2",
        "records": args.count, "contracts": CONTRACTS,
        "output_file": output.name, "output_bytes": output.stat().st_size,
        "output_sha256": digest.hexdigest(), "second_run_sha256": second.hexdigest(),
        "trust_level": "D1_EXPLORATORY_ONLY",
        "restrictions": ["synthetic controlled self-play", "basic scoring subset only",
                         "no strength claim or authoritative training use"],
    }
    fd, tmp = tempfile.mkstemp(prefix=manifest.name + ".", dir=manifest.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(record, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, manifest)
    print(json.dumps({"records": args.count, "output_sha256": digest.hexdigest(),
                      "manifest": str(manifest)}, sort_keys=True))


if __name__ == "__main__":
    main()
