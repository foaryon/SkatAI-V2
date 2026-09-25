"""The bounded rules window must bind every byte of its registered source."""

import hashlib
import json
from pathlib import Path
import runpy

import pytest

from skatai.data.sgf import parse_sgf_line


ROOT = Path(__file__).parents[1]
VALIDATE_WINDOW = runpy.run_path(
    str(ROOT / "scripts/validate_announced_hs_corpus.py")
)["validate_window"]
SGF_FIXTURES = runpy.run_path(str(ROOT / "tests/test_sgf.py"))


def test_registered_source_hash_covers_bytes_after_selected_window(tmp_path):
    record = parse_sgf_line("oracle-source-hash-test", SGF_FIXTURES["PLAYED"])
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")
    registered = hashlib.sha256(source.read_bytes()).hexdigest()
    result = VALIDATE_WINDOW(
        source, start=0, end=1, plan_sha256="plan", registered_source_sha256=registered,
    )
    assert result["verified_source_sha256"] == registered
    source.write_bytes(source.read_bytes() + b"corrupt suffix\n")
    with pytest.raises(ValueError, match="REGISTERED_SOURCE_HASH_MISMATCH"):
        VALIDATE_WINDOW(
            source, start=0, end=1, plan_sha256="plan", registered_source_sha256=registered,
        )
