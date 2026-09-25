"""Public DHS rules regression cases; these are not an acceptance holdout."""

import hashlib
import json
from pathlib import Path
import runpy

import pytest

from skatai.data.sgf import parse_properties, parse_sgf_line


_ROOT = Path(__file__).parents[1]
_FIXTURE = json.loads(
    (_ROOT / "tests/fixtures/dhs_public_rules_regression_v1.json").read_text()
)
_CHECK_RECORD = runpy.run_path(
    str(_ROOT / "scripts/validate_announced_hs_corpus.py")
)["check_record"]


def test_dhs_regression_scope_is_explicit():
    assert _FIXTURE["schema"] == "skatai.v2.rules.dhs-public-regression.v1"
    assert "not independent acceptance holdout" in _FIXTURE["purpose"]
    assert len(_FIXTURE["cases"]) == 13
    assert sum(case["outcome"] == "loss" for case in _FIXTURE["cases"]) == 5
    assert len({case["semantic_sha256"] for case in _FIXTURE["cases"]}) == 13


@pytest.mark.parametrize(
    "case", _FIXTURE["cases"], ids=lambda case: case["raw_sha256"][:12]
)
def test_public_dhs_result_matches_clean_v2_rules(case):
    raw = case["raw_sgf"].encode()
    assert hashlib.sha256(raw).hexdigest() == case["raw_sha256"]
    source_outcome = set(parse_properties(case["raw_sgf"])["R"].split()) & {
        "win", "loss"
    }
    assert source_outcome == {case["outcome"]}
    parsed = parse_sgf_line("public-dhs-regression", raw)
    assert parsed["classification"] == "PARSED_PLAYED_GAME"
    assert parsed["announcement"] == "DHS" and parsed["play_count"] == 30
    assert parsed["semantic_sha256"] == case["semantic_sha256"]
    checked = _CHECK_RECORD(parsed)
    assert checked["status"] == "MATCH"
    assert checked["computed_value"] == case["signed_game_value"]
    assert checked["computed_matadors"] == case["matadors"]
