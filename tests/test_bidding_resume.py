import json
from pathlib import Path

import torch

from skatai.data.bidding_parquet import materialize_jsonl
from skatai.models.bidding import BiddingModelConfig
from skatai.training.bidding import train


def _game(identity: str, day: int):
    return {
        "source": "iss",
        "date": f"2022-06-{day:02d}",
        "semantic_sha256": identity * 64,
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


def _state(path: Path):
    artifact = torch.load(path, map_location="cpu")
    return artifact["state_dict"]


def test_epoch_boundary_resume_matches_uninterrupted_training(tmp_path):
    src = tmp_path / "games.jsonl"
    src.write_text(
        "\n".join(json.dumps(_game(str(i + 1), i + 1)) for i in range(4)) + "\n"
    )
    dataset = tmp_path / "dataset"
    materialize_jsonl(src, dataset, rows_per_shard=5)

    cfg = BiddingModelConfig(hidden_dim=16, depth=1, dropout=0.1)
    common = dict(
        config=cfg,
        batch_size=4,
        learning_rate=1e-3,
        weight_decay=0.0,
        seed=77,
        device="cpu",
        max_batches=3,
    )

    continuous = tmp_path / "continuous"
    train(dataset, continuous, epochs=2, **common)

    resumed = tmp_path / "resumed"
    train(dataset, resumed, epochs=1, **common)
    ckpt = resumed / "checkpoint-epoch-001.pt"
    result = train(dataset, resumed, epochs=2, resume_from=ckpt, **common)

    a = _state(continuous / "model.pt")
    b = _state(resumed / "model.pt")
    assert a.keys() == b.keys()
    assert all(torch.equal(a[k], b[k]) for k in a)
    assert result["steps"] == 6
    assert len(result["epoch_metrics"]) == 2


def test_resume_rejects_dataset_identity_change(tmp_path):
    src = tmp_path / "games.jsonl"
    src.write_text(json.dumps(_game("1", 1)) + "\n")
    dataset = tmp_path / "dataset"
    materialize_jsonl(src, dataset, rows_per_shard=10)

    out = tmp_path / "run"
    cfg = BiddingModelConfig(hidden_dim=8, depth=1, dropout=0.0)
    train(dataset, out, epochs=1, config=cfg, batch_size=2, max_batches=1)

    manifest = dataset / "manifest.json"
    manifest.write_text(manifest.read_text() + "\n")

    try:
        train(
            dataset,
            out,
            epochs=2,
            config=cfg,
            batch_size=2,
            max_batches=1,
            resume_from=out / "checkpoint-epoch-001.pt",
        )
    except ValueError as exc:
        assert "DATASET_IDENTITY_MISMATCH" in str(exc)
    else:
        raise AssertionError("resume must reject changed dataset identity")
