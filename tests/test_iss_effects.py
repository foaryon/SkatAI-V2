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


def test_semantic_echo_accepts_ouvert_augmented_discard_only():
    expected = "C7.D8"
    observed = "C7.D8.C8.C9.CT.CJ.CQ.CK.CA.S7.S8.S9"
    assert ISSEffectJournal.wire_actions_equivalent(expected, observed)
    assert not ISSEffectJournal.wire_actions_equivalent("18", "18.C7")


def test_protocol_state_hash_ignores_connection_flags():
    from types import SimpleNamespace
    from skatai.iss.effects import table_state_hash
    from skatai.iss.protocol import parse_move_line

    moves = [parse_move_line("1 18"), parse_move_line("0 y")]
    a = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=moves,
        in_progress=True,
        stopped=False,
        start_payload="old",
        state_payload=None,
    )
    b = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=moves,
        in_progress=False,
        stopped=True,
        start_payload="different",
        state_payload="reconnect state",
    )
    assert table_state_hash(a) == table_state_hash(b)


def test_reconcile_confirms_first_post_intent_move(tmp_path):
    from skatai.iss.protocol import parse_move_line
    from skatai.iss.effects import table_state_hash
    from types import SimpleNamespace

    req, result = request_result()
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    before = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=[parse_move_line("1 18"), parse_move_line("0 y")],
    )
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash=table_state_hash(before),
        table_id="T",
        game_sequence=1,
        protocol_sequence=2,
        wire_action="20",
        outbound_line="table T SkatAI play 20",
    )
    journal.mark_send_returned(effect.effect_id)

    after = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=[
            parse_move_line("1 18"),
            parse_move_line("0 y"),
            parse_move_line("2 20"),
        ],
    )
    outcomes = journal.reconcile_table(after)
    assert outcomes[0]["outcome"] == "CONFIRMED"
    assert journal.pending() == []


def test_reconcile_same_replayed_prefix_stays_pending_without_retry(tmp_path):
    from skatai.iss.protocol import parse_move_line
    from skatai.iss.effects import table_state_hash
    from types import SimpleNamespace

    req, result = request_result()
    table = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=[parse_move_line("1 18"), parse_move_line("0 y")],
    )
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash=table_state_hash(table),
        table_id="T",
        game_sequence=1,
        protocol_sequence=2,
        wire_action="20",
        outbound_line="table T SkatAI play 20",
    )
    journal.mark_send_returned(effect.effect_id)
    outcomes = journal.reconcile_table(table)
    assert outcomes[0]["outcome"] == "PENDING_SAME_STATE"
    assert journal.states()[effect.effect_id].status == "SEND_RETURNED"


def test_reconcile_different_first_move_aborts_stale(tmp_path):
    from skatai.iss.protocol import parse_move_line
    from skatai.iss.effects import table_state_hash
    from types import SimpleNamespace

    req, result = request_result()
    before = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=[parse_move_line("1 18"), parse_move_line("0 y")],
    )
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash=table_state_hash(before),
        table_id="T",
        game_sequence=1,
        protocol_sequence=2,
        wire_action="20",
        outbound_line="table T SkatAI play 20",
    )
    after = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=[
            parse_move_line("1 18"),
            parse_move_line("0 y"),
            parse_move_line("2 p"),
        ],
    )
    outcomes = journal.reconcile_table(after)
    assert outcomes[0]["outcome"] == "ABORTED_STALE"
    assert journal.states()[effect.effect_id].status == "ABORTED_STALE"


def test_effect_state_binds_decision_phase_release_and_latency(tmp_path):
    req, result = request_result()
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    effect, created = journal.begin(
        req,
        result,
        external_state_hash="1" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=0,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    assert created
    assert effect.release_id == "R1"
    assert effect.decision_type == "BID"
    assert effect.latency_ms >= 0.0


def test_reconcile_skips_out_of_order_resign_before_own_echo(tmp_path):
    from skatai.iss.protocol import parse_move_line
    from skatai.iss.effects import table_state_hash
    from types import SimpleNamespace

    req, result = request_result()
    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    before = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=[parse_move_line("0 C7"), parse_move_line("1 C8")],
    )
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash=table_state_hash(before),
        table_id="T",
        game_sequence=1,
        protocol_sequence=2,
        wire_action="DQ",
        outbound_line="table T SkatAI play DQ",
    )
    journal.mark_send_returned(effect.effect_id)

    after = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        moves=[
            parse_move_line("0 C7"),
            parse_move_line("1 C8"),
            parse_move_line("1 RE"),
            parse_move_line("2 DQ"),
        ],
    )
    outcomes = journal.reconcile_table(after)
    assert outcomes[0]["outcome"] == "CONFIRMED"
    assert outcomes[0]["observed_action"] == "DQ"
    assert journal.pending() == []
