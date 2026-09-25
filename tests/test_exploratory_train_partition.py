"""The learner-facing exploratory reader cannot expose frozen test rows."""

import hashlib
import json

import pytest

from skatai.selfplay import learner_dataset


def test_exploratory_reader_returns_only_frozen_train_rows(tmp_path, monkeypatch):
    rows = [{"ordinal": i} for i in range(96)]
    manifest_path = tmp_path / "learner-manifest.json"
    data_path = tmp_path / "learner.jsonl"
    split_path = tmp_path / "split.json"
    manifest = {"source_commit": "a" * 40, "learner_sha256": "b" * 64}
    manifest_path.write_text(json.dumps(manifest))
    parts = {"train": list(range(72)), "validation": list(range(72, 84)),
             "test": list(range(84, 96))}
    split = {
        "schema": learner_dataset.SPLIT_SCHEMA,
        "trust_level": "D1_EXPLORATORY_ONLY",
        "learner_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "learner_sha256": manifest["learner_sha256"],
        "producer_source_commit": manifest["source_commit"],
        "partition": parts,
        "counts": {name: len(indices) for name, indices in parts.items()},
    }
    monkeypatch.setattr(learner_dataset, "load_bounded_learner_pilot", lambda *_: rows)

    def read_train():
        split_path.write_text(json.dumps(split))
        digest = hashlib.sha256(split_path.read_bytes()).hexdigest()
        return learner_dataset.load_exploratory_train_partition(
            manifest_path, data_path, split_path, expected_split_sha256=digest,
        )

    assert [row["ordinal"] for row in read_train()] == list(range(72))
    with pytest.raises(ValueError, match="LEARNER_SPLIT_HASH_MISMATCH"):
        learner_dataset.load_exploratory_train_partition(
            manifest_path, data_path, split_path, expected_split_sha256="0" * 64,
        )
    split["partition"]["train"][0] = 84
    with pytest.raises(ValueError, match="LEARNER_SPLIT_PARTITION_INVALID"):
        read_train()
    split["partition"]["train"][0] = 0
    split["learner_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="LEARNER_SPLIT_LINEAGE_MISMATCH"):
        read_train()
