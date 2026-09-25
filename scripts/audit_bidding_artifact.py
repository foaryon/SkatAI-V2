"""Audit a pinned local bidding Parquet artifact without rewriting it.

Run on isolated compute after staging exactly the manifest-listed shards.
The output is a compact count report; rows and holdout identities stay local.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date as calendar_date
import hashlib
import json
from pathlib import Path
import re

import pyarrow.parquet as pq

from skatai.data.bidding_parquet import sha256_file


def _date_split(raw: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return "quarantine_date"
    try:
        calendar_date.fromisoformat(raw)
    except ValueError:
        return "quarantine_date"
    if raw < "2023-01-01":
        return "train"
    if raw < "2024-01-01":
        return "validation"
    if raw < "2025-01-01":
        return "test"
    return "future_holdout"


def audit_artifact(manifest_path: Path, root: Path, expected_manifest_sha256: str) -> dict:
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise ValueError("BIDDING_AUDIT_MANIFEST_HASH_MISMATCH")
    manifest = json.loads(manifest_bytes)
    shards = manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        raise ValueError("BIDDING_AUDIT_NO_SHARDS")
    root = root.resolve()
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    anomalies: Counter[str] = Counter()
    for shard in shards:
        rel = shard["path"]
        if rel in seen:
            raise ValueError("BIDDING_AUDIT_DUPLICATE_SHARD")
        seen.add(rel)
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("BIDDING_AUDIT_SHARD_PATH_INVALID")
        if sha256_file(path) != shard["sha256"]:
            raise ValueError("BIDDING_AUDIT_SHARD_HASH_MISMATCH")
        split = str(shard["split"])
        if split not in {
            "train", "validation", "test", "future_holdout",
            "external_bot_holdout", "quarantine_date",
        }:
            raise ValueError("BIDDING_AUDIT_SHARD_SPLIT_INVALID")
        shard_rows = 0
        reader = pq.ParquetFile(path)
        for batch in reader.iter_batches(
            batch_size=65536,
            columns=["date", "split", "actor", "bidder", "answerer", "decision_role"],
        ):
            cols = batch.to_pydict()
            for raw, observed_split, actor, bidder, answerer, role in zip(
                cols["date"], cols["split"], cols["actor"], cols["bidder"],
                cols["answerer"], cols["decision_role"], strict=True,
            ):
                shard_rows += 1
                expected_split = _date_split(str(raw))
                if expected_split == "quarantine_date":
                    anomalies["invalid_calendar_date_rows"] += 1
                if (observed_split != split
                        or (split != "external_bot_holdout" and expected_split != split)):
                    anomalies["split_mismatch_rows"] += 1
                if (actor not in (0, 1, 2) or bidder not in (0, 1, 2)
                        or answerer not in (0, 1, 2) or bidder == answerer
                        or role not in (0, 1)
                        or actor != (bidder if role == 0 else answerer)):
                    anomalies["actor_role_mismatch_rows"] += 1
        if shard_rows != shard["rows"]:
            raise ValueError("BIDDING_AUDIT_SHARD_ROW_COUNT_MISMATCH")
        counts[split] += shard_rows
    if dict(counts) != {str(k): int(v) for k, v in manifest["rows"].items() if v}:
        raise ValueError("BIDDING_AUDIT_MANIFEST_ROW_COUNT_MISMATCH")
    return {
        "schema": "skatai.v2.bidding-artifact-integrity-audit.v1",
        "manifest_sha256": expected_manifest_sha256,
        "shards_verified": len(seen),
        "rows_by_split": dict(sorted(counts.items())),
        "anomalies": {
            key: anomalies[key] for key in (
                "invalid_calendar_date_rows", "split_mismatch_rows",
                "actor_role_mismatch_rows",
            )
        },
        "status": (
            "PASS" if not (
                anomalies["split_mismatch_rows"] or anomalies["actor_role_mismatch_rows"]
            ) else "REVIEW_REQUIRED"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(audit_artifact(
        args.manifest, args.root, args.expected_manifest_sha256,
    ), sort_keys=True))


if __name__ == "__main__":
    main()
