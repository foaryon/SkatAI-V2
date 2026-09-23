import numpy as np

from skatai.evaluation.bidding_strata import Accumulator, _sample_mask, domain_drift


def metrics(rows, gap):
    observed = 0.6
    return {
        "rows": rows,
        "mean_predicted_continue": observed + gap,
        "observed_continue_rate": observed,
        "calibration_gap_predicted_minus_observed": gap,
        "brier": 0.2,
        "accuracy_at_0_5": 0.7,
    }


def test_accumulator_metrics():
    a = Accumulator()
    a.update(np.array([0.9, 0.2]), np.array([1.0, 0.0]))
    r = a.result()
    assert r["rows"] == 2
    assert abs(r["mean_predicted_continue"] - 0.55) < 1e-12
    assert abs(r["observed_continue_rate"] - 0.5) < 1e-12
    assert r["accuracy_at_0_5"] == 1.0


def test_game_identity_sampling_keeps_same_game_together():
    a = bytes.fromhex("00" * 32)
    b = bytes.fromhex("ff" * 32)
    mask = _sample_mask([a, a, b, b], numerator=1, denominator=2)
    assert mask.tolist() == [True, True, False, False]


def test_domain_drift_orders_largest_shift():
    test = {
        "groups": {
            "actor": {
                "0": metrics(10000, 0.01),
                "1": metrics(10000, 0.02),
            }
        }
    }
    ext = {
        "groups": {
            "actor": {
                "0": metrics(12000, 0.20),
                "1": metrics(12000, 0.05),
            }
        }
    }
    r = domain_drift(test, ext)
    rows = r["largest_absolute_calibration_gap_shifts"]
    assert rows[0]["key"] == "0"
    assert abs(rows[0]["calibration_gap_shift_external_minus_test"] - 0.19) < 1e-12
