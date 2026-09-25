"""Generate paired private replay and single-seat learner artifacts."""

from __future__ import annotations

from dataclasses import asdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

from run_basic_trajectory_pilot import Bid18, CONTRACTS, Declare, DiscardFirstTwo
from skatai.selfplay.cardplay import RandomLegalPolicy
from skatai.selfplay.trajectory import PHASES, capture_basic_game, declarer_learner_view


def _atomic_json(path: Path, payload: object, mode: int = 0o644) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    os.fchmod(fd, mode)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _private_inputs(path: Path, *, count: int, commit: str, script_hash: str) -> dict:
    if path.exists():
        value = json.loads(path.read_text())
        if (value.get("source_commit") != commit or value.get("script_sha256") != script_hash
                or len(value.get("games", [])) != count):
            raise ValueError("EXISTING_PRIVATE_INPUT_IDENTITY_MISMATCH")
        return value
    value = {
        "schema": "skatai.v2.selfplay.private-pilot-input.v1",
        "source_commit": commit, "script_sha256": script_hash,
        "games": [{"ordinal": i, "deal_seed": secrets.randbits(128),
                   "cardplay_rng_seed": secrets.randbits(128)} for i in range(count)],
    }
    _atomic_json(path, value, mode=0o600)
    return value


def _rows(games: list[dict], commit: str, script_hash: str):
    public_ids = {phase: f"pilot-v3:{script_hash}:{phase.lower()}" for phase in PHASES}
    for item in games:
        ordinal = item["ordinal"]
        seed = item["deal_seed"]
        rng_seed = item["cardplay_rng_seed"]
        contract = CONTRACTS[ordinal % len(CONTRACTS)]
        pickup = bool((ordinal // len(CONTRACTS)) % 2)
        raw_ids = {phase: (public_ids[phase],) * 3 for phase in PHASES}
        raw = capture_basic_game(
            seed, source_commit=commit, policy_ids=raw_ids,
            bidding_policies=[Bid18() for _ in range(3)],
            declaration_policies=[Declare(contract, pickup) for _ in range(3)],
            discard_policies=[DiscardFirstTwo() for _ in range(3)],
            cardplay_policies=[RandomLegalPolicy(rng_seed + seat) for seat in range(3)],
            legal_contracts=(contract, contract + "H"),
        )
        learner = declarer_learner_view(raw, policy_family_ids=public_ids)
        raw_bytes = (json.dumps(asdict(raw), sort_keys=True, separators=(",", ":")) + "\n").encode()
        learner_bytes = (json.dumps(asdict(learner), sort_keys=True, separators=(",", ":")) + "\n").encode()
        yield raw_bytes, learner_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=96)
    args = parser.parse_args()
    if not 1 <= args.count <= 256:
        raise ValueError("COUNT_OUT_OF_BOUNDS")
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    args.private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.private_dir, 0o700)
    if args.private_dir.stat().st_mode & 0o077:
        raise ValueError("PRIVATE_DIR_PERMISSIONS_NOT_ENFORCED")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    private_path = args.private_dir / (args.output_prefix.name + ".private-seeds.json")
    inputs = _private_inputs(private_path, count=args.count, commit=commit, script_hash=script_hash)
    if private_path.stat().st_mode & 0o077:
        raise ValueError("PRIVATE_SEED_FILE_PERMISSIONS_NOT_ENFORCED")
    output_paths = (args.private_dir / (args.output_prefix.name + ".raw.jsonl"),
                    args.output_prefix.with_suffix(".learner.jsonl"))
    fds_and_tmps = [tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
                    for path in output_paths]
    digests = [hashlib.sha256(), hashlib.sha256()]
    streams = [os.fdopen(fd, "wb") for fd, _ in fds_and_tmps]
    try:
        for pair in _rows(inputs["games"], commit, script_hash):
            for i, payload in enumerate(pair):
                streams[i].write(payload)
                digests[i].update(payload)
        for stream in streams:
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        for stream in streams:
            stream.close()
    second = [hashlib.sha256(), hashlib.sha256()]
    for pair in _rows(inputs["games"], commit, script_hash):
        for i, payload in enumerate(pair):
            second[i].update(payload)
    if any(digests[i].digest() != second[i].digest() for i in range(2)):
        raise RuntimeError("NONDETERMINISTIC_PRIVATE_SEED_PILOT")
    for (_, tmp), path in zip(fds_and_tmps, output_paths):
        os.replace(tmp, path)
    if output_paths[0].stat().st_mode & 0o077:
        raise ValueError("PRIVATE_REPLAY_PERMISSIONS_NOT_ENFORCED")
    manifest = {
        "schema": "skatai.v2.selfplay.private-seed-pilot-manifest.v1",
        "source_commit": commit, "script_sha256": script_hash,
        "count": args.count,
        "private_seed_file": private_path.name,
        "private_seed_sha256": hashlib.sha256(private_path.read_bytes()).hexdigest(),
        "raw_file": output_paths[0].name, "raw_sha256": digests[0].hexdigest(),
        "learner_file": output_paths[1].name, "learner_sha256": digests[1].hexdigest(),
        "second_run_sha256": [x.hexdigest() for x in second],
        "learner_scope": "declarer-only decision-time views; no deal seed, deal hash, game key or opponent private observation",
        "trust_level": "D1_EXPLORATORY_ONLY",
        "restrictions": ["private seed and raw replay files are not training inputs",
                         "basic contracts only", "no strength claim"],
    }
    path = args.output_prefix.with_suffix(".manifest.json")
    _atomic_json(path, manifest)
    learner_manifest = {
        "schema": "skatai.v2.selfplay.learner-seat-pilot-manifest.v1",
        "source_commit": commit, "count": args.count,
        "learner_schema": "skatai.v2.selfplay.learner-seat.v1",
        "learner_file": output_paths[1].name,
        "learner_sha256": digests[1].hexdigest(),
        "trust_level": "D1_EXPLORATORY_ONLY",
        "restrictions": ["declarer-only views", "basic contract subset",
                         "not approved for policy training or strength evaluation"],
    }
    _atomic_json(args.output_prefix.with_suffix(".learner-manifest.json"), learner_manifest)
    print(json.dumps({"count": args.count, "learner_sha256": manifest["learner_sha256"],
                      "manifest": str(path)}, sort_keys=True))


if __name__ == "__main__":
    main()
