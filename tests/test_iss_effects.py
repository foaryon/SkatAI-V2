import json

import pytest

from skatai.iss.effects import (
    ISSAuthorityGuard,
    ISSEffectError,
    ISSEffectJournal,
)
from skatai.runtime.decision import DecisionRequest, DecisionType, decide
from skatai.runtime.interface import BiddingObservation
from test_product_interface import HAND10, _ai


def request_result():
    obs = BiddingObservation.create(
        HAND10,
        actor=1,
        bidder=1,
        answerer=0,
        bid_index=0,
        decision_role="BIDDER",
    )
    req = DecisionRequest.create(
        game_id="ISS:T:1",
        sequence_no=4,
        decision_type=DecisionType.BID,
        observation=obs,
        source="ISS",
        source_context={"table_id": "T", "game_sequence": 1},
    )
    return req, decide(_ai(), req, release_id="R1")


def test_intent_is_durable_before_send_and_unresolved_restart_cannot_blindly_send(tmp_path):
    req, result = request_result()
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    guard = ISSAuthorityGuard(journal)
    effect, created = guard.prepare(
        req,
        result,
        current_external_state_hash="a" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    assert created is True
    assert journal.states()[effect.effect_id].status == "INTENT"

    # Simulated restart: same deterministic effect is found, not duplicated.
    again = ISSEffectJournal(tmp_path / "effects.jsonl")
    again_guard = ISSAuthorityGuard(again)
    effect2, created2 = again_guard.prepare(
        req,
        result,
        current_external_state_hash="a" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    assert effect2.effect_id == effect.effect_id
    assert created2 is False
    with pytest.raises(ISSEffectError, match="REQUIRES_RECONCILIATION"):
        again_guard.send(
            effect2,
            created_now=False,
            send_line=lambda line: None,
            outbound_line="table T SkatAI play 18",
        )


def test_reconcile_same_position_can_authorize_one_retry(tmp_path):
    req, result = request_result()
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    guard = ISSAuthorityGuard(journal)
    effect, _ = guard.prepare(
        req,
        result,
        current_external_state_hash="b" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    journal.authorize_retry(
        effect.effect_id,
        current_external_state_hash="b" * 64,
        same_request_still_pending=True,
        reason="server_go_same_position",
    )
    sent = []
    guard.send(
        journal.states()[effect.effect_id],
        created_now=False,
        send_line=sent.append,
        outbound_line="table T SkatAI play 18",
    )
    assert sent == ["table T SkatAI play 18"]
    state = journal.states()[effect.effect_id]
    assert state.status == "SEND_RETURNED"
    assert state.attempts == 1


def test_server_echo_confirms_matching_pending_effect(tmp_path):
    req, result = request_result()
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash="c" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    journal.mark_send_returned(effect.effect_id)
    confirmed = journal.confirm_observed_action(
        table_id="T",
        game_sequence=1,
        wire_action="18",
    )
    assert confirmed is not None
    assert confirmed.status == "CONFIRMED"
    assert journal.pending() == []


def test_stale_position_cannot_be_retry_authorized(tmp_path):
    req, result = request_result()
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash="d" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    with pytest.raises(ISSEffectError, match="STATE_HASH_CHANGED"):
        journal.authorize_retry(
            effect.effect_id,
            current_external_state_hash="e" * 64,
            same_request_still_pending=True,
            reason="bad",
        )
    state = journal.abort_stale(effect.effect_id, reason="server_position_advanced")
    assert state.status == "ABORTED_STALE"


def test_journal_hash_chain_detects_tampering(tmp_path):
    req, result = request_result()
    path = tmp_path / "effects.jsonl"
    journal = ISSEffectJournal(path)
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash="f" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    journal.mark_send_returned(effect.effect_id)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    rows[0]["wire_action"] = "20"
    path.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    with pytest.raises(ISSEffectError, match="EVENT_HASH_MISMATCH"):
        journal.events()
