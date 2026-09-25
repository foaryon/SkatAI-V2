import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.audit_bidding_artifact import audit_artifact
from skatai.data.bidding_parquet import (
    ANALYSIS_COLUMNS,
    DATASET_SCHEMA,
    DATASET_SCHEMA_V2,
    FEATURE_COLUMNS,
    materialize_jsonl,
    sha256_file,
)
from skatai.data.sgf import parse_sgf_line
ALL_PASS = b"(;GM[Skat]PC[ISS]ID[99]DT[2024-07-01/00:00:00/UTC]P0[a]P1[b]P2[c]R0[1]R1[2]R2[3]MV[w C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8.S9.ST.SJ.SQ.SK.SA.H7.H8.H9.HT.HJ.HQ.HK.HA.D7.D8.D9.DT.DJ.DQ.DK.DA 1 p 2 p 0 p w TI.0 ]R[d:-1 penalty v:0 m:0 bidok p:0 t:0 s:0 z:0 p0:0 p1:0 p2:1 l:-1 to:0 r:0] ;)"


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
    assert table.schema.metadata[b"dataset_schema"] == DATASET_SCHEMA.encode()
    assert not table.schema.field("declarer").nullable
    assert not table.schema.field("game_won").nullable
    assert table.column("hand_mask").to_pylist()[0] != 0


def test_verified_all_pass_materializes_with_null_analysis_in_v2(tmp_path):
    game = parse_sgf_line("iss", ALL_PASS)
    assert game["classification"] == "VERIFIED_ALL_PASS"
    assert "declarer" not in game and "bid_level" not in game
    src = tmp_path / "games.jsonl"
    src.write_text(json.dumps(game) + "\n")
    root = tmp_path / "out"
    manifest = materialize_jsonl(src, root, dataset_schema=DATASET_SCHEMA_V2)
    assert manifest["dataset_schema"] == DATASET_SCHEMA_V2
    assert manifest["eligible_games"] == 1
    assert manifest["rows"] == {"test": 3}
    table = pq.read_table(root / manifest["shards"][0]["path"])
    assert table.schema.metadata[b"dataset_schema"] == DATASET_SCHEMA_V2.encode()
    for name in ("declarer", "bid_level", "game_type", "game_won", "game_value", "card_points"):
        assert table.schema.field(name).nullable
        assert table.column(name).to_pylist() == [None] * table.num_rows
    assert table.column("target_continue").to_pylist() == [0, 0, 0]


def test_parquet_materialization_quarantines_impossible_calendar_date(tmp_path):
    game = _game()
    game["date"] = "2022-02-30"
    src = tmp_path / "games.jsonl"
    src.write_text(json.dumps(game) + "\n")
    manifest = materialize_jsonl(src, tmp_path / "out", rows_per_shard=4)
    assert manifest["rows"] == {"quarantine_date": 4}
    assert not (tmp_path / "out" / "train").exists()


def test_pinned_artifact_audit_counts_quarantine_and_detects_role_corruption(tmp_path):
    valid = _game()
    invalid = {**_game(), "date": "2022-02-30", "semantic_sha256": "2" * 64}
    src = tmp_path / "games.jsonl"
    src.write_text(json.dumps(valid) + "\n" + json.dumps(invalid) + "\n")
    root = tmp_path / "out"
    manifest = materialize_jsonl(src, root, rows_per_shard=4)
    path = root / "manifest.json"
    report = audit_artifact(path, root, manifest["manifest_sha256"])
    assert report["status"] == "PASS"
    assert report["rows_by_split"] == {"quarantine_date": 4, "train": 4}
    assert report["anomalies"]["invalid_calendar_date_rows"] == 4

    shard = next(s for s in manifest["shards"] if s["split"] == "train")
    shard_path = root / shard["path"]
    table = pq.read_table(shard_path)
    idx = table.schema.get_field_index("decision_role")
    table = table.set_column(idx, "decision_role", pa.array([1] * 4, type=pa.uint8()))
    idx = table.schema.get_field_index("hand_mask")
    table = table.set_column(idx, "hand_mask", pa.array([0] * 4, type=pa.uint32()))
    pq.write_table(table, shard_path)
    stored = json.loads(path.read_text())
    stored_shard = next(s for s in stored["shards"] if s["split"] == "train")
    stored_shard["sha256"] = sha256_file(shard_path)
    stored_shard["bytes"] = shard_path.stat().st_size
    path.write_text(json.dumps(stored, sort_keys=True) + "\n")
    changed_manifest_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    changed = audit_artifact(path, root, changed_manifest_sha256)
    assert changed["status"] == "REVIEW_REQUIRED"
    assert changed["anomalies"]["actor_role_mismatch_rows"] > 0
    assert changed["anomalies"]["feature_target_mismatch_rows"] == 4


def test_parquet_audit_accepts_legal_forehand_self_offer_at_18(tmp_path):
    game = _game()
    game["declarer"] = 0
    game["bid_level"] = 18
    game["bidding_history"] = ["1", "p", "2", "p", "0", "18"]
    src = tmp_path / "games.jsonl"
    src.write_text(json.dumps(game) + "\n")
    root = tmp_path / "out"
    manifest = materialize_jsonl(src, root, rows_per_shard=4)
    report = audit_artifact(
        root / "manifest.json",
        root,
        manifest["manifest_sha256"],
    )
    assert report["status"] == "PASS"
    assert report["anomalies"]["actor_role_mismatch_rows"] == 0
