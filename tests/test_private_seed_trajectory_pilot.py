import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def test_private_seed_pilot_separates_replay_from_learner(tmp_path):
    root = Path(__file__).parents[1]
    prefix = tmp_path / "pilot"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    subprocess.run(
        [sys.executable, str(root / "scripts" / "run_private_seed_trajectory_pilot.py"),
         "--output-prefix", str(prefix), "--count", "2"],
        env=env, cwd=root, check=True, stdout=subprocess.DEVNULL,
    )
    private = json.loads(prefix.with_suffix(".private-seeds.json").read_text())
    learner_manifest = json.loads(prefix.with_suffix(".learner-manifest.json").read_text())
    replay_manifest = json.loads(prefix.with_suffix(".manifest.json").read_text())
    learner_path = prefix.with_suffix(".learner.jsonl")
    learner_rows = [json.loads(line) for line in learner_path.open()]
    assert len(private["games"]) == len(learner_rows) == 2
    assert all(game["deal_seed"].bit_length() > 96 for game in private["games"])
    assert prefix.with_suffix(".private-seeds.json").stat().st_mode & 0o077 == 0
    assert hashlib.sha256(learner_path.read_bytes()).hexdigest() == learner_manifest["learner_sha256"]
    assert replay_manifest["learner_sha256"] == learner_manifest["learner_sha256"]
    assert "private_seed_file" not in learner_manifest and "raw_file" not in learner_manifest
    assert all("deal_seed" not in row and "deal_sha256" not in row for row in learner_rows)
    assert all(len({decision["seat"] for decision in row["decisions"]}) == 1 for row in learner_rows)
