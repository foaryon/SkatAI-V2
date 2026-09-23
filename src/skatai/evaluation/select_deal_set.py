from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterator, Mapping

from skatai.evaluation.bidding_gameplay_gate import (
    DEAL_SET_SCHEMA,
    _atomic_write_json,
    _deal_to_json,
    select_deals_from_games,
)


def select_from_stdin(
    output: Path,
    *,
    count: int,
    seed: int,
    expected_source_sha256: str | None = None,
    expected_source_bytes: int | None = None,
    expected_source_records: int | None = None,
) -> dict[str, Any]:
    h = hashlib.sha256()
    source_bytes = 0
    source_records = 0

    def games() -> Iterator[Mapping[str, Any]]:
        nonlocal source_bytes, source_records
        for raw in sys.stdin.buffer:
            h.update(raw)
            source_bytes += len(raw)
            source_records += 1
            yield json.loads(raw)

    deals, scanned = select_deals_from_games(games(), count=count, seed=seed)
    source_sha256 = h.hexdigest()
    if scanned != source_records:
        raise ValueError("SOURCE_RECORD_ACCOUNTING_MISMATCH")
    if expected_source_sha256 is not None and source_sha256 != expected_source_sha256:
        raise ValueError(
            f"SOURCE_SHA256_MISMATCH:{source_sha256}!={expected_source_sha256}"
        )
    if expected_source_bytes is not None and source_bytes != expected_source_bytes:
        raise ValueError(
            f"SOURCE_BYTES_MISMATCH:{source_bytes}!={expected_source_bytes}"
        )
    if expected_source_records is not None and source_records != expected_source_records:
        raise ValueError(
            f"SOURCE_RECORDS_MISMATCH:{source_records}!={expected_source_records}"
        )

    payload = {
        "schema": DEAL_SET_SCHEMA,
        "source": {
            "sha256": source_sha256,
            "bytes": source_bytes,
            "records": source_records,
        },
        "selection": {
            "split": "test",
            "seed": seed,
            "count": count,
        },
        "deals": [_deal_to_json(deal) for deal in deals],
    }
    _atomic_write_json(output, payload)
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--count", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--expected-source-sha256")
    p.add_argument("--expected-source-bytes", type=int)
    p.add_argument("--expected-source-records", type=int)
    args = p.parse_args()

    payload = select_from_stdin(
        args.output,
        count=args.count,
        seed=args.seed,
        expected_source_sha256=args.expected_source_sha256,
        expected_source_bytes=args.expected_source_bytes,
        expected_source_records=args.expected_source_records,
    )
    print(
        json.dumps(
            {
                "source": payload["source"],
                "selection": payload["selection"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
