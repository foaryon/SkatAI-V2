from skatai.iss.bridge import next_action
from skatai.iss.protocol import parse_move_line
from skatai.iss.session import TableSession


DEAL_FH = (
    "w C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|"
    "??.??.??.??.??.??.??.??.??.??|"
    "??.??.??.??.??.??.??.??.??.??|??.??"
)


class Engine:
    def __init__(self):
        self.contract = "PICKUP"
        self.discard = ("C7", "C8")
        self.card = "S7"

    def decide_bid(self, observation):
        return "CONTINUE"

    def choose_contract(self, observation):
        return self.contract

    def choose_discard(self, observation):
        return self.discard

    def play_card(self, observation):
        assert self.card in observation.legal_cards
        return self.card


def table(*lines):
    t = TableSession("T", "bot", "3", True, in_progress=True)
    t.moves = [parse_move_line(x) for x in lines]
    return t


def test_bridge_answerer_continue_maps_to_yes():
    e = Engine()
    assert next_action(table(DEAL_FH, "1 18"), e) == "y"


def test_bridge_pre_skat_pickup_action():
    e = Engine()
    t = table(DEAL_FH, "1 18", "0 y", "1 p", "2 p")
    assert next_action(t, e) == "s"


def test_bridge_pickup_combines_declaration_and_discard():
    e = Engine()
    e.contract = "G"
    t = table(
        DEAL_FH,
        "1 18", "0 y", "1 p", "2 p",
        "0 s", "w S9.ST",
    )
    assert next_action(t, e) == "G.C7.C8"


def test_bridge_cardplay_uses_stable_product_surface():
    e = Engine()
    e.contract = "GH"
    e.card = "S7"
    t = table(DEAL_FH, "1 18", "0 y", "1 p", "2 p", "0 GH")
    assert next_action(t, e) == "S7"
