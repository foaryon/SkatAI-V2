from skatai.evaluation.bidding_weakness import analyze_gate_result


def auction(winner, bid, action):
    return {
        "winner": winner,
        "winning_bid": bid,
        "decisions": [
            {
                "actor": 1,
                "current_offer": 18,
                "decision_role": "BIDDER",
                "native_action": action,
            }
        ],
    }


def test_weakness_diagnostic_classifies_changed_and_declarer_transitions():
    payload = {
        "schema": "skatai.v2.b0-vs-b1-local-paired-gameplay.v1",
        "records": [
            {
                "baseline_auction": auction(None, None, "p"),
                "baseline_declaration": None,
                "paired": [
                    {
                        "candidate_seat": 0,
                        "delta": 10.0,
                        "treatment_auction": auction(0, 18, "18"),
                        "treatment_declaration": {"actual_contract": "G"},
                    },
                    {
                        "candidate_seat": 1,
                        "delta": 0.0,
                        "treatment_auction": auction(None, None, "p"),
                        "treatment_declaration": None,
                    },
                ],
            }
        ],
    }
    r = analyze_gate_result(payload)
    assert r["scope"]["paired_candidate_seat_observations"] == 2
    assert r["auction_change"]["changed_pairs"] == 1
    assert r["auction_change"]["candidate_became_declarer"] == 1
    assert r["by_auction_transition"]["ALL_PASS_TO_GAME"]["mean"] == 10.0
    assert r["by_baseline_contract"]["ALL_PASS"]["n"] == 2


def test_weakness_diagnostic_detects_same_declarer_bid_change():
    payload = {
        "schema": "skatai.v2.b0-vs-b1-local-paired-gameplay.v1",
        "records": [
            {
                "baseline_auction": auction(2, 18, "18"),
                "baseline_declaration": {"actual_contract": "D"},
                "paired": [
                    {
                        "candidate_seat": 2,
                        "delta": -5.0,
                        "treatment_auction": auction(2, 20, "20"),
                        "treatment_declaration": {"actual_contract": "D"},
                    }
                ],
            }
        ],
    }
    r = analyze_gate_result(payload)
    assert r["by_auction_transition"]["SAME_DECLARER_BID_CHANGED"]["n"] == 1
    assert r["auction_change"]["higher_winning_bid"] == 1
    assert r["by_baseline_role"]["CANDIDATE_BASELINE_DECLARER"]["mean"] == -5.0
