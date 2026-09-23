from pathlib import Path

import pytest

from skatai.evaluation.iss_ledger import ISSGateLedger, ISSGateLedgerRecord


SHA = "a" * 64
COMMIT = "b" * 40


def scored(game_id="g1"):
    return ISSGateLedgerRecord.create(
        game_id=game_id,
        arm="B1",
        opponent="kermit",
        seat=1,
        status="SCORED",
        score=12.5,
        declarer=True,
        contract="G",
        winning_bid=48,
        overbid=False,
        latency_ms_p50=20.0,
        latency_ms_p95=35.0,
        raw_sgf_sha256="c" * 64,
        journal_sha256="d" * 64,
        model_sha256=SHA,
        source_commit=COMMIT,
        recorded_unix_ns=1,
    )


def test_append_and_gate_projection(tmp_path: Path):
    p = tmp_path / "gate.jsonl"
    ledger = ISSGateLedger(p)
    ledger.append(scored())
    rows = ledger.scored_for_gate()
    assert rows == [{
        "arm": "B1",
        "opponent": "kermit",
        "seat": 1,
        "score": 12.5,
        "game_id": "g1",
    }]


def test_duplicate_game_id_is_rejected(tmp_path: Path):
    ledger = ISSGateLedger(tmp_path / "gate.jsonl")
    ledger.append(scored())
    with pytest.raises(ValueError, match="DUPLICATE_GAME_ID"):
        ledger.append(scored())


def test_failure_is_preserved_but_not_strength_scored(tmp_path: Path):
    ledger = ISSGateLedger(tmp_path / "gate.jsonl")
    failure = ISSGateLedgerRecord.create(
        game_id="g2",
        arm="B0",
        opponent="zoot",
        seat=2,
        status="INFRA_FAILURE",
        model_sha256=SHA,
        source_commit=COMMIT,
        failure_reason="connection reset before game result",
        journal_sha256="d" * 64,
        recorded_unix_ns=2,
    )
    ledger.append(failure)
    assert ledger.scored_for_gate() == []
    assert ledger.records()[0].failure_reason is not None


def test_scored_game_cannot_be_marked_with_failure_reason():
    with pytest.raises(ValueError, match="SCORED_CANNOT_HAVE_FAILURE_REASON"):
        ISSGateLedgerRecord.create(
            game_id="g3",
            arm="B1",
            opponent="theCount",
            seat=0,
            status="SCORED",
            score=1,
            model_sha256=SHA,
            source_commit=COMMIT,
            failure_reason="bad",
        )


def test_failure_cannot_leak_into_score():
    with pytest.raises(ValueError, match="FAILURE_RECORD_MUST_NOT_HAVE_SCORE"):
        ISSGateLedgerRecord.create(
            game_id="g4",
            arm="B0",
            opponent="kermit",
            seat=0,
            status="PROTOCOL_FAILURE",
            score=-100,
            model_sha256=SHA,
            source_commit=COMMIT,
            failure_reason="parser mismatch",
        )
