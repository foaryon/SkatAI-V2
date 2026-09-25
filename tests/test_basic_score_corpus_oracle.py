import hashlib
import io
import json
from pathlib import Path
import runpy

import pytest

from skatai.data.sgf import parse_sgf_line


_module = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "validate_basic_score_corpus.py"))
validate_stream = _module["validate_stream"]
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
