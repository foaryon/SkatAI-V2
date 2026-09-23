from skatai.data.bidding import contains_benchmark_bot, iter_bidding_decisions, split_for_game


def _game(players=None):
    return {
        "source": "iss",
        "semantic_sha256": "a" * 64,
        "players": players or ["alice", "bob", "carol"],
        "initial_hands": [["C7"] * 10, ["S7"] * 10, ["H7"] * 10],
        "bidding_history": ["1", "18", "0", "y", "1", "20", "0", "p", "2", "p", "0", "s"],
    }


def test_benchmark_bot_detection():
    assert contains_benchmark_bot(["alice", "kermit:2", "bob"])
    assert contains_benchmark_bot(["theCount", "alice", "bob"])
    assert not contains_benchmark_bot(["alice", "bob", "carol"])


def test_benchmark_games_are_held_out():
    assert split_for_game(_game(["zoot", "alice", "bob"])) == "external_bot_holdout"


def test_decision_reconstruction_stops_before_postbid_tokens():
    rows = list(iter_bidding_decisions(_game()))
    assert [r["target_action"] for r in rows] == ["18", "y", "20", "p", "p"]
    assert rows[0]["history_before"] == []
    assert rows[-1]["history_before"] == ["1", "18", "0", "y", "1", "20", "0", "p"]
    assert all("game_type" not in r for r in rows)
    assert all("won" not in r for r in rows)
