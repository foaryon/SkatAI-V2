from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from skatai.data.bidding import iter_bidding_decisions
from skatai.data.bidding_features import FEATURE_SCHEMA, encode_decision

DATASET_SCHEMA = "skatai.v2.bidding-parquet.v1"
SPLITS = (
    "train",
    "validation",
    "test",
    "external_bot_holdout",
    "future_holdout",
    "quarantine_date",
)

FEATURE_COLUMNS = (
    "hand_mask",
    "actor",
    "bidder",
    "answerer",
    "bid_index",
    "decision_role",
)

TARGET_COLUMNS = ("target_continue",)

# Stored for provenance/analysis/possible future separately-gated auxiliary tasks.
# These MUST NOT be inputs to the B1 bidding policy.
ANALYSIS_COLUMNS = (
    "game_identity",
    "source",
    "date",
    "ordinal",
    "current_offer",
    "declarer",
    "bid_level",
    "game_type",
    "actor_rating",
    "game_won",
    "game_value",
    "card_points",
)


def _arrow():
    import pyarrow as pa
    import pyarrow.parquet as pq

    return pa, pq


def arrow_schema():
    pa, _ = _arrow()
    metadata = {
        b"dataset_schema": DATASET_SCHEMA.encode(),
        b"feature_schema": FEATURE_SCHEMA.encode(),
        b"feature_columns": ",".join(FEATURE_COLUMNS).encode(),
        b"target_columns": ",".join(TARGET_COLUMNS).encode(),
        b"analysis_columns": ",".join(ANALYSIS_COLUMNS).encode(),
        b"information_policy": (
            b"B1 features are own 10-card hand plus public bidding duel state only"
        ),
    }
    return pa.schema(
        [
            pa.field("game_identity", pa.binary(32), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("date", pa.string(), nullable=False),
            pa.field("split", pa.string(), nullable=False),
            pa.field("ordinal", pa.uint8(), nullable=False),
            pa.field("hand_mask", pa.uint32(), nullable=False),
            pa.field("actor", pa.uint8(), nullable=False),
            pa.field("bidder", pa.uint8(), nullable=False),
            pa.field("answerer", pa.uint8(), nullable=False),
            pa.field("bid_index", pa.uint8(), nullable=False),
            pa.field("decision_role", pa.uint8(), nullable=False),
            pa.field("current_offer", pa.uint16(), nullable=False),
            pa.field("target_continue", pa.uint8(), nullable=False),
            pa.field("declarer", pa.uint8(), nullable=False),
            pa.field("bid_level", pa.uint16(), nullable=False),
            pa.field("game_type", pa.string(), nullable=False),
            pa.field("actor_rating", pa.float32(), nullable=True),
            pa.field("game_won", pa.bool_(), nullable=False),
            pa.field("game_value", pa.int16(), nullable=False),
            pa.field("card_points", pa.int16(), nullable=False),
        ],
        metadata=metadata,
    )


def _actor_rating(game: Mapping[str, Any], actor: int) -> float | None:
    ratings = game.get("ratings") or ()
    if actor >= len(ratings):
        return None
    try:
        value = float(ratings[actor])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parquet_row(game: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    enc = encode_decision(decision)
    actor = enc.actor
    identity = str(decision["game_identity"])
    if len(identity) != 64:
        raise ValueError("BAD_GAME_IDENTITY")
    return {
        "game_identity": bytes.fromhex(identity),
        "source": str(game["source"]),
        "date": str(game.get("date") or ""),
        "split": str(decision["split"]),
        "ordinal": int(decision["ordinal"]),
        "hand_mask": enc.hand_mask,
        "actor": actor,
        "bidder": enc.bidder,
        "answerer": enc.answerer,
        "bid_index": enc.bid_index,
        "decision_role": enc.decision_role,
        "current_offer": int(decision["current_offer"]),
        "target_continue": enc.target_continue,
        "declarer": int(game["declarer"]),
        "bid_level": int(game["bid_level"]),
        "game_type": str(game.get("game_type") or ""),
        "actor_rating": _actor_rating(game, actor),
        "game_won": bool(game.get("won")),
        "game_value": int(game.get("game_value") or 0),
        "card_points": int(game.get("card_points") or 0),
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb", buffering=8 * 1024 * 1024) as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class Materializer:
    def __init__(self, output: Path, rows_per_shard: int = 1_000_000) -> None:
        self.output = output
        self.rows_per_shard = rows_per_shard
        self.buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.shard_index: dict[str, int] = defaultdict(int)
        self.rows: dict[str, int] = defaultdict(int)
        self.shards: list[dict[str, Any]] = []
        self.schema = arrow_schema()
        output.mkdir(parents=True, exist_ok=True)

    def add(self, game: Mapping[str, Any], decision: Mapping[str, Any]) -> None:
        split = str(decision["split"])
        if split not in SPLITS:
            raise ValueError(f"UNKNOWN_SPLIT:{split}")
        self.buffers[split].append(parquet_row(game, decision))
        self.rows[split] += 1
        if len(self.buffers[split]) >= self.rows_per_shard:
            self.flush(split)

    def flush(self, split: str) -> None:
        rows = self.buffers[split]
        if not rows:
            return
        pa, pq = _arrow()
        directory = self.output / split
        directory.mkdir(parents=True, exist_ok=True)
        idx = self.shard_index[split]
        path = directory / f"part-{idx:05d}.parquet"
        table = pa.Table.from_pylist(rows, schema=self.schema)
        pq.write_table(
            table,
            path,
            compression="zstd",
            compression_level=6,
            row_group_size=262_144,
            use_dictionary=["source", "date", "split", "game_type"],
            write_statistics=True,
        )
        self.shards.append(
            {
                "split": split,
                "path": str(path.relative_to(self.output)),
                "rows": len(rows),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
        self.shard_index[split] += 1
        rows.clear()

    def finish(self) -> dict[str, Any]:
        for split in SPLITS:
            self.flush(split)
        return {
            "dataset_schema": DATASET_SCHEMA,
            "feature_schema": FEATURE_SCHEMA,
            "rows": dict(sorted(self.rows.items())),
            "rows_per_shard": self.rows_per_shard,
            "feature_columns": list(FEATURE_COLUMNS),
            "target_columns": list(TARGET_COLUMNS),
            "analysis_columns": list(ANALYSIS_COLUMNS),
            "shards": self.shards,
        }


def materialize_jsonl(
    input_path: Path,
    output: Path,
    *,
    rows_per_shard: int = 1_000_000,
    max_games: int | None = None,
) -> dict[str, Any]:
    m = Materializer(output, rows_per_shard=rows_per_shard)
    games = eligible_games = 0
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            games += 1
            game = json.loads(line)
            decisions = list(iter_bidding_decisions(game))
            if decisions:
                eligible_games += 1
                for decision in decisions:
                    m.add(game, decision)
            if max_games is not None and games >= max_games:
                break
    manifest = m.finish()
    manifest["source_path"] = str(input_path)
    manifest["games_scanned"] = games
    manifest["eligible_games"] = eligible_games
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest["manifest_sha256"] = sha256_file(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows-per-shard", type=int, default=1_000_000)
    parser.add_argument("--max-games", type=int)
    args = parser.parse_args()
    result = materialize_jsonl(
        args.input,
        args.output,
        rows_per_shard=args.rows_per_shard,
        max_games=args.max_games,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
