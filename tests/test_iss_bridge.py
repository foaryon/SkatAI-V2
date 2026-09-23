from skatai.iss.bridge import next_bidding_action
from skatai.iss.client import ISSClientCore
from skatai.iss.service import parse_service_line
from skatai.iss.session import TableSession


DEAL_SEAT2 = (
    "??.??.??.??.??.??.??.??.??.??|"
    "??.??.??.??.??.??.??.??.??.??|"
    "HJ.C9.SK.S8.S7.HT.DA.DK.D9.D7|??.??"
)


class Engine:
    def __init__(self, decision):
        self.decision = decision
        self.observations = []

    def decide_bid(self, observation):
        self.observations.append(observation)
        return self.decision


def _table():
    return TableSession("T", "SkatAIV2", "3", True, in_progress=True)


def test_bridge_derives_seat_and_next_offer_from_public_history():
    t = _table()
    for line in [
        f"table T SkatAIV2 play w {DEAL_SEAT2}",
        "table T SkatAIV2 play 1 18",
        "table T SkatAIV2 play 0 y",
        "table T SkatAIV2 play 1 p",
    ]:
        t.apply(parse_service_line(line))

    e = Engine("CONTINUE")
    assert next_bidding_action(t, e) == "20"
    obs = e.observations[-1]
    assert obs.actor == 2
    assert obs.bidder == 2
    assert obs.answerer == 0
    assert obs.hand == ("HJ","C9","SK","S8","S7","HT","DA","DK","D9","D7")


def test_bridge_maps_answerer_continue_to_yes_and_pass_to_p():
    deal0 = (
        "C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|"
        "??.??.??.??.??.??.??.??.??.??|"
        "??.??.??.??.??.??.??.??.??.??|??.??"
    )
    t = _table()
    for line in [
        f"table T SkatAIV2 play w {deal0}",
        "table T SkatAIV2 play 1 18",
    ]:
        t.apply(parse_service_line(line))
    assert next_bidding_action(t, Engine("CONTINUE")) == "y"
    assert next_bidding_action(t, Engine("PASS")) == "p"


def test_bridge_returns_none_when_other_seat_moves():
    t = _table()
    t.apply(parse_service_line(f"table T SkatAIV2 play w {DEAL_SEAT2}"))
    assert next_bidding_action(t, Engine("CONTINUE")) is None


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.authenticated_client_id = "SkatAIV2"
    def send_line(self, line): self.sent.append(line)
    def connect(self): pass
    def login(self, password): return "SkatAIV2"
    def read_line(self): raise EOFError
    def close(self): pass


class Provider:
    def __init__(self, engine):
        self.engine = engine
    def next_action(self, table):
        return next_bidding_action(table, self.engine)


def test_client_dispatches_one_idempotent_bidding_action_for_same_state():
    tr = FakeTransport()
    c = ISSClientCore(tr, move_provider=Provider(Engine("CONTINUE")))
    c.state.set_connected("SkatAIV2")
    c.handle_line("create T SkatAIV2 3")
    c.handle_line("table T SkatAIV2 start x")
    c.handle_line(f"table T SkatAIV2 play w {DEAL_SEAT2}")
    c.handle_line("table T SkatAIV2 play 1 18")
    c.handle_line("table T SkatAIV2 play 0 y")
    c.handle_line("table T SkatAIV2 play 1 p")
    plays = [x for x in tr.sent if x == "table T SkatAIV2 play 20"]
    assert len(plays) == 1

    # A non-state-changing go event cannot cause a duplicate send.
    c.handle_line("table T SkatAIV2 go")
    plays = [x for x in tr.sent if x == "table T SkatAIV2 play 20"]
    assert len(plays) == 1


def test_decision_provider_emits_stable_request_result_and_wire_action():
    from skatai.iss.bridge import ISSBiddingDecisionProvider
    from test_product_interface import _ai

    t = _table()
    for line in [
        f"table T SkatAIV2 play w {DEAL_SEAT2}",
        "table T SkatAIV2 play 1 18",
        "table T SkatAIV2 play 0 y",
        "table T SkatAIV2 play 1 p",
    ]:
        t.apply(parse_service_line(line))

    p = ISSBiddingDecisionProvider(_ai(), release_id="R-B1")
    d1 = p.next_decision(t)
    d2 = p.next_decision(t)
    assert d1 is not None and d2 is not None
    assert d1.request.request_id == d2.request.request_id
    assert d1.result.decision_id == d2.result.decision_id
    assert d1.result.action == "CONTINUE"
    assert d1.wire_action == "20"
    assert d1.request.source == "ISS"
    assert d1.request.source_context["table_id"] == "T"
