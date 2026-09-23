from skatai.evaluation.bidding_gameplay_gate import (
    Deal,
    _downstream_identity,
    _parse_hand_contract,
    _parse_pickup_contract,
)
from skatai.gameplay.bidding import simulate_max_bid_auction


HANDS = (
    ("C7", "C8", "C9", "CT", "CJ", "CQ", "CK", "CA", "S7", "S8"),
    ("S9", "ST", "SJ", "SQ", "SK", "SA", "H7", "H8", "H9", "HT"),
    ("HJ", "HQ", "HK", "HA", "D7", "D8", "D9", "DT", "DJ", "DQ"),
)


def test_parse_hand_contracts():
    assert _parse_hand_contract("CH") == ("C", True, False)
    assert _parse_hand_contract("GH") == ("G", True, False)
    assert _parse_hand_contract("NHO.C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8") == (
        "N",
        True,
        True,
    )


def test_parse_pickup_contracts():
    assert _parse_pickup_contract("C.C7.C8") == ("C", False, False, ("C7", "C8"))
    assert _parse_pickup_contract("NO.C7.C8.S9.ST") == (
        "N",
        False,
        True,
        ("C7", "C8"),
    )


def test_downstream_identity_depends_on_observed_bid_state():
    a = simulate_max_bid_auction(HANDS, [0, 18, 20])
    b = simulate_max_bid_auction(HANDS, [0, 18, 20])
    c = simulate_max_bid_auction(HANDS, [18, 18, 20])
    assert _downstream_identity(a) == _downstream_identity(b)
    assert _downstream_identity(a) != _downstream_identity(c)


def test_deal_shape():
    d = Deal("1" * 64, HANDS, ("DK", "DA"))
    assert len(d.hands) == 3


def test_paired_delta_stats():
    from skatai.evaluation.bidding_gameplay_gate import paired_delta_stats

    s = paired_delta_stats([1.0, 2.0, 3.0])
    assert s["n"] == 3
    assert s["mean"] == 2.0
    assert s["ci95_low"] < 2.0 < s["ci95_high"]


def test_auction_action_identity_ignores_probabilities():
    from skatai.evaluation.bidding_gameplay_gate import _auction_action_identity
    from skatai.gameplay.bidding import simulate_auction

    def p1(hand, actor, bidder, answerer, bid_index, role):
        return 0.9 if bid_index < 1 else 0.1

    def p2(hand, actor, bidder, answerer, bid_index, role):
        return 0.8 if bid_index < 1 else 0.2

    a = simulate_auction(HANDS, p1)
    b = simulate_auction(HANDS, p2)
    assert _auction_action_identity(a) == _auction_action_identity(b)
