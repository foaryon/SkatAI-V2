from skatai.iss.bridge import ISSSkatAIDecisionProvider
from skatai.iss.protocol import parse_move_line
from skatai.iss.session import TableSession
from skatai.runtime.decision import DecisionType


HAND0 = ("C7","C8","C9","CT","CJ","CQ","CK","CA","S7","S8")


def deal0():
    return (
        "w " + ".".join(HAND0) + "|"
        + ".".join(("??",) * 10) + "|"
        + ".".join(("??",) * 10) + "|??.??"
    )


def table(*lines):
    t = TableSession("T", "SkatAI", "3", True, in_progress=True)
    t.moves = [parse_move_line(x) for x in lines]
    return t


class AI:
    def __init__(self):
        self.bid = "CONTINUE"
        self.contract = "PICKUP"
        self.discard = ("C7","C8")
        self.card = "S7"

    def decide_bid(self, observation):
        return self.bid

    def choose_contract(self, observation):
        return self.contract

    def choose_discard(self, observation):
        return self.discard

    def play_card(self, observation):
        return self.card


def provider(ai):
    return ISSSkatAIDecisionProvider(ai, release_id="test-release")


def test_decision_provider_bidding_envelope_and_wire():
    ai = AI()
    d = provider(ai).next_decision(table(deal0(), "1 18"))
    assert d.request.decision_type is DecisionType.BID
    assert d.result.action == "CONTINUE"
    assert d.wire_action == "y"


def test_decision_provider_pickup_choice_is_one_effect():
    ai = AI()
    d = provider(ai).next_decision(
        table(deal0(), "1 18", "0 y", "1 p", "2 p")
    )
    assert d.request.decision_type is DecisionType.DECLARATION
    assert d.result.action == "PICKUP"
    assert d.wire_action == "s"


def test_pickup_contract_and_discard_are_two_guardable_half_moves():
    ai = AI()
    ai.contract = "G"
    p = provider(ai)
    before_contract = table(
        deal0(), "1 18", "0 y", "1 p", "2 p",
        "0 s", "w S9.ST",
    )
    d1 = p.next_decision(before_contract)
    assert d1.request.decision_type is DecisionType.DECLARATION
    assert d1.result.action == "G"
    assert d1.wire_action == "G"

    after_contract = table(
        deal0(), "1 18", "0 y", "1 p", "2 p",
        "0 s", "w S9.ST", "0 G",
    )
    d2 = p.next_decision(after_contract)
    assert d2.request.decision_type is DecisionType.DISCARD
    assert d2.result.action == "C7.C8"
    assert d2.wire_action == "C7.C8"


def test_decision_provider_cardplay_is_guardable():
    ai = AI()
    ai.contract = "GH"
    ai.card = "S7"
    d = provider(ai).next_decision(
        table(deal0(), "1 18", "0 y", "1 p", "2 p", "0 GH")
    )
    assert d.request.decision_type is DecisionType.PLAY_CARD
    assert d.result.action == "S7"
    assert d.wire_action == "S7"
