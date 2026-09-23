from skatai.data.bidding import contains_benchmark_bot, iter_bidding_decisions, split_for_game
from skatai.game.bidding import replay


def _game(players=None):
    return {
        "source": "iss",
        "semantic_sha256": "a" * 64,
        "players": players or ["alice", "bob", "carol"],
        "declarer": 2,
        "bid_level": 20,
        "initial_hands": [["C7"] * 10, ["S7"] * 10, ["H7"] * 10],
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
