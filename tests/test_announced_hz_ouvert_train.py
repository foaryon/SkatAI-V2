from pathlib import Path
import runpy

import pytest

from skatai.selfplay.cardplay import DECK


candidate_value = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/validate_announced_hz_ouvert_train.py")
)["candidate_value"]


def test_research_schwarz_and_ouvert_levels_and_overbid():
    cards = set(DECK[:12])  # With one top jack.
    assert candidate_value(base="G", mode="HZ", cards=cards, bid=18, tricks=10) == (
        168, 1, 168, True,
    )
    assert candidate_value(base="G", mode="O", cards=cards, bid=18, tricks=9) == (
        -384, 1, 192, False,
    )
    assert candidate_value(base="G", mode="HO", cards=cards, bid=200, tricks=10) == (
        -432, 1, 192, False,
    )


def test_research_rule_rejects_invalid_modes_and_cards():
    with pytest.raises(ValueError, match="UNSUPPORTED_RESEARCH_MODE"):
        candidate_value(base="G", mode="HS", cards=set(DECK[:12]), bid=18, tricks=10)
    with pytest.raises(ValueError, match="INVALID_RESEARCH_INPUT"):
        candidate_value(base="G", mode="HZ", cards=set(DECK[:11]), bid=18, tricks=10)
