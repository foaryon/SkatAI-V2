from scripts.conclude_endgame_pimc_campaign import _same_summary


def test_campaign_summary_comparison_allows_roundoff_but_rejects_changed_evidence():
    expected = {"summary": {"overall": {
        "independent_deal_count": 30,
        "mean_candidate_delta": 0.06666666666666664,
    }}}
    rounded = {"summary": {"overall": {
        "independent_deal_count": 30,
        "mean_candidate_delta": 0.06666666666666667,
    }}}
    altered = {"summary": {"overall": {
        "independent_deal_count": 30,
        "mean_candidate_delta": 0.067,
    }}}
    assert _same_summary(expected, rounded)
    assert not _same_summary(expected, altered)
    assert not _same_summary(expected, {"summary": {}})
