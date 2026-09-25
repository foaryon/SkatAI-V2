"""Public DHS rules regression cases; these are not an acceptance holdout."""

import hashlib
import json
from pathlib import Path
import runpy

import pytest

from skatai.data.bidding import split_for_game
from skatai.data.sgf import parse_properties, parse_sgf_line


_ROOT = Path(__file__).parents[1]
_FIXTURE = json.loads(
    (_ROOT / "tests/fixtures/dhs_public_rules_regression_v2.json").read_text()
)
_EXPOSURE = json.loads(
    (_ROOT / "provenance/DHS_PUBLIC_HOLDOUT_EXPOSURE_20260925.json").read_text()
)
_EXPOSURE_SHA256 = hashlib.sha256(
    (_ROOT / "provenance/DHS_PUBLIC_HOLDOUT_EXPOSURE_20260925.json").read_bytes()
).hexdigest()
_CHECK_RECORD = runpy.run_path(
    str(_ROOT / "scripts/validate_announced_hs_corpus.py")
)["check_record"]


def test_dhs_regression_scope_is_explicit():
    assert _FIXTURE["schema"] == "skatai.v2.rules.dhs-public-regression.v2"
    assert "not an independent acceptance holdout" in _FIXTURE["purpose"]
    assert len(_FIXTURE["cases"]) == 3
    assert sum(case["outcome"] == "loss" for case in _FIXTURE["cases"]) == 1
    assert len({case["semantic_sha256"] for case in _FIXTURE["cases"]}) == 3
    assert _FIXTURE["exposure_correction_sha256"] == _EXPOSURE_SHA256
    exposed = {row["semantic_sha256"] for row in _EXPOSURE["exposed_identities"]}
    assert _EXPOSURE["exposed_identity_count"] == len(exposed) == 18
    assert not exposed.intersection(
        case["semantic_sha256"] for case in _FIXTURE["cases"]
    )


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
    assert split_for_game(parsed) == "train"
    assert parsed["classification"] == "PARSED_PLAYED_GAME"
    assert parsed["announcement"] == "DHS" and parsed["play_count"] == 30
    assert parsed["semantic_sha256"] == case["semantic_sha256"]
    checked = _CHECK_RECORD(parsed)
    assert checked["status"] == "MATCH"
    assert checked["computed_value"] == case["signed_game_value"]
    assert checked["computed_matadors"] == case["matadors"]
