from skatai.data.bidding import contains_benchmark_bot, iter_bidding_decisions, split_for_game
from skatai.game.bidding import replay


def _game(players=None):
    return {
        "source": "iss",
        "date": "2022-06-15",
        "semantic_sha256": "a" * 64,
        "players": players or ["alice", "bob", "carol"],
        "declarer": 2,
        "bid_level": 20,
        "initial_hands": [
            ["C7", "C8", "C9", "CT", "CJ", "CQ", "CK", "CA", "S7", "S8"],
            ["S9", "ST", "SJ", "SQ", "SK", "SA", "H7", "H8", "H9", "HT"],
            ["HJ", "HQ", "HK", "HA", "D7", "D8", "D9", "DT", "DJ", "DQ"],
        ],
        "bidding_history": ["1", "18", "0", "p", "2", "20", "1", "p", "2", "s"],
    }


def test_replay_known_history():
    r = replay(_game()["bidding_history"])
    assert r.ok
    assert r.winner == 2
    assert r.winning_bid == 20
    assert [x["target"] for x in r.actions] == ["CONTINUE", "PASS", "CONTINUE", "PASS"]


def test_illegal_skip_is_quarantined():
    r = replay(["1", "20", "0", "p"])
    assert not r.ok
    assert "ILLEGAL_ACTION" in (r.error or "")


def test_benchmark_bot_detection():
    assert contains_benchmark_bot(["alice", "kermit:2", "bob"])
    assert contains_benchmark_bot(["theCount", "alice", "bob"])
    assert not contains_benchmark_bot(["alice", "bob", "carol"])


def test_benchmark_games_are_held_out():
    assert split_for_game(_game(["zoot", "alice", "bob"])) == "external_bot_holdout"


def test_decision_reconstruction_uses_only_bidding_information():
    rows = list(iter_bidding_decisions(_game()))
    assert [r["target"] for r in rows] == ["CONTINUE", "PASS", "CONTINUE", "PASS"]
    assert rows[0]["public_bidding_prefix"] == []
    assert rows[-1]["public_bidding_prefix"] == ["1", "18", "0", "p", "2", "20"]
    assert all("game_type" not in r for r in rows)
    assert all("won" not in r for r in rows)


def test_bidding_feature_encoder_is_decision_time_only():
    from skatai.data.bidding_features import encode_decision

    row = list(iter_bidding_decisions(_game()))[0]
    encoded = encode_decision(row)
    dense = encoded.dense()

    assert len(dense) == 44
    assert sum(dense[:32]) == 10
    assert encoded.actor == 1
    assert encoded.bidder == 1
    assert encoded.answerer == 0
    assert encoded.bid_index == 0
    assert encoded.target_continue == 1


def test_bidding_state_exposes_only_public_duel_state():
    row = list(iter_bidding_decisions(_game()))[2]
    assert row["actor"] == 2
    assert row["bidder"] == 2
    assert row["answerer"] == 1
    assert row["bid_index"] == 1
    forbidden = {"skat_initial", "discards", "game_type", "won", "card_points", "plays"}
    assert forbidden.isdisjoint(row)


def test_chronological_bidding_split():
    g = _game()
    g["date"] = "2022-12-31"
    assert split_for_game(g) == "train"
    g["date"] = "2023-01-01"
    assert split_for_game(g) == "validation"
    g["date"] = "2024-07-01"
    assert split_for_game(g) == "test"
    g["date"] = "2025-01-01"
    assert split_for_game(g) == "future_holdout"
    g["date"] = ""
    assert split_for_game(g) == "quarantine_date"


def test_benchmark_holdout_overrides_date():
    g = _game(["kermit", "alice", "bob"])
    g["date"] = "2018-01-01"
    assert split_for_game(g) == "external_bot_holdout"
