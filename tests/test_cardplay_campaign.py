import copy

import pytest

from skatai.evaluation.cardplay_campaign import (
    frozen_hand_positions,
    summarize_position_results,
)
from skatai.evaluation.cardplay_gameplay_gate import CardplayPosition, evaluate_cardplay_position


def _results(position_set):
    return [
        {
            "deal_seed": position["deal_seed"],
            "deal_sha256": position["deal_sha256"],
            "contract": position["contract"],
            "declarer": position["declarer"],
            "winning_bid": position["winning_bid"],
            "picked_up_skat": False,
            "position_cluster_mean_delta": float(position["deal_seed"]),
            "rows": [
                {
                    "candidate_seat": seat,
                    "role": "DECLARER" if seat == position["declarer"] else "DEFENDER",
                    "control_signed_declarer_score": 0,
                    "treatment_signed_declarer_score": (
                        position["deal_seed"] if seat == position["declarer"]
                        else -position["deal_seed"]
                    ),
                    "candidate_delta": float(position["deal_seed"]),
                }
                for seat in range(3)
            ],
        }
        for position in position_set["positions"]
    ]


def test_frozen_positions_cover_every_contract_and_seat_with_stable_identity():
    positions = frozen_hand_positions([1, 2])
    assert positions == frozen_hand_positions([1, 2])
    assert positions["position_count"] == 36
    assert {row["contract"] for row in positions["positions"]} == {
        "CH", "SH", "HH", "DH", "GH", "NH"
    }
    assert {row["declarer"] for row in positions["positions"]} == {0, 1, 2}
    with pytest.raises(ValueError, match="INVALID_CARDPLAY_CAMPAIGN_SEEDS"):
        frozen_hand_positions([1, 1])


def test_summary_clusters_reused_positions_by_deal():
    positions = frozen_hand_positions([1, 3])
    summary = summarize_position_results(positions, _results(positions))
    overall = summary["summary"]["overall"]
    assert overall["independent_deal_count"] == 2
    assert overall["position_count"] == 36
    assert overall["mean_candidate_delta"] == 2
    assert overall["standard_error"] == 1
    assert summary["summary"]["family:SUIT"]["position_count"] == 24
    assert summary["acceptance_decision"] == "NOT_AUTHORIZED"


def test_summary_rejects_missing_duplicate_and_mismatched_evidence():
    positions = frozen_hand_positions([1])
    rows = _results(positions)
    with pytest.raises(ValueError, match="INCOMPLETE"):
        summarize_position_results(positions, rows[:-1])
    with pytest.raises(ValueError, match="DUPLICATE"):
        summarize_position_results(positions, rows + rows[:1])
    bad = copy.deepcopy(rows)
    bad[0]["deal_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="DEAL_MISMATCH"):
        summarize_position_results(positions, bad)
    bad = copy.deepcopy(rows)
    bad[0]["rows"][0]["candidate_delta"] = 100
    with pytest.raises(ValueError, match="DELTA_MISMATCH"):
        summarize_position_results(positions, bad)
    bad = copy.deepcopy(rows)
    bad[0]["position_cluster_mean_delta"] = 100
    with pytest.raises(ValueError, match="CLUSTER_MEAN_MISMATCH"):
        summarize_position_results(positions, bad)
    bad_set = copy.deepcopy(positions)
    bad_set["positions"][0]["deal_seed"] = 99
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        summarize_position_results(bad_set, rows)


def test_position_set_summarizes_real_legal_gameplay():
    class FirstLegal:
        def play_card(self, observation):
            return observation.legal_cards[0]

    positions = frozen_hand_positions([7])
    results = [
        evaluate_cardplay_position(
            CardplayPosition(
                row["deal_seed"], row["declarer"], row["contract"], row["winning_bid"]
            ),
            control_factory=lambda _seat: FirstLegal(),
            candidate_factory=lambda _seat: FirstLegal(),
        )
        for row in positions["positions"]
    ]
    summary = summarize_position_results(positions, results)
    assert summary["summary"]["overall"] == {
        "independent_deal_count": 1,
        "position_count": 18,
        "mean_candidate_delta": 0,
        "standard_error": None,
    }
