from skatai.gameplay.bidding import simulate_auction
from skatai.gameplay.skatzero_bidding import (
    bidding_seat_to_player,
    map_auction_to_players,
    max_accepted_bids_by_seat,
)


HANDS = [
    ["C7", "C8", "C9", "CT", "CJ", "CQ", "CK", "CA", "S7", "S8"],
    ["S9", "ST", "SJ", "SQ", "SK", "SA", "H7", "H8", "H9", "HT"],
    ["HJ", "HQ", "HK", "HA", "D7", "D8", "D9", "DT", "DJ", "DQ"],
]


def test_starting_player_mapping():
    assert bidding_seat_to_player(0) == (0, 1, 2)
    assert bidding_seat_to_player(1) == (1, 2, 0)
    assert bidding_seat_to_player(2) == (2, 0, 1)


def test_map_auction_rotates_winner_and_bid_evidence():
    def p(hand, actor, bidder, answerer, bid_index, role):
        if actor == 1 and bid_index == 0 and role == "BIDDER":
            return 0.9
        return 0.1

    auction = simulate_auction(HANDS, p)
    assert auction.winner == 1
    assert max_accepted_bids_by_seat(auction) == (0, 18, 0)

    mapped = map_auction_to_players(auction, starting_player=2)
    assert mapped.seat_to_player == (2, 0, 1)
    assert mapped.winner_player_id == 0
    assert mapped.max_accepted_bids_by_player == (18, 0, 0)
