import pytest

from skatai.evaluation.skatzero_bidding_baseline import _parse_max_bid


def test_parse_b0_final_bid():
    text = "D 12.3\n{18: 1.0}\n{18: 2.0}\n72\n"
    assert _parse_max_bid(text) == 72


def test_parse_b0_final_bid_rejects_bad_tail():
    with pytest.raises(ValueError):
        _parse_max_bid("not-a-bid\n")


def test_parse_declaration_helpers_are_strict():
    from skatai.evaluation.skatzero_bidding_baseline import _capture_final_line

    final, elapsed = _capture_final_line(lambda: print("noise\nCH"))
    assert final == "CH"
    assert elapsed >= 0.0
