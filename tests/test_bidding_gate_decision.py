from skatai.evaluation.bidding_gate_decision import decide_local_stage


def _result(deals: int, pairs: int, *, ci_high, mean=0.0, changed=0):
    return {
        "configuration": {
            "deal_count": deals,
            "paired_seat_observations": pairs,
        },
        "summary": {
            "paired_delta_stats": {
                "n": pairs,
                "mean": mean,
                "sample_std": 1.0 if pairs > 1 else None,
                "standard_error": 0.1 if pairs > 1 else None,
                "ci95_low": None if ci_high is None else mean - 0.2,
                "ci95_high": ci_high,
            },
            "changed_auction_pairs": changed,
            "elapsed_s": 123.0,
        },
    }


def test_screen_incomplete_requires_resume():
    d = decide_local_stage(_result(29, 87, ci_high=1.0), "SCREEN")
    assert d["status"] == "INCOMPLETE"
    assert d["next_action"] == "RESUME_STAGE"
    assert d["accept_authorized"] is False
    assert d["strength_claim_authorized"] is False


def test_screen_clear_regression_stops_candidate():
    d = decide_local_stage(_result(30, 90, ci_high=-0.01, mean=-2.0), "SCREEN")
    assert d["status"] == "LOCAL_REGRESSION"
    assert d["next_action"] == "STOP_CANDIDATE"
    assert d["accept_authorized"] is False


def test_screen_nonregression_continues_confirmation_without_accept():
    d = decide_local_stage(_result(30, 90, ci_high=0.5, mean=0.1), "SCREEN")
    assert d["status"] == "LOCAL_GATE_NOT_REGRESSING"
    assert d["next_action"] == "CONTINUE_LOCAL_CONFIRMATION"
    assert d["accept_authorized"] is False
    assert d["strength_claim_authorized"] is False


def test_local_confirmation_can_only_require_external_evaluation():
    d = decide_local_stage(
        _result(100, 300, ci_high=0.25, mean=0.05),
        "LOCAL_CONFIRMATION",
    )
    assert d["status"] == "LOCAL_GATE_NOT_REGRESSING"
    assert d["next_action"] == "REQUIRE_EXTERNAL_DEPLOYMENT_VALID_EVALUATION"
    assert d["accept_authorized"] is False
    assert d["strength_claim_authorized"] is False


def test_local_confirmation_regression_rejects_locally():
    d = decide_local_stage(
        _result(100, 300, ci_high=-0.5, mean=-1.0),
        "LOCAL_CONFIRMATION",
    )
    assert d["status"] == "LOCAL_REGRESSION"
    assert d["next_action"] == "REJECT_FOR_LOCAL_REGRESSION"
    assert d["accept_authorized"] is False
