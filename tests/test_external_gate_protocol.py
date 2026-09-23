import json
from pathlib import Path


def test_b1_external_gate_cannot_be_satisfied_by_local_or_offline_metrics():
    p = Path(__file__).resolve().parents[1] / "provenance" / "B1_EXTERNAL_ISS_GATE_PROTOCOL.json"
    x = json.loads(p.read_text())
    promotion = x["promotion_policy"]
    assert promotion["offline_metrics_can_accept"] is False
    assert promotion["local_pregate_can_accept"] is False
    assert promotion["external_gameplay_required_for_accept"] is True
    assert set(promotion["allowed_final_outcomes"]) == {"ACCEPT", "REJECT", "INCONCLUSIVE"}


def test_external_gate_preserves_frozen_eval_membership():
    p = Path(__file__).resolve().parents[1] / "provenance" / "B1_EXTERNAL_ISS_GATE_PROTOCOL.json"
    x = json.loads(p.read_text())
    assert x["contamination_policy"]["external_gate_games_are_frozen_evaluation_evidence"]
    assert x["contamination_policy"]["do_not_train_on_gate_games_before_gate_decision"]
