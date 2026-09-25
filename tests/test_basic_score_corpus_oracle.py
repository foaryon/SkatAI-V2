import hashlib
import io
import json
from pathlib import Path
import runpy

import pytest

from skatai.data.sgf import parse_sgf_line


_module = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "validate_basic_score_corpus.py"))
validate_stream = _module["validate_stream"]
compare_played_record = _module["compare_played_record"]
_fixtures = runpy.run_path(str(Path(__file__).parent / "test_sgf.py"))


def test_stream_hash_scores_train_and_excludes_external_bot_holdout():
    records = [parse_sgf_line("oracle-test", _fixtures[name]) for name in ("PLAYED", "LIVE_SPLIT_PICKUP")]
    payload = b"".join((json.dumps(record) + "\n").encode() for record in records)
    expected = hashlib.sha256(payload).hexdigest()
    evidence = validate_stream(io.BytesIO(payload), expected)
    assert evidence["counts"]["played"] == 2
    assert evidence["counts"]["match"] == 1
    assert evidence["counts"]["excluded_split"] == 1
    assert len(evidence["played_results"]) == 1
    assert evidence["counts"]["mismatch"] == 0
    altered = dict(records[0], card_points=records[0]["card_points"] + 1)
    corrupted_payload = (json.dumps(altered) + "\n").encode()
    corrupted = validate_stream(
        io.BytesIO(corrupted_payload), hashlib.sha256(corrupted_payload).hexdigest(),
    )
    assert corrupted["counts"]["point_mismatch"] == 1
    assert corrupted["counts"]["match"] == 0
    with pytest.raises(ValueError, match="SOURCE_SHA256_MISMATCH"):
        validate_stream(io.BytesIO(payload), "0" * 64)


def test_oracle_rechecks_ownership_and_follow_suit_on_hash_valid_input():
    record = parse_sgf_line("oracle-test", _fixtures["PLAYED"])
    assert compare_played_record(record)["status"] == "MATCH"
    unowned = dict(record, plays=[list(move) for move in record["plays"]])
    unowned["plays"][0][1] = record["initial_hands"][1][0]
    assert compare_played_record(unowned)["status"] == "INVALID"
    revoke_follow = dict(record, plays=[list(move) for move in record["plays"]])
    revoke_follow["plays"][2][1] = "CJ"  # Seat 2 owns it but must follow spades.
    assert compare_played_record(revoke_follow)["status"] == "INVALID"
    wrong_turn = dict(record, plays=[list(move) for move in record["plays"]])
    wrong_turn["plays"][0], wrong_turn["plays"][1] = wrong_turn["plays"][1], wrong_turn["plays"][0]
    assert compare_played_record(wrong_turn)["status"] == "INVALID"
    broken_deal = dict(record, initial_hands=[list(hand) for hand in record["initial_hands"]])
    broken_deal["initial_hands"][0][0] = broken_deal["initial_hands"][1][0]
    assert compare_played_record(broken_deal)["status"] == "INVALID"
