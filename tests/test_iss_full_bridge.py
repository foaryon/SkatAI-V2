from skatai.iss.bridge import next_skat_action
from skatai.iss.service import parse_service_line
from skatai.iss.session import TableSession


SEAT2_HAND = ("HJ","C9","SK","S8","S7","HT","DA","DK","D9","D7")
SEAT0_HAND = ("C7","C8","C9","CT","CJ","CQ","CK","CA","S7","S8")


def deal_for(seat, hand):
    groups = []
    for i in range(3):
        groups.append(".".join(hand if i == seat else ("??",) * 10))
    return "|".join(groups + ["??.??"])


def table_with(lines):
    t = TableSession("T", "SkatAI", "3", True, in_progress=True)
    for line in lines:
        t.apply(parse_service_line("table T SkatAI play " + line))
    return t


AUCTION_SEAT2_WINS = ["1 18", "0 y", "1 p", "2 20", "0 p"]


class Engine:
    def __init__(self, *, bid="CONTINUE", contract="GH", discard=("H9","H8"), card="C7"):
        self.bid = bid
        self.contract = contract
        self.discard = discard
        self.card = card
        self.bid_obs = []
        self.decl_obs = []
        self.discard_obs = []
        self.card_obs = []

    def decide_bid(self, obs):
        self.bid_obs.append(obs)
        return self.bid

    def choose_contract(self, obs):
        self.decl_obs.append(obs)
        return self.contract

    def choose_discard(self, obs):
        self.discard_obs.append(obs)
        return self.discard

    def play_card(self, obs):
        self.card_obs.append(obs)
        return self.card


def test_full_bridge_hand_declaration_for_auction_winner():
    t = table_with(["w " + deal_for(2, SEAT2_HAND), *AUCTION_SEAT2_WINS])
    e = Engine(contract="GH")
    assert next_skat_action(t, e) == "GH"
    obs = e.decl_obs[-1]
    assert obs.seat == 2
    assert obs.winning_bid == 20
    assert obs.max_accepted_bids_by_seat == (18,18,20)
    assert "PICKUP" in obs.legal_contracts


def test_full_bridge_pickup_then_combined_declaration_and_discard():
    t = table_with(["w " + deal_for(2, SEAT2_HAND), *AUCTION_SEAT2_WINS])
    assert next_skat_action(t, Engine(contract="PICKUP")) == "s"

    t.apply(parse_service_line("table T SkatAI play 2 s"))
    assert next_skat_action(t, Engine(contract="G")) is None

    t.apply(parse_service_line("table T SkatAI play w H9.H8"))
    e = Engine(contract="G", discard=("H9","H8"))
    assert next_skat_action(t, e) == "G.H9.H8"
    assert len(e.decl_obs[-1].cards) == 12
    assert set(("H9","H8")).issubset(e.decl_obs[-1].cards)


def test_full_bridge_recovers_split_pickup_declaration():
    t = table_with([
        "w " + deal_for(2, SEAT2_HAND),
        *AUCTION_SEAT2_WINS,
        "2 s",
        "w H9.H8",
        "2 G",
    ])
    e = Engine(discard=("H9","H8"))
    assert next_skat_action(t, e) == "H9.H8"
    assert len(e.discard_obs) == 1


def test_full_bridge_defender_cardplay_uses_public_state_only():
    t = table_with([
        "w " + deal_for(0, SEAT0_HAND),
        *AUCTION_SEAT2_WINS,
        "2 GH",
    ])
    e = Engine(card="C7")
    assert next_skat_action(t, e) == "C7"
    obs = e.card_obs[-1]
    assert obs.seat == 0
    assert obs.declarer == 2
    assert obs.contract == "GH"
    assert obs.points_self == 0
    assert obs.points_other == 0
    assert obs.skat_cards == ()
    assert obs.blind_hand is True


def test_full_bridge_tracks_completed_trick_points_for_defender():
    t = table_with([
        "w " + deal_for(0, SEAT0_HAND),
        *AUCTION_SEAT2_WINS,
        "2 GH",
        "0 CA",
        "1 C7",
        "2 CT",
    ])
    e = Engine(card="C8")
    assert next_skat_action(t, e) == "C8"
    obs = e.card_obs[-1]
    assert obs.points_self == 0
    assert obs.points_other == 21
    assert "CA" not in obs.hand
    assert obs.current_trick == ()


def test_pickup_declarer_cardplay_includes_visible_discard_points():
    t = table_with([
        "w " + deal_for(2, SEAT2_HAND),
        *AUCTION_SEAT2_WINS,
        "2 s",
        "w H9.H8",
        "2 G.DA.H9",
        "0 C7",
        "1 C8",
    ])
    e = Engine(card="C9")
    assert next_skat_action(t, e) == "C9"
    obs = e.card_obs[-1]
    assert obs.declarer == 2
    assert obs.points_self == 11
    assert obs.points_other == 0
    assert obs.skat_cards == ("DA","H9")
    assert "DA" not in obs.hand
    assert "H9" not in obs.hand


def test_other_player_declaration_phase_never_calls_private_ai_methods():
    t = table_with(["w " + deal_for(0, SEAT0_HAND), *AUCTION_SEAT2_WINS])
    e = Engine()
    assert next_skat_action(t, e) is None
    assert e.decl_obs == []
    assert e.discard_obs == []
