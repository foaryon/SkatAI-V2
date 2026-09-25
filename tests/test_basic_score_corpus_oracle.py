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


def test_stream_hash_and_both_scored_iss_records():
    records = [parse_sgf_line("oracle-test", _fixtures[name]) for name in ("PLAYED", "LIVE_SPLIT_PICKUP")]
    payload = b"".join((json.dumps(record) + "\n").encode() for record in records)
    expected = hashlib.sha256(payload).hexdigest()
    evidence = validate_stream(io.BytesIO(payload), expected)
    assert evidence["counts"]["played"] == evidence["counts"]["match"] == 2
    assert evidence["counts"]["mismatch"] == 0
    with pytest.raises(ValueError, match="SOURCE_SHA256_MISMATCH"):
        validate_stream(io.BytesIO(payload), "0" * 64)
