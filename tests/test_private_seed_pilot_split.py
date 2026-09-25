import pytest

from scripts.build_private_seed_pilot_split import assign_split


def test_private_seed_split_is_disjoint_balanced_and_deterministic():
    contracts = ("C", "CH", "S", "SH", "H", "HH", "D", "DH", "G", "GH", "N", "NH")
    rows = [{"contract": contract} for contract in contracts for _ in range(8)]
    games = [
        {"ordinal": i, "deal_seed": 2**100 + i, "cardplay_rng_seed": 2**120 + i}
        for i in range(96)
    ]
    parts = assign_split(games, rows)
    assert parts == assign_split(games, rows)
    assert {name: len(indices) for name, indices in parts.items()} == {
        "train": 72, "validation": 12, "test": 12,
    }
    assert sorted(i for indices in parts.values() for i in indices) == list(range(96))
    for contract in contracts:
        assert [sum(rows[i]["contract"] == contract for i in parts[name])
                for name in ("train", "validation", "test")] == [6, 1, 1]
    duplicate = [dict(game) for game in games]
    duplicate[1]["deal_seed"] = duplicate[0]["deal_seed"]
    with pytest.raises(ValueError, match="PRIVATE_SPLIT_DUPLICATE_DEAL_OR_RNG"):
        assign_split(duplicate, rows)
