from skatai.iss.gameview import ISSPhase, replay_player_view
from skatai.iss.protocol import parse_move_line


def moves(*lines):
    return [parse_move_line(x) for x in lines]


DEAL_FH = (
    "w C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|"
    "??.??.??.??.??.??.??.??.??.??|"
    "??.??.??.??.??.??.??.??.??.??|??.??"
)


def test_official_auction_replay_tracks_per_seat_maxima_and_declarer():
    s = replay_player_view(moves(
        DEAL_FH,
        "1 18", "0 y", "1 20", "0 p", "2 22", "1 p",
    ))
    assert s.phase == ISSPhase.SKAT_OR_HAND_DECL
    assert s.declarer == 2
    assert s.winning_bid == 22
    assert s.max_accepted_bids_by_seat == (18, 20, 22)


def test_hidden_skat_and_discards_do_not_leak_to_defender():
    s = replay_player_view(moves(
        DEAL_FH,
        "1 18", "0 p", "2 p", "1 s",
        "w ??.??",
        "1 G.??.??",
    ))
    assert s.phase == ISSPhase.CARDPLAY
    assert s.declarer == 1
    assert s.known_skat == ()
    assert s.discarded_cards == ()
    assert len(s.hand) == 10


def test_half_declaration_then_discard_is_supported_for_declarer_view():
    deal_mh = (
        "w ??.??.??.??.??.??.??.??.??.??|"
        "C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|"
        "??.??.??.??.??.??.??.??.??.??|??.??"
    )
    s = replay_player_view(moves(
        deal_mh,
        "1 18", "0 p", "2 p", "1 s",
        "w S9.ST",
        "1 G",
        "1 C7.C8",
    ))
    assert s.phase == ISSPhase.CARDPLAY
    assert s.contract == "G"
    assert s.discarded_cards == ("C7", "C8")
    assert len(s.hand) == 10


def test_cardplay_replay_computes_legal_cards_and_public_points():
    deal_mh = (
        "w ??.??.??.??.??.??.??.??.??.??|"
        "C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|"
        "??.??.??.??.??.??.??.??.??.??|??.??"
    )
    s = replay_player_view(moves(
        deal_mh,
        "1 18", "0 p", "2 p", "1 GH",
        "0 H7",
    ))
    assert s.phase == ISSPhase.CARDPLAY
    assert s.to_move == 1
    assert s.current_trick == ((0, "H7"),)
    assert set(s.legal_cards).issubset(set(s.hand))
    assert s.declarer_visible_points == 0
    assert s.defender_points == 0



def test_pickup_null_ouvert_server_echo_reconstructs_discards_and_open_hand():
    deal_mh = (
        "w ??.??.??.??.??.??.??.??.??.??|"
        "S8.CT.SK.CQ.CA.H8.H9.HJ.SJ.C7|"
        "??.??.??.??.??.??.??.??.??.??|??.??"
    )
    s = replay_player_view(moves(
        deal_mh,
        "1 18", "0 p", "2 p", "1 s",
        "w ST.S7",
        "1 NO",
        "1 CQ.CA.H8.H9.HJ.S7.S8.ST.SJ.SK.C7.CT",
    ))
    assert s.phase == ISSPhase.CARDPLAY
    assert s.contract == "NO"
    assert s.discarded_cards == ("CQ", "CA")
    assert set(s.hand) == {"S8", "CT", "SK", "H8", "H9", "HJ", "SJ", "C7", "ST", "S7"}
    assert set(s.open_hand_cards) == set(s.hand)
