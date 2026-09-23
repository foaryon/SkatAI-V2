from skatai.evaluation.bidding_gate_clustered_decision import (
    deal_mean_deltas,
    decide_clustered_local_stage,
)


def _result(deal_means):
    records = []
    for i, mean in enumerate(deal_means):
        records.append(
            {
                "deal_identity": f"{i:064x}",
                "paired": [
                    {"delta": mean - 1.0},
                    {"delta": mean},
                    {"delta": mean + 1.0},
                ],
            }
        )
    n = len(records) * 3
    return {
        "configuration": {
            "deal_count": len(records),
            "paired_seat_observations": n,
        },
        "summary": {
            "paired_delta_stats": {
                "n": n,
                "mean": sum(deal_means) / len(deal_means) if deal_means else None,
                "sample_std": 999.0,
                "standard_error": 999.0,
                "ci95_low": -999.0,
                "ci95_high": 999.0,
            },
            "changed_auction_pairs": n,
            "elapsed_s": 1.0,
        },
        "records": records,
    }


def test_deal_mean_deltas_are_primary_cluster_units():
    r = _result([3.0, -6.0])
    assert deal_mean_deltas(r) == [3.0, -6.0]


def test_clustered_screen_clear_regression_stops():
    r = _result([-5.0] * 30)
    d = decide_clustered_local_stage(r, "SCREEN")
    assert d["status"] == "LOCAL_REGRESSION"
    assert d["next_action"] == "STOP_CANDIDATE"
    assert d["primary_stats"]["n"] == 30
    assert d["accept_authorized"] is False


def test_clustered_screen_nonregression_continues():
    r = _result([1.0] * 30)
    d = decide_clustered_local_stage(r, "SCREEN")
    assert d["status"] == "LOCAL_GATE_NOT_REGRESSING"
    assert d["next_action"] == "CONTINUE_LOCAL_CONFIRMATION"
    assert d["primary_stats"]["n"] == 30
    assert d["accept_authorized"] is False


def test_clustered_confirmation_never_accepts_locally():
    r = _result([1.0] * 100)
    d = decide_clustered_local_stage(r, "LOCAL_CONFIRMATION")
    assert d["next_action"] == "REQUIRE_EXTERNAL_DEPLOYMENT_VALID_EVALUATION"
    assert d["accept_authorized"] is False
    assert d["strength_claim_authorized"] is False


def test_clustered_incomplete_stage_requires_resume():
    r = _result([1.0] * 29)
    d = decide_clustered_local_stage(r, "SCREEN")
    assert d["status"] == "INCOMPLETE"
    assert d["next_action"] == "RESUME_STAGE"
