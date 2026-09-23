from skatai.gameplay.bidding import simulate_auction


HANDS = [
    ["C7", "C8", "C9", "CT", "CJ", "CQ", "CK", "CA", "S7", "S8"],
    ["S9", "ST", "SJ", "SQ", "SK", "SA", "H7", "H8", "H9", "HT"],
    ["HJ", "HQ", "HK", "HA", "D7", "D8", "D9", "DT", "DJ", "DQ"],
]


def test_simulate_all_pass_is_legal_and_complete():
    result = simulate_auction(HANDS, lambda *args: 0.0)
    assert result.all_pass
    assert result.winner is None
    assert result.winning_bid is None
    assert [(d.actor, d.native_action) for d in result.decisions] == [
        (1, "p"),
        (2, "p"),
        (0, "p"),
    ]


def test_simulate_simple_auction_with_fixed_probabilities():
    def policy(hand, actor, bidder, answerer, bid_index, role):
        # Seat 1 bids 18 then passes to seat 2; everyone else passes.
        if actor == 1 and bid_index == 0 and role == "BIDDER":
            return 0.9
        return 0.1

    result = simulate_auction(HANDS, policy)
    assert result.winner == 1
    assert result.winning_bid == 18
    assert not result.all_pass
    assert all(0.0 <= d.probability_continue <= 1.0 for d in result.decisions)


def test_threshold_validation():
    try:
        simulate_auction(HANDS, lambda *args: 0.5, threshold=1.1)
    except ValueError as exc:
        assert "BAD_THRESHOLD" in str(exc)
    else:
        raise AssertionError("invalid threshold must fail")


def test_simulate_max_bid_auction():
    from skatai.gameplay.bidding import simulate_max_bid_auction

    result = simulate_max_bid_auction(HANDS, [0, 18, 20])
    assert result.winner == 2
    assert result.winning_bid == 20
    assert result.all_pass is False


def test_simulate_max_bid_all_pass():
    from skatai.gameplay.bidding import simulate_max_bid_auction

    result = simulate_max_bid_auction(HANDS, [0, 0, 0])
    assert result.winner is None
    assert result.all_pass is True
