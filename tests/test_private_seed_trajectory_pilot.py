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
    corrupted_points = json.loads(json.dumps(raw_rows[0]))
    raw_cardplay = next(d for d in corrupted_points["decisions"] if d["phase"] == "CARDPLAY")
    raw_cardplay["observation"]["points_self"] += 1
    corrupted_points["decision_trace_sha256"] = hashlib.sha256(json.dumps(
        corrupted_points["decisions"], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with pytest.raises(ValueError, match="LEARNER_CARDPLAY_POINT_STATE_MISMATCH"):
        audit_captured_record(corrupted_points, seed=private["games"][0]["deal_seed"])
    assert len(load_bounded_learner_pilot(
        prefix.with_suffix(".learner-manifest.json"), learner_path,
    )) == 2
    truncated = json.loads(json.dumps(learner_rows))
    truncated[0]["decisions"].pop()
    learner_path.write_text("".join(json.dumps(x) + "\n" for x in truncated))
    learner_manifest["learner_sha256"] = hashlib.sha256(learner_path.read_bytes()).hexdigest()
    prefix.with_suffix(".learner-manifest.json").write_text(json.dumps(learner_manifest))
    with pytest.raises(ValueError, match="LEARNER_COMPLETED_DECLARER_SEQUENCE_REQUIRED"):
        load_bounded_learner_pilot(prefix.with_suffix(".learner-manifest.json"), learner_path)
    altered = json.loads(json.dumps(learner_rows))
    bid = next(d for row in altered for d in row["decisions"] if d["phase"] == "BID")
    bid["native_bid_action"] = "y" if bid["native_bid_action"] != "y" else "p"
    learner_path.write_text("".join(json.dumps(x) + "\n" for x in altered))
    learner_manifest["learner_sha256"] = hashlib.sha256(learner_path.read_bytes()).hexdigest()
    prefix.with_suffix(".learner-manifest.json").write_text(json.dumps(learner_manifest))
    with pytest.raises(ValueError, match="LEARNER_BID_NATIVE_ACTION_MISMATCH"):
        load_bounded_learner_pilot(prefix.with_suffix(".learner-manifest.json"), learner_path)
    altered_points = json.loads(json.dumps(learner_rows))
    cardplay = next(d for row in altered_points for d in row["decisions"] if d["phase"] == "CARDPLAY")
    cardplay["observation"]["points_self"] += 1
    learner_path.write_text("".join(json.dumps(x) + "\n" for x in altered_points))
    learner_manifest["learner_sha256"] = hashlib.sha256(learner_path.read_bytes()).hexdigest()
    prefix.with_suffix(".learner-manifest.json").write_text(json.dumps(learner_manifest))
    with pytest.raises(ValueError, match="LEARNER_CARDPLAY_POINT_STATE_MISMATCH"):
        load_bounded_learner_pilot(prefix.with_suffix(".learner-manifest.json"), learner_path)
    learner_rows[0]["deal_seed"] = private["games"][0]["deal_seed"]
    learner_path.write_text("".join(json.dumps(x) + "\n" for x in learner_rows))
    learner_manifest["learner_sha256"] = hashlib.sha256(learner_path.read_bytes()).hexdigest()
    prefix.with_suffix(".learner-manifest.json").write_text(json.dumps(learner_manifest))
    with pytest.raises(ValueError, match="LEARNER_RECORD_INVALID"):
        load_bounded_learner_pilot(prefix.with_suffix(".learner-manifest.json"), learner_path)


@pytest.mark.parametrize("contract,pickup", [("NO", True), ("NHO", False)])
def test_ouvert_capture_audit_and_learner_reject_false_public_hand(
    tmp_path, contract, pickup,
):
    from dataclasses import asdict
    from skatai.game.bidding import BID_VALUES
    from skatai.selfplay.cardplay import RandomLegalPolicy, make_deal
    from skatai.selfplay.trajectory import capture_basic_game, declarer_learner_view

    class Bid18:
        def probability_continue(self, view):
            return float(BID_VALUES[view.bid_index] <= 18)

    class Declare:
        def choose_contract(self, view):
            return "PICKUP" if pickup and not view.picked_up_skat else contract

    class Discard:
        def choose_discard(self, view):
            return view.hand12[:2]

    seed = 20260925
    raw = capture_basic_game(
        seed, source_commit="a" * 40,
        policy_ids={phase: (phase,) * 3 for phase in
                    ("BID", "DECLARATION", "DISCARD", "CARDPLAY")},
        bidding_policies=[Bid18() for _ in range(3)],
        declaration_policies=[Declare() for _ in range(3)],
        discard_policies=[Discard() for _ in range(3)],
        cardplay_policies=[RandomLegalPolicy(seed + seat) for seat in range(3)],
        legal_contracts=(contract,),
    )
    assert raw.contract == contract
    raw_row = json.loads(json.dumps(asdict(raw)))
    assert audit_captured_record(raw_row, seed=seed)["contract"] == contract
    assert all(
        len(item.observation["open_hand_cards"]) == 10 - sum(
            actor == raw.declarer for actor, _ in item.observation["played_cards"]
        )
        for item in raw.decisions if item.phase == "CARDPLAY"
    )

    learner = json.loads(json.dumps(asdict(declarer_learner_view(
        raw,
        policy_family_ids={phase: phase for phase in
                           ("BID", "DECLARATION", "DISCARD", "CARDPLAY")},
    ))))
    data_path = tmp_path / "ouvert.learner.jsonl"
    manifest_path = tmp_path / "ouvert.learner-manifest.json"

    def load_row(row):
        data_path.write_text(json.dumps(row) + "\n")
        manifest_path.write_text(json.dumps({
            "schema": "skatai.v2.selfplay.learner-seat-pilot-manifest.v1",
            "source_commit": "a" * 40,
            "count": 1,
            "learner_schema": "skatai.v2.selfplay.learner-seat.v1",
            "learner_file": data_path.name,
            "learner_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
            "trust_level": "D1_EXPLORATORY_ONLY",
            "restrictions": ["no training"],
        }))
        return load_bounded_learner_pilot(manifest_path, data_path)

    assert len(load_row(learner)) == 1
    wrong = json.loads(json.dumps(learner))
    view = next(d["observation"] for d in wrong["decisions"] if d["phase"] == "CARDPLAY")
    foreign = next(card for card in make_deal(seed).hands[(raw.declarer + 1) % 3]
                   if card not in view["open_hand_cards"])
    view["open_hand_cards"][0] = foreign
    with pytest.raises(ValueError, match="OUVERT_OWN_HAND_MISMATCH"):
        load_row(wrong)

    privileged = json.loads(json.dumps(asdict(raw)))
    raw_view = next(d["observation"] for d in privileged["decisions"]
                    if d["phase"] == "CARDPLAY" and d["seat"] != raw.declarer)
    foreign_seat = next(seat for seat in range(3)
                        if seat not in (raw.declarer, raw_view["seat"]))
    foreign_raw = next(card for card in make_deal(seed).hands[foreign_seat]
                       if card not in raw_view["open_hand_cards"])
    raw_view["open_hand_cards"][0] = foreign_raw
    privileged["decision_trace_sha256"] = hashlib.sha256(json.dumps(
        privileged["decisions"], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with pytest.raises(ValueError, match="REPLAY_OUVERT_PUBLIC_HAND_MISMATCH"):
        audit_captured_record(privileged, seed=seed)
