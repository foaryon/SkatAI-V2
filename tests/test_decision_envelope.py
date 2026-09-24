import pytest

from skatai.runtime.decision import (
    DecisionRequest,
    DecisionResult,
    DecisionType,
    decide,
)
from skatai.runtime.interface import (
    BiddingObservation,
    DiscardObservation,
    SkatAIInterfaceError,
)
from tests.test_product_interface import HAND10, HAND12, _ai


def test_request_identity_is_stable_and_state_bound():
    obs = BiddingObservation.create(
        HAND10,
        actor=1,
        bidder=1,
        answerer=0,
        bid_index=0,
        decision_role="BIDDER",
    )
    a = DecisionRequest.create(
        game_id="G1",
        sequence_no=7,
        decision_type=DecisionType.BID,
        observation=obs,
        source="ISS",
        source_context={"table_id": "T", "game_sequence": 2},
    )
    b = DecisionRequest.create(
        game_id="G1",
        sequence_no=7,
        decision_type="BID",
        observation=obs,
        source="ISS",
        source_context={"game_sequence": 2, "table_id": "T"},
    )
    assert a.request_id == b.request_id
    assert a.position_hash == b.position_hash

    c = DecisionRequest.create(
        game_id="G1",
        sequence_no=8,
        decision_type="BID",
        observation=obs,
        source="ISS",
        source_context={"game_sequence": 2, "table_id": "T"},
    )
    assert c.position_hash != a.position_hash


def test_generic_decision_dispatches_and_binds_result_to_request():
    obs = BiddingObservation.create(
        HAND10,
        actor=1,
        bidder=1,
        answerer=0,
        bid_index=0,
        decision_role="BIDDER",
    )
    req = DecisionRequest.create(
        game_id="G",
        sequence_no=1,
        decision_type="BID",
        observation=obs,
        source="TEST",
    )
    result = decide(_ai(), req, release_id="test-release")
    assert result.action == "CONTINUE"
    assert result.request_id == req.request_id
    assert result.position_hash == req.position_hash
    assert result.release_id == "test-release"
    assert result.latency_ms >= 0


def test_discard_actions_are_explicit_legal_pairs():
    obs = DiscardObservation.create(HAND12, seat=1, winning_bid=18)
    req = DecisionRequest.create(
        game_id="G",
        sequence_no=2,
        decision_type="DISCARD",
        observation=obs,
        source="TEST",
    )
    assert len(req.legal_actions) == 66
    result = decide(_ai(), req, release_id="test-release")
    assert result.action == "S9.ST"
    assert result.action in req.legal_actions


def test_result_rejects_action_not_authorized_by_request():
    obs = BiddingObservation.create(
        HAND10,
        actor=1,
        bidder=1,
        answerer=0,
        bid_index=0,
        decision_role="BIDDER",
    )
    req = DecisionRequest.create(
        game_id="G",
        sequence_no=1,
        decision_type="BID",
        observation=obs,
        source="TEST",
    )
    with pytest.raises(SkatAIInterfaceError, match="ACTION_NOT_LEGAL"):
        DecisionResult.create(
            req,
            action="18",
            release_id="x",
            latency_ms=1.0,
        )
