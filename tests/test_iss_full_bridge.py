from skatai.iss.bridge import ISSSkatAIDecisionProvider
from skatai.iss.gameview import replay_player_view
from skatai.iss.service import parse_service_line
from skatai.iss.session import TableSession
from skatai.runtime.interface import SkatAI


DEAL_SEAT2 = (
    "??.??.??.??.??.??.??.??.??.??|"
    "??.??.??.??.??.??.??.??.??.??|"
    "HJ.C9.SK.S8.S7.HT.DA.DK.D9.D7|??.??"
)


class Bid:
    def probability_continue(self, obs):
        return 1.0


class DeclarePickupThenGrand:
    def choose_contract(self, obs):
        return "PICKUP" if not obs.picked_up_skat else "G"


class DiscardSkat:
    def choose_discard(self, obs):
        return ("H9", "H8")


class PlayFirst:
    def play_card(self, obs):
        return obs.legal_cards[0]


def ai():
    return SkatAI(Bid(), DeclarePickupThenGrand(), DiscardSkat(), PlayFirst())


def table():
    return TableSession("T", "SkatAI", "3", True, in_progress=True)


def apply(t, move):
    t.apply(parse_service_line(f"table T SkatAI play {move}"))


def auction_to_seat2(t):
    apply(t, f"w {DEAL_SEAT2}")
    apply(t, "1 18")
    apply(t, "0 p")
    apply(t, "2 20")
    apply(t, "1 p")


def test_full_provider_runs_pickup_declaration_discard_as_separate_decisions():
    t = table()
    auction_to_seat2(t)
    p = ISSSkatAIDecisionProvider(ai(), release_id="R-B1")

    d = p.next_decision(t)
    assert d.result.decision_type.value == "DECLARATION"
    assert d.result.action == "PICKUP"
    assert d.wire_action == "s"

    apply(t, "2 s")
    apply(t, "w H9.H8")
    d = p.next_decision(t)
    assert d.result.action == "G"
    assert d.wire_action == "G"
    assert len(d.request.observation.cards) == 12

    apply(t, "2 G")
    d = p.next_decision(t)
    assert d.result.decision_type.value == "DISCARD"
    assert d.result.action == "H9.H8"
    assert d.wire_action == "H9.H8"

    apply(t, "2 H9.H8")
    s = replay_player_view(t.moves)
    assert len(s.hand) == 10
    assert s.discarded_cards == ("H9", "H8")


def test_full_provider_cardplay_uses_canonical_legality_and_public_points():
    t = table()
    auction_to_seat2(t)
    apply(t, "2 s")
    apply(t, "w H9.H8")
    apply(t, "2 G")
    apply(t, "2 H9.H8")
    # Forehand and middlehand lead; seat 2 then acts.
    apply(t, "0 C7")
    apply(t, "1 D7")

    p = ISSSkatAIDecisionProvider(ai(), release_id="R-B1")
    d = p.next_decision(t)
    assert d.result.decision_type.value == "PLAY_CARD"
    assert d.wire_action == "C9"  # seat 2 must follow clubs
    obs = d.request.observation
    assert obs.legal_cards == ("C9",)
    # Declarer knows own discarded skat; those points seed B0's declarer counter.
    assert obs.points_self == 0
    assert obs.points_other == 0
    assert obs.skat_cards == ("H9", "H8")


class HandGrand:
    def choose_contract(self, obs):
        return "GH"


def test_hand_game_skips_skat_and_discard():
    t = table()
    auction_to_seat2(t)
    hand_ai = SkatAI(Bid(), HandGrand(), DiscardSkat(), PlayFirst())
    p = ISSSkatAIDecisionProvider(hand_ai, release_id="R-B0")
    d = p.next_decision(t)
    assert d.result.action == "GH"
    assert d.wire_action == "GH"

    apply(t, "2 GH")
    # Forehand leads, not seat2, so no extra declarer/discard action is produced.
    assert p.next_decision(t) is None


class HandNullOuvert:
    def choose_contract(self, obs):
        return "NHO"


def test_hand_ouvert_wire_reveals_exact_ten_cards_deterministically():
    t = table()
    auction_to_seat2(t)
    hand_ai = SkatAI(Bid(), HandNullOuvert(), DiscardSkat(), PlayFirst())
    d = ISSSkatAIDecisionProvider(hand_ai, release_id="R").next_decision(t)
    assert d is not None
    assert d.wire_action.startswith("NHO.")
    assert len(d.wire_action.split(".")) == 11
