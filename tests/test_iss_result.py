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


def test_live_result_scores_split_pickup_terminal_sgf():
    from tests.test_sgf import LIVE_SPLIT_PICKUP

    r = live_game_result(LIVE_SPLIT_PICKUP.decode(), viewer_name="SkatAI")
    assert r["seat"] == 0
    assert r["declarer"] == 0
    assert r["winning_bid"] == 48
    assert r["contract"] == "G"
    assert r["score"] == -194.0
    assert r["failure_reason"] is None


TIMEOUT_BEFORE_DISCARD = (
    ";GM[Skat]PC[International Skat Server]CO[]SE[470016]ID[10330141]"
    "DT[2026-09-25/14:03:54/UTC]P0[kermit]P1[SkatAI]P2[theCount]"
    "R0[]R1[0.0]R2[]MV[w SK.C7.CK.S9.SQ.HA.S7.H9.HQ.DT.S8.C8.H8.SJ.SA.C9.CQ.CJ."
    "HJ.D9.D8.CA.DQ.DJ.ST.D7.HK.DK.HT.CT.DA.H7 1 18 0 p 2 20 1 y 2 22 1 y "
    "2 23 1 y 2 24 1 y 2 p 1 s w DA.H7 1 C w TI.1 ]"
    "R[d:1 loss v:-288 m:-11 bidok p:11 t:0 s:0 z:0 p0:0 p1:0 p2:0 l:-1 to:1 r:0] ;)"
)


def test_live_result_preserves_timeout_before_pickup_discard_as_unscored_failure():
    r = live_game_result(TIMEOUT_BEFORE_DISCARD, viewer_name="SkatAI")
    assert r["seat"] == 1
    assert r["declarer"] == 1
    assert r["contract"] == "C"
    assert r["score"] is None
    assert r["failure_reason"] == "ISS_TIMEOUT"
    assert r["timeout_seat"] == 1
