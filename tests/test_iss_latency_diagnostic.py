from skatai.evaluation.iss_latency_diagnostic import summarize
from skatai.iss.effects import EffectState


def _effect(latency_ms, *, status="CONFIRMED", release_id="B0", phase="BID"):
    return EffectState(
        effect_id="x", request_id="r", decision_id="d", position_hash="p",
        external_state_hash="e", table_id="t", game_sequence=1,
        protocol_sequence=1, wire_action="p", outbound_line_sha256="h",
        release_id=release_id, decision_type=phase,
        latency_ms=latency_ms, status=status, attempts=1,
    )


def test_summary_excludes_unconfirmed_decisions_and_separates_release_and_phase():
    result = summarize([
        _effect(10), _effect(70_000),
        _effect(1_000_000, status="INTENT"),
        _effect(20, release_id="B1"),
        _effect(30, phase="PLAY_CARD"),
    ])
    assert result["effect_status_counts"] == {"CONFIRMED": 4, "INTENT": 1}
    assert result["groups"] == [
        {"release_id": "B0", "decision_type": "BID", "count": 2,
         "p50_ms": 10.0, "p95_ms": 70000.0, "p99_ms": 70000.0,
         "max_ms": 70000.0, "at_or_above_60000_ms": 1},
        {"release_id": "B0", "decision_type": "PLAY_CARD", "count": 1,
         "p50_ms": 30.0, "p95_ms": 30.0, "p99_ms": 30.0,
         "max_ms": 30.0, "at_or_above_60000_ms": 0},
        {"release_id": "B1", "decision_type": "BID", "count": 1,
         "p50_ms": 20.0, "p95_ms": 20.0, "p99_ms": 20.0,
         "max_ms": 20.0, "at_or_above_60000_ms": 0},
    ]
