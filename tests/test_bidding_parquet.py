import json

import pyarrow.parquet as pq

from skatai.data.bidding_parquet import (
    ANALYSIS_COLUMNS,
    FEATURE_COLUMNS,
    materialize_jsonl,
)


def _game():
    return {
        "source": "iss",
        "date": "2022-06-15",
        "semantic_sha256": "1" * 64,
        "players": ["alice", "bob", "carol"],
        "ratings": [1200.0, 1300.0, 1400.0],
        "declarer": 2,
        "bid_level": 20,
        "game_type": "CLUBS",
        "won": True,
        "game_value": 24,
        "card_points": 72,
        "initial_hands": [
            ["C7", "C8", "C9", "CT", "CJ", "CQ", "CK", "CA", "S7", "S8"],
            ["S9", "ST", "SJ", "SQ", "SK", "SA", "H7", "H8", "H9", "HT"],
            ["HJ", "HQ", "HK", "HA", "D7", "D8", "D9", "DT", "DJ", "DQ"],
        ],
        "bidding_history": ["1", "18", "0", "p", "2", "20", "1", "p", "2", "s"],
    }


def test_parquet_materialization_keeps_feature_boundary(tmp_path):
    src = tmp_path / "games.jsonl"
    src.write_text(json.dumps(_game()) + "\n")
    out = tmp_path / "out"
    manifest = materialize_jsonl(src, out, rows_per_shard=2)

    assert manifest["rows"] == {"train": 4}
    assert set(FEATURE_COLUMNS).isdisjoint(ANALYSIS_COLUMNS)
    files = sorted((out / "train").glob("*.parquet"))
    assert len(files) == 2

    table = pq.read_table(files[0])
    assert table.num_rows == 2
    assert table.schema.metadata[b"information_policy"].startswith(b"B1 features")
    assert table.column("hand_mask").to_pylist()[0] != 0
