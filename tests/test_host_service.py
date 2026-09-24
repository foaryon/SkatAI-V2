from __future__ import annotations

import pytest

from skatai.runtime.host_service import REQUEST_SCHEMA, RESPONSE_SCHEMA, handle_request
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
