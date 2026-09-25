from skatai.data.sgf import SGFParseError, parse_sgf_line, parse_properties
from skatai.game.rules import replay_tricks
from skatai.selfplay.scoring import score_basic_game


PLAYED = b"(;GM[Skat]PC[Internet Skat Server]CO[]SE[53]ID[9]DT[2007-10-29/04:58:00/UTC]P0[jeff]P1[Montana]P2[vaun]R0[0.0]R1[0.0]R2[0.0]MV[w S7.ST.CA.DT.CQ.S8.C8.D9.HT.SQ.HQ.DK.H9.DJ.HK.HJ.C9.DQ.HA.H8.SA.CJ.CK.DA.H7.C7.S9.CT.SK.D7.SJ.D8 1 18 0 p 2 20 1 p 2 CH 0 SQ 1 H9 2 SA 2 SK 0 ST 1 HK 0 D9 1 DQ 2 DA 2 D7 0 DT 1 DK 0 HT 1 H8 2 H7 0 S8 1 HQ 2 S9 2 C7 0 CA 1 DJ 1 C9 2 CT 0 C8 2 CK 0 CQ 1 HJ 1 HA 2 CJ 0 S7 ]R[d:2 loss v:-96 m:2 bidok p:56 t:5 s:0 z:0 p0:0 p1:0 p2:0 l:-1 to:-1 r:0] ;)"


NO_CONTRACT = b"(;GM[Skat]PC[Internet Skat Server]CO[]SE[50]ID[8]DT[2007-10-29/04:44:01/UTC]P0[Montana]P1[vaun]P2[Ben]R0[null]R1[0.0]R2[null]MV[w HT.ST.DK.HK.CT.CJ.SJ.SA.C8.S7.DQ.H8.HA.CA.DA.SK.HJ.C9.DJ.SQ.S9.H7.C7.DT.H9.S8.CK.D9.D8.CQ.HQ.D7 1 p w TI.2 ]R[d:-1 penalty v:0 m:0 bidok p:0 t:0 s:0 z:0 p0:0 p1:0 p2:1 l:-1 to:2 r:0] ;)"

ALL_PASS = b"(;GM[Skat]PC[ISS]ID[99]DT[2024-07-01/00:00:00/UTC]P0[a]P1[b]P2[c]R0[1]R1[2]R2[3]MV[w C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8.S9.ST.SJ.SQ.SK.SA.H7.H8.H9.HT.HJ.HQ.HK.HA.D7.D8.D9.DT.DJ.DQ.DK.DA 1 p 2 p 0 p w TI.0 ]R[d:-1 penalty v:0 m:0 bidok p:0 t:0 s:0 z:0 p0:0 p1:0 p2:1 l:-1 to:0 r:0] ;)"


def test_parse_properties_handles_basic_node():
    p = parse_properties(PLAYED.decode())
    assert p["GM"] == "Skat"
    assert p["ID"] == "9"
    assert p["P2"] == "vaun"


def test_parse_played_hand_game():
    g = parse_sgf_line("iss", PLAYED)
    assert g["classification"] == "PARSED_PLAYED_GAME"
    assert g["declarer"] == 2
    assert g["bid_level"] == 20
    assert g["game_type"] == "CLUBS"
    assert g["is_hand"] is True
    assert g["discards"] is None
    assert g["play_count"] == 30
    assert g["initial_hands"][0][0] == "S7"
    assert g["skat_initial"] == ["SJ", "D8"]


def test_incomplete_no_contract_is_quarantined_without_implicit_actions():
    g = parse_sgf_line("iss", NO_CONTRACT)
    assert g["classification"] == "QUARANTINED_NO_CONTRACT"
    assert g["raw_bidding_prefix"] == ["1", "p"]
    assert g["semantic_sha256"] is None


def test_explicit_all_pass_is_verified_bidding_evidence():
    g = parse_sgf_line("iss", ALL_PASS)
    assert g["classification"] == "VERIFIED_ALL_PASS"
    assert g["all_pass"] is True
    assert g["bidding_history"] == ["1", "p", "2", "p", "0", "p"]
    assert g["semantic_sha256"]


def test_illegal_follow_suit_is_rejected():
    # Change the first lead from SQ to CQ. In Clubs, CQ is trump. Seat 1
    # still holds trump jacks but attempts H9, so it must be rejected at once.
    bad = PLAYED.replace(b"0 SQ 1 H9 2 SA", b"0 CQ 1 H9 2 SA")
    try:
        parse_sgf_line("iss", bad)
    except SGFParseError as exc:
        assert "FOLLOW_VIOLATION" in str(exc) or "PLAY_NOT_OWNED" in str(exc)
    else:
        raise AssertionError("illegal play must be rejected")


def test_semantic_identity_is_source_and_record_id_agnostic():
    a = parse_sgf_line("iss", PLAYED)
    changed = PLAYED.replace(b"ID[9]", b"ID[9999]").replace(b"R0[0.0]", b"R0[1234.5]")
    b = parse_sgf_line("mirror", changed)
    assert a["raw_sha256"] != b["raw_sha256"]
    assert a["source"] != b["source"]
    assert a["game_id"] != b["game_id"]
    assert a["semantic_sha256"] == b["semantic_sha256"]


def test_parser_preserves_full_source_timestamp():
    g = parse_sgf_line("iss", PLAYED)
    assert g["date"] == "2007-10-29"
    assert g["timestamp_utc"] == "2007-10-29/04:58:00/UTC"


def test_semantic_identity_ignores_pickup_marker_representation():
    from skatai.data.sgf import semantic_identity

    g = parse_sgf_line("iss", PLAYED)
    # Hand game representation has no pickup marker, so construct a normalized
    # equivalent by appending non-action marker tokens that native_pairs ignores.
    equivalent = dict(g)
    equivalent["bidding_history"] = list(g["bidding_history"]) + ["2", "s", "w", "SJ.D8"]
    assert semantic_identity(g) == semantic_identity(equivalent)


def test_contract_vocabulary_preserves_modifiers_without_guessing():
    from skatai.data.sgf import parse_contract_token

    assert parse_contract_token("G") == ("G", "", [])
    assert parse_contract_token("GH") == ("G", "H", [])
    assert parse_contract_token("GHS") == ("G", "HS", [])
    assert parse_contract_token("GHOSZ") == ("G", "HOSZ", [])
    assert parse_contract_token("NHO") == ("N", "HO", [])
    assert parse_contract_token("NOH") == ("NO", "H", [])
    assert parse_contract_token("C.D7.D8") == ("C", "", ["D7", "D8"])
    assert parse_contract_token("C7") is None


def test_hand_and_modifier_fields_are_preserved():
    g = parse_sgf_line("iss", PLAYED)
    assert g["contract_base"] == "C"
    assert g["contract_modifiers"] == "H"
    assert g["is_hand"] is True
    assert g["is_ouvert"] is False


LIVE_SPLIT_PICKUP = (
    b"(;GM[Skat]PC[International Skat Server]CO[]SE[469759]ID[10327743]"
    b"DT[2026-09-23/23:18:34/UTC]P0[SkatAI]P1[zoot]P2[kermit]R0[0.0]R1[]R2[]"
    b"MV[w CJ.H8.SK.H9.DJ.C9.S7.H7.CA.CQ.S8.DT.CK.SQ.ST.HQ.S9.D8.D9.HK.DQ.DK."
    b"D7.HA.SJ.DA.C8.HT.HJ.SA.C7.CT 1 p 2 18 0 y 2 20 0 y 2 22 0 y 2 23 0 y "
    b"2 24 0 y 2 27 0 y 2 30 0 y 2 33 0 y 2 35 0 y 2 36 0 y 2 40 0 y 2 44 "
    b"0 y 2 45 0 y 2 46 0 y 2 48 0 y 2 p 0 s w C7.CT 0 G 0 SK.S7 0 CJ 1 CK "
    b"2 HJ 0 C7 1 HK 2 C8 2 SJ 0 DJ 1 HQ 2 HT 0 H7 1 SQ 2 DQ 0 CT 1 D9 2 SA 0 C9 "
    b"1 S9 2 D7 0 CQ 1 DT 1 ST 2 DA 0 H8 1 S8 2 HA 0 CA 1 D8 2 DK 0 H9 ]"
    b"R[d:0 loss v:-144 m:1 bidok p:12 t:1 s:1 z:0 p0:0 p1:0 p2:0 l:-1 to:-1 r:0] ;)"
)


def test_live_iss_split_pickup_discard_representation_is_normalized():
    g = parse_sgf_line("iss-live", LIVE_SPLIT_PICKUP)
    assert g["classification"] == "PARSED_PLAYED_GAME"
    assert g["declarer"] == 0
    assert g["bid_level"] == 48
    assert g["game_type"] == "GRAND"
    assert g["is_hand"] is False
    assert g["discards"] == ["SK", "S7"]
    assert g["play_count"] == 30
    assert g["game_value"] == -144


def test_basic_scorer_matches_two_independent_iss_result_records():
    for raw in (PLAYED, LIVE_SPLIT_PICKUP):
        parsed = parse_sgf_line("scoring-oracle", raw)
        replay = replay_tricks(
            parsed["plays"], game_type=parsed["game_type"],
            declarer=parsed["declarer"],
        )
        declarer_tricks = sum(
            trick["winner"] == parsed["declarer"]
            for trick in replay["completed_tricks"]
        )
        cards = (*parsed["initial_hands"][parsed["declarer"]], *parsed["skat_initial"])
        result = score_basic_game(
            contract=parsed["contract_base"] + parsed["contract_modifiers"],
            winning_bid=parsed["bid_level"], declarer_cards=cards,
            declarer_points=parsed["card_points"], declarer_tricks=declarer_tricks,
        )
        assert result.signed_game_value == parsed["game_value"]
        assert result.matadors == parsed["matadors"]
