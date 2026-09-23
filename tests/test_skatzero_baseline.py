from skatai.evaluation.skatzero_baseline import (
    actor_max_accepted_bids,
    dealer_spec,
    normalized_gametype,
)


def _game():
    return {
        "source": "iss",
        "game_id": 1,
        "date": "2024-03-01",
        "semantic_sha256": "a" * 64,
        "game_type": "HEARTS",
        "declarer": 2,
        "is_hand": False,
        "is_ouvert": False,
        "skat_initial": ["DK", "DA"],
        "discards": ["D7", "D8"],
        "initial_hands": [
            ["C7", "C8", "C9", "CT", "CJ", "CQ", "CK", "CA", "S7", "S8"],
            ["S9", "ST", "SJ", "SQ", "SK", "SA", "H7", "H8", "H9", "HT"],
            ["HJ", "HQ", "HK", "HA", "D7", "D8", "D9", "DT", "DJ", "DQ"],
        ],
        "bidding_history": ["1", "18", "0", "p", "2", "20", "1", "p", "2", "s"],
    }


def test_normalized_gametype():
    assert normalized_gametype("CLUBS") == "D"
    assert normalized_gametype("GRAND") == "G"
    assert normalized_gametype("NULL") == "N"


def test_actor_max_accepted_bids():
    assert actor_max_accepted_bids(_game()) == [0, 18, 20]


def test_dealer_spec_rotates_declarer_to_zero_and_resolves_skat():
    spec = dealer_spec(_game())
    assert spec["normalized_gametype"] == "D"
    assert spec["starting_player"] == 1
    assert len(spec["deck"]) == 32
    assert len(set(spec["deck"])) == 32
    assert spec["deck"][:10] == [
        "HJ", "HQ", "HK", "HA", "D9", "DT", "DJ", "DQ", "DK", "DA"
    ]
    assert spec["deck"][-2:] == ["D7", "D8"]
    assert spec["relative_max_bids"] == [20, 0, 18]
