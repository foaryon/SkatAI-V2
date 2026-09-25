"""Map bounded train-only HS oracle cases to clean V2 raw-SGF identities.

Read compressed public ISS SGF from stdin; no archive is staged locally.
Legacy semantic hashes are retained as lineage, never assumed to be V2 IDs.
"""

from __future__ import annotations

import argparse
import bz2
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from skatai.data.bidding import split_for_game
from skatai.data.sgf import parse_sgf_line, semantic_identity

SCHEMA = "skatai.v2.evidence.announced-hs-raw-identity-audit.v1"
CORE_FIELDS = (
    "players", "initial_hands", "skat_initial", "bidding_history",
    "declarer", "bid_level", "announcement", "game_type", "is_hand",
    "is_ouvert", "discards", "plays", "game_value", "card_points", "matadors",
)


def target_records(canonical: Path, oracle_paths: list[Path], limit: int) -> dict:
    identities = {
        row["identity"]
        for path in oracle_paths
        for row in json.loads(path.read_text())["results"]
    }
    if not identities or len(identities) > 256 or not 1 <= limit <= 400_000:
        raise ValueError("BAD_BOUNDED_TARGET_SET")
    targets = {}
    with canonical.open("rb") as stream:
        for ordinal in range(limit):
            raw = stream.readline()
            if not raw:
                raise ValueError("CANONICAL_PREFIX_TOO_SHORT")
            record = json.loads(raw)
            if record.get("semantic_sha256") not in identities:
                continue
            if split_for_game(record) != "train":
                raise ValueError("TARGET_OUTSIDE_FROZEN_TRAIN_SPLIT")
            key = record["raw_sha256"]
            if key in targets:
                raise ValueError("DUPLICATE_TARGET_RAW_HASH")
            targets[key] = (ordinal, record)
    if len(targets) != len(identities):
        raise ValueError(f"TARGETS_NOT_IN_PREFIX:{len(targets)}!={len(identities)}")
    return targets


def audit_stream(compressed_stream, targets: dict, max_raw_lines: int) -> dict:
    if not 1 <= max_raw_lines <= 600_000:
        raise ValueError("RAW_LINE_BOUND_INVALID")
    found = []
    seen = set()
    with bz2.BZ2File(compressed_stream, "rb") as archive:
        for raw_ordinal in range(max_raw_lines):
            line = archive.readline()
            if not line:
                break
            raw = line.rstrip(b"\r\n")
            digest = hashlib.sha256(raw).hexdigest()
            if digest not in targets:
                continue
            if digest in seen:
                raise ValueError("DUPLICATE_RAW_ARCHIVE_TARGET")
            seen.add(digest)
            canonical_ordinal, legacy = targets[digest]
            parsed = parse_sgf_line("registered-public-iss-raw", raw)
            differences = [
                field for field in CORE_FIELDS
                if parsed.get(field) != legacy.get(field)
            ]
            v2_from_projection = semantic_identity(legacy)
            found.append({
                "raw_sha256": digest,
                "raw_line_ordinal": raw_ordinal,
                "canonical_line_ordinal": canonical_ordinal,
                "legacy_semantic_sha256": legacy["semantic_sha256"],
                "v2_semantic_sha256": parsed["semantic_sha256"],
                "v2_projection_identity_equal": (
                    v2_from_projection == parsed["semantic_sha256"]
                ),
                "classification": parsed["classification"],
                "core_field_differences": differences,
            })
            if len(seen) == len(targets):
                break
    return {
        "raw_lines_scanned": raw_ordinal + 1,
        "targets": len(targets),
        "found": sorted(found, key=lambda row: row["canonical_line_ordinal"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--oracle-evidence", type=Path, action="append", required=True)
    parser.add_argument("--canonical-prefix-lines", type=int, default=100_000)
    parser.add_argument("--max-raw-lines", type=int, default=150_000)
    parser.add_argument("--registered-raw-archive-sha256", required=True)
    parser.add_argument("--registered-canonical-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    targets = target_records(
        args.canonical, args.oracle_evidence, args.canonical_prefix_lines,
    )
    result = audit_stream(sys.stdin.buffer, targets, args.max_raw_lines)
    findings = result["found"]
    passed = (
        len(findings) == result["targets"]
        and all(row["classification"] == "PARSED_PLAYED_GAME"
                and row["v2_projection_identity_equal"]
                and not row["core_field_differences"] for row in findings)
    )
    result.update({
        "schema": SCHEMA,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "registered_raw_archive_sha256": args.registered_raw_archive_sha256,
        "registered_canonical_sha256": args.registered_canonical_sha256,
        "canonical_prefix_lines": args.canonical_prefix_lines,
        "legacy_and_v2_identities_differ": sum(
            row["legacy_semantic_sha256"] != row["v2_semantic_sha256"]
            for row in findings
        ),
        "status": "PASS" if passed else "FAIL",
        "restriction": "Train-split rules research only. Full archive hash is registered provenance, not freshly recomputed by bounded prefix scan; do not use Legacy semantic IDs as V2 IDs.",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=args.output.name + ".", dir=args.output.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, args.output)
    print(json.dumps({
        "status": result["status"], "targets": result["targets"],
        "found": len(findings), "raw_lines_scanned": result["raw_lines_scanned"],
        "legacy_and_v2_identities_differ": result["legacy_and_v2_identities_differ"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
