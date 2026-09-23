from skatai.evaluation.iss_result import (
    ISS_DEFENDER_LOSS_BONUS_3P,
    ISS_DECLARER_BONUS,
    iss_score_for_seat,
    live_game_result,
)


def _played(value, declarer=2, penalties=(0,0,0)):
    return {
        "classification": "PARSED_PLAYED_GAME",
        "declarer": declarer,
        "game_value": value,
        "penalties": list(penalties),
    }


def test_server_score_reproduces_official_three_player_arithmetic():
    assert iss_score_for_seat(_played(48), 2) == 48 + ISS_DECLARER_BONUS
    assert iss_score_for_seat(_played(-96), 2) == -96 - ISS_DECLARER_BONUS
    assert iss_score_for_seat(_played(48), 0) == 0
    assert iss_score_for_seat(_played(-96), 0) == ISS_DEFENDER_LOSS_BONUS_3P


def test_live_result_parses_real_reference_sgf():
    from tests.test_sgf import PLAYED

    r = live_game_result(PLAYED.decode(), viewer_name="jeff")
    assert r["seat"] == 0
    assert r["declarer"] == 2
    assert r["score"] == 40.0
    assert r["failure_reason"] is None
    assert len(r["game_id"]) == 64
    assert len(r["raw_sgf_sha256"]) == 64


def test_player_group_suffix_can_resolve_when_unique():
    from tests.test_sgf import PLAYED

    sgf = PLAYED.decode().replace("P0[jeff]", "P0[SkatAI:2]")
    r = live_game_result(sgf, viewer_name="SkatAI")
    assert r["seat"] == 0
