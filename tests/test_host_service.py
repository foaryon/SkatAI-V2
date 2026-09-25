from __future__ import annotations

import pytest

from skatai.runtime.host_service import (
    PICKUP_PLAN_REQUEST_SCHEMA,
    PICKUP_PLAN_RESPONSE_SCHEMA,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    handle_pickup_plan,
    handle_request,
)
from skatai.runtime.interface import SkatAIInterfaceError
from tests.test_product_interface import HAND10, HAND12, _ai


@pytest.mark.parametrize(
    ("decision_type", "observation", "expected"),
    [
        (
            "BID",
            {"hand": HAND10, "actor": 1, "bidder": 1, "answerer": 0,
             "bid_index": 0, "decision_role": "BIDDER"},
            "CONTINUE",
        ),
        (
            "DECLARATION",
            {"cards": HAND10, "seat": 1, "winning_bid": 18,
             "picked_up_skat": False, "legal_contracts": ("G", "C")},
            "G",
        ),
        (
            "DISCARD",
            {"hand12": HAND12, "seat": 1, "winning_bid": 18},
            "S9.ST",
        ),
        (
            "PLAY_CARD",
            {"hand": HAND10, "seat": 1, "declarer": 1, "contract": "G",
             "winning_bid": 18, "current_trick": (), "played_cards": (),
             "legal_cards": ("C7", "C8")},
            "C7",
        ),
    ],
)
def test_host_request_routes_all_product_phases(decision_type, observation, expected):
    payload = {
        "schema": REQUEST_SCHEMA,
        "game_id": "game-1",
        "sequence_no": 1,
        "decision_type": decision_type,
        "observation": observation,
    }
    response = handle_request(_ai(), "release-1", payload)
    assert response["schema"] == RESPONSE_SCHEMA
    assert response["ok"] is True
    assert response["result"]["action"] == expected
    assert response["result"]["release_id"] == "release-1"
    assert len(response["result"]["request_id"]) == 64


def test_host_request_rejects_unknown_schema():
    with pytest.raises(SkatAIInterfaceError, match="HOST_REQUEST_SCHEMA_MISMATCH"):
        handle_request(_ai(), "release-1", {"schema": "unknown"})


def test_host_rejects_caller_actions_outside_observation():
    payload = {
        "schema": REQUEST_SCHEMA,
        "game_id": "game-1",
        "sequence_no": 1,
        "decision_type": "PLAY_CARD",
        "observation": {
            "hand": HAND10, "seat": 1, "declarer": 1, "contract": "G",
            "winning_bid": 18, "current_trick": (), "played_cards": (),
            "legal_cards": ("C7", "C8"),
        },
        "legal_actions": ("C7", "H7"),
    }
    with pytest.raises(SkatAIInterfaceError, match="LEGAL_ACTION_OUTSIDE_OBSERVATION"):
        handle_request(_ai(), "release-1", payload)


def test_host_accepts_reverse_order_legal_discard():
    ai = _ai()
    ai.discard.choose_discard = lambda _observation: ("ST", "S9")
    response = handle_request(ai, "release-1", {
        "schema": REQUEST_SCHEMA,
        "game_id": "game-1", "sequence_no": 1,
        "decision_type": "DISCARD",
        "observation": {"hand12": HAND12, "seat": 1, "winning_bid": 18},
    })
    assert response["result"]["action"] == "S9.ST"


def _pickup_payload():
    return {
        "schema": PICKUP_PLAN_REQUEST_SCHEMA,
        "game_id": "table-7/game-4", "sequence_no": 12,
        "hand12": HAND12, "seat": 1, "winning_bid": 18,
        "max_accepted_bids_by_seat": (0, 18, 0),
        "legal_contracts": ("C", "S", "H", "D", "G", "N", "NO"),
    }


def test_pickup_plan_binds_discard_and_later_declaration_to_one_snapshot():
    ai = _ai()
    plan = handle_pickup_plan(ai, "release-1", _pickup_payload())
    assert plan["schema"] == PICKUP_PLAN_RESPONSE_SCHEMA
    assert plan["contract"] == "G"
    assert plan["discard"] == ["S9", "ST"]
    assert plan["final_hand"] == list(HAND10)
    assert plan["sequence_no"] == 12
    assert len(plan["plan_id"]) == 64
    assert [d["decision_type"] for d in plan["decisions"]] == ["DECLARATION", "DISCARD"]
    assert all(d["release_id"] == "release-1" for d in plan["decisions"])
    # Timing varies; identical legal choices retain the same commitment.
    assert handle_pickup_plan(ai, "release-1", _pickup_payload())["plan_id"] == plan["plan_id"]
    assert handle_pickup_plan(ai, "release-2", _pickup_payload())["plan_id"] != plan["plan_id"]


@pytest.mark.parametrize("change, error", [
    ({"hand12": HAND10}, "BAD_CARD_COUNT"),
    ({"legal_contracts": ("GH",)}, "BAD_PICKUP_CONTRACT_SET"),
    ({"legal_contracts": ("G", "G")}, "BAD_PICKUP_CONTRACT_SET"),
    ({"winning_bid": 77}, "PICKUP_NULL_BELOW_WINNING_BID"),
    ({"winning_bid": 0}, "PICKUP_PLAN_BAD_WINNING_BID"),
    ({"source_context": {"opponent_hand": HAND10}}, "PICKUP_PLAN_UNKNOWN_FIELD"),
])
def test_pickup_plan_rejects_bad_host_context(change, error):
    with pytest.raises(SkatAIInterfaceError, match=error):
        handle_pickup_plan(_ai(), "release-1", {**_pickup_payload(), **change})


def test_pickup_plan_rejects_illegal_policy_choice_before_host_discard():
    ai = _ai()
    ai.declaration.choice = "GH"
    with pytest.raises(SkatAIInterfaceError, match="ILLEGAL_CONTRACT"):
        handle_pickup_plan(ai, "release-1", _pickup_payload())
