import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from skatai.selfplay.learner_dataset import load_bounded_learner_pilot
from skatai.selfplay.replay_audit import audit_captured_record


def test_private_seed_pilot_separates_replay_from_learner(tmp_path):
    root = Path(__file__).parents[1]
    prefix = tmp_path / "pilot"
    private_dir = tmp_path / "private"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    subprocess.run(
        [sys.executable, str(root / "scripts" / "run_private_seed_trajectory_pilot.py"),
         "--output-prefix", str(prefix), "--count", "2",
         "--private-dir", str(private_dir)],
        env=env, cwd=root, check=True, stdout=subprocess.DEVNULL,
    )
    private_path = private_dir / "pilot.private-seeds.json"
    private = json.loads(private_path.read_text())
    learner_manifest = json.loads(prefix.with_suffix(".learner-manifest.json").read_text())
    replay_manifest = json.loads(prefix.with_suffix(".manifest.json").read_text())
    learner_path = prefix.with_suffix(".learner.jsonl")
    learner_rows = [json.loads(line) for line in learner_path.open()]
    assert len(private["games"]) == len(learner_rows) == 2
    assert all(game["deal_seed"].bit_length() > 96 for game in private["games"])
    assert private_path.stat().st_mode & 0o077 == 0
    assert (private_dir / "pilot.raw.jsonl").stat().st_mode & 0o077 == 0
    assert hashlib.sha256(learner_path.read_bytes()).hexdigest() == learner_manifest["learner_sha256"]
    assert replay_manifest["learner_sha256"] == learner_manifest["learner_sha256"]
    assert "private_seed_file" not in learner_manifest and "raw_file" not in learner_manifest
    assert all("deal_seed" not in row and "deal_sha256" not in row for row in learner_rows)
    assert all(len({decision["seat"] for decision in row["decisions"]}) == 1 for row in learner_rows)
    raw_rows = [json.loads(line) for line in (private_dir / "pilot.raw.jsonl").open()]
    assert all(
        audit_captured_record(record, seed=seed["deal_seed"])["decisions"] == len(record["decisions"])
        for record, seed in zip(raw_rows, private["games"])
    )
    corrupted = json.loads(json.dumps(raw_rows[0]))
    corrupted["decisions"][0]["observation"]["hand"][0] = "XX"
    corrupted["decision_trace_sha256"] = hashlib.sha256(json.dumps(
        corrupted["decisions"], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with pytest.raises(ValueError, match="REPLAY_DECISION_OR_VIEW_MISMATCH"):
        audit_captured_record(corrupted, seed=private["games"][0]["deal_seed"])
    assert len(load_bounded_learner_pilot(
        prefix.with_suffix(".learner-manifest.json"), learner_path,
    )) == 2
    altered = json.loads(json.dumps(learner_rows))
    bid = next(d for row in altered for d in row["decisions"] if d["phase"] == "BID")
    bid["native_bid_action"] = "y" if bid["native_bid_action"] != "y" else "p"
    learner_path.write_text("".join(json.dumps(x) + "\n" for x in altered))
    learner_manifest["learner_sha256"] = hashlib.sha256(learner_path.read_bytes()).hexdigest()
    prefix.with_suffix(".learner-manifest.json").write_text(json.dumps(learner_manifest))
    with pytest.raises(ValueError, match="LEARNER_BID_NATIVE_ACTION_MISMATCH"):
        load_bounded_learner_pilot(prefix.with_suffix(".learner-manifest.json"), learner_path)
    learner_rows[0]["deal_seed"] = private["games"][0]["deal_seed"]
    learner_path.write_text("".join(json.dumps(x) + "\n" for x in learner_rows))
    learner_manifest["learner_sha256"] = hashlib.sha256(learner_path.read_bytes()).hexdigest()
    prefix.with_suffix(".learner-manifest.json").write_text(json.dumps(learner_manifest))
    with pytest.raises(ValueError, match="LEARNER_RECORD_INVALID"):
        load_bounded_learner_pilot(prefix.with_suffix(".learner-manifest.json"), learner_path)
