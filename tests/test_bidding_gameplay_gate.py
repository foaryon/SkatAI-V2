import pytest

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


def test_checkpoint_roundtrip_and_atomic_write(tmp_path):
    from skatai.evaluation.bidding_gameplay_gate import (
        CHECKPOINT_SCHEMA,
        _atomic_write_json,
        _load_checkpoint,
    )

    path = tmp_path / "gate.checkpoint.json"
    config = {"deal_count": 2, "b1_model_sha256": "a" * 64}
    payload = {
        "schema": CHECKPOINT_SCHEMA,
        "complete": False,
        "configuration": config,
        "selected_deal_identities": ["d1", "d2"],
        "completed_deals": 1,
        "elapsed_s": 12.5,
        "records": [{"deal_identity": "d1", "paired": []}],
    }
    _atomic_write_json(path, payload)
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()

    records, elapsed = _load_checkpoint(
        path,
        configuration=config,
        selected_deal_identities=["d1", "d2"],
    )
    assert list(records) == ["d1"]
    assert elapsed == 12.5


def test_checkpoint_rejects_configuration_mismatch(tmp_path):
    from skatai.evaluation.bidding_gameplay_gate import (
        CHECKPOINT_SCHEMA,
        _atomic_write_json,
        _load_checkpoint,
    )

    path = tmp_path / "gate.checkpoint.json"
    _atomic_write_json(
        path,
        {
            "schema": CHECKPOINT_SCHEMA,
            "configuration": {"deal_count": 1},
            "selected_deal_identities": ["d1"],
            "records": [],
            "elapsed_s": 0.0,
        },
    )
    with pytest.raises(ValueError, match="CHECKPOINT_CONFIGURATION_MISMATCH"):
        _load_checkpoint(
            path,
            configuration={"deal_count": 2},
            selected_deal_identities=["d1"],
        )


def test_deal_set_roundtrip(tmp_path):
    import json
    from skatai.evaluation.bidding_gameplay_gate import DEAL_SET_SCHEMA, load_deal_set

    path = tmp_path / "deals.json"
    path.write_text(
        json.dumps(
            {
                "schema": DEAL_SET_SCHEMA,
                "source": {"sha256": "a" * 64, "bytes": 1, "records": 1},
                "selection": {"split": "test", "seed": 1, "count": 1},
                "deals": [
                    {
                        "game_identity": "1" * 64,
                        "hands": [list(h) for h in HANDS],
                        "skat": ["DK", "DA"],
                    }
                ],
            }
        )
        + "\n"
    )
    deals = load_deal_set(path)
    assert len(deals) == 1
    assert deals[0].game_identity == "1" * 64
    assert deals[0].skat == ("DK", "DA")


def test_gate_requires_exactly_one_deal_source():
    from skatai.evaluation.bidding_gameplay_gate import run_gate

    with pytest.raises(ValueError, match="EXACTLY_ONE_DEAL_SOURCE_REQUIRED"):
        run_gate(
            None,
            None,
            None,
            None,
            deal_count=1,
            selection_seed=1,
            master_seed=1,
            threshold=0.5,
            accuracy=231,
            bid_threshold=-5.0,
            deal_set_path=None,
        )
