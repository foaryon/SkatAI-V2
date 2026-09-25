from pathlib import Path

import pytest

import skatai.runtime.skatzero_backend as backend
from skatai.runtime.interface import (
    BiddingObservation,
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
    SkatAIInterfaceError,
)


HAND10 = ("CJ","DJ","DA","DK","DQ","D7","C9","HA","HT","HK")
HAND12 = HAND10 + ("CT","ST")


def test_b0_bidding_policy_maps_cli_max_bid_to_public_auction(monkeypatch):
    calls = []
    def fake(root, py, args, timeout_s=120.0):
        calls.append(args)
        return ["diagnostic", "72"]
    monkeypatch.setattr(backend, "_run_cli", fake)
    p = backend.FrozenB0BiddingPolicy(Path("/r"), Path("/p"))
    low = BiddingObservation.create(
        HAND10, actor=0, bidder=0, answerer=0, bid_index=0, decision_role="BIDDER"
    )
    high = BiddingObservation.create(
        HAND10, actor=0, bidder=0, answerer=0, bid_index=24, decision_role="BIDDER"
    )
    assert p.probability_continue(low) == 1.0
    assert p.probability_continue(high) == 0.0
    assert len(calls) == 1


def test_hand_declaration_maps_pickup_without_fabricating_bid_state(monkeypatch):
    monkeypatch.setattr(backend, "_run_cli", lambda *a, **k: ["s"])
    policy = backend.FrozenB0DeclarationDiscardPolicy(Path("/r"), Path("/p"))
    obs = DeclarationObservation.create(
        HAND10,
        seat=0,
        winning_bid=18,
        picked_up_skat=False,
        legal_contracts=["PICKUP","GH"],
        max_accepted_bids_by_seat=[18,0,0],
    )
    assert policy.choose_contract(obs) == "PICKUP"


def test_pickup_declaration_and_discard_share_cli_result(monkeypatch):
    calls = []
    def fake(*args, **kwargs):
        calls.append(1)
        return ["G 100.0", "G.CT.ST"]
    monkeypatch.setattr(backend, "_run_cli", fake)

    policy = backend.FrozenB0DeclarationDiscardPolicy(Path("/r"), Path("/p"))
    d = DeclarationObservation.create(
        HAND12,
        seat=0,
        winning_bid=18,
        picked_up_skat=True,
        legal_contracts=["C","S","H","D","G","N","NO"],
        max_accepted_bids_by_seat=[18,0,0],
    )
    assert policy.choose_contract(d) == "G"
    x = DiscardObservation.create(
        HAND12,
        seat=0,
        winning_bid=18,
        max_accepted_bids_by_seat=[18,0,0],
    )
    assert policy.choose_discard(x) == ("CT","ST")
    assert len(calls) == 1


def test_pickup_declaration_falls_back_to_best_ranked_legal_contract(monkeypatch):
    calls = []
    def fake(*args, **kwargs):
        calls.append(1)
        return [
            "N -136.0",
            "C -170.0",
            "S -170.0",
            "H -170.0",
            "D -170.0",
            "NO -182.0",
            "G -195.01",
            "N.DT.DJ",
        ]
    monkeypatch.setattr(backend, "_run_cli", fake)

    policy = backend.FrozenB0DeclarationDiscardPolicy(Path("/r"), Path("/p"))
    d = DeclarationObservation.create(
        ("CT","S7","HK","S9","C7","HJ","HT","DT","C8","DJ","H8","C9"),
        seat=1,
        winning_bid=77,
        picked_up_skat=True,
        legal_contracts=["C","S","H","D","G"],
        max_accepted_bids_by_seat=[72,77,0],
    )

    assert policy.choose_contract(d) == "C"
    x = DiscardObservation.create(
        d.cards,
        seat=1,
        winning_bid=77,
        max_accepted_bids_by_seat=[72,77,0],
    )
    assert policy.choose_discard(x) == ("DT","DJ")
    assert len(calls) == 1


def test_downstream_policy_refuses_missing_public_bid_state():
    p = backend.FrozenB0DeclarationDiscardPolicy(Path("/r"), Path("/p"))
    obs = DeclarationObservation.create(
        HAND10,
        seat=0,
        winning_bid=18,
        picked_up_skat=False,
        legal_contracts=["PICKUP","GH"],
    )
    with pytest.raises(SkatAIInterfaceError, match="MAX_ACCEPTED_BIDS"):
        p.choose_contract(obs)


def smoke_observation():
    return CardplayObservation.create(
        ["CJ","DJ","DA","DT","DQ","D8","C7","SA","S9"],
        seat=0,
        declarer=0,
        contract="D",
        winning_bid=30,
        current_trick=[(1,"HT"),(2,"HA")],
        played_cards=[(1,"HT"),(2,"HA")],
        legal_cards=["CJ","DJ","DA","DT","DQ","D8","C7","SA","S9"],
        points_self=25,
        points_other=0,
        max_accepted_bids_by_seat=[0,0,30],
        skat_cards=["DK","D7"],
        blind_hand=False,
    )


def test_cardplay_args_match_frozen_api_contract():
    args = backend._cardplay_args(smoke_observation())
    assert args == [
        "CARDPLAY",
        "D",
        "CJ,DJ,DA,DT,DQ,D8,C7,SA,S9",
        "0","25","0","0","30","DK","D7","0","0","??","1HT,2HA",
    ]


def test_cardplay_uses_final_cli_decision(monkeypatch):
    monkeypatch.setattr(
        backend,
        "_run_cli",
        lambda *a, **k: ["D8 81.29", "After recursion:", "D8"],
    )
    p = backend.FrozenB0CardplayPolicy(Path("/r"), Path("/p"))
    assert p.play_card(smoke_observation()) == "D8"


def test_cardplay_refuses_missing_points():
    obs = CardplayObservation.create(
        ["C7"],
        seat=0,
        declarer=0,
        contract="D",
        winning_bid=18,
        current_trick=[],
        played_cards=[],
        legal_cards=["C7"],
        max_accepted_bids_by_seat=[18,0,0],
    )
    with pytest.raises(SkatAIInterfaceError, match="POINT_STATE"):
        backend._cardplay_args(obs)


def test_cardplay_maps_absolute_iss_seats_to_relative_skatzero_roles():
    obs = CardplayObservation.create(
        ["C7","C8","C9"],
        seat=2,
        declarer=1,
        contract="G",
        winning_bid=24,
        current_trick=[(0,"H7")],
        played_cards=[(1,"DA"),(2,"D7"),(0,"DT"),(0,"H7")],
        legal_cards=["C7","C8","C9"],
        points_self=21,
        points_other=0,
        max_accepted_bids_by_seat=[18,24,20],
        blind_hand=True,
    )
    args = backend._cardplay_args(obs)
    assert args[3] == "1"
    assert args[11] == "1"
    assert args[13] == "0DA,1D7,2DT,2H7"


class _FakeRunner:
    def __init__(self, lines):
        self.lines = list(lines)
        self.calls = []

    def run(self, args, *, timeout_s):
        self.calls.append((list(args), float(timeout_s)))
        return list(self.lines)


def test_b0_backend_can_use_persistent_runner_without_cli(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("cold CLI must not run when warm runner is supplied")

    monkeypatch.setattr(backend, "_run_cli", forbidden)
    runner = _FakeRunner(["diagnostic", "72"])
    p = backend.FrozenB0BiddingPolicy(
        Path("/r"), Path("/p"), runner=runner
    )
    obs = BiddingObservation.create(
        HAND10, actor=0, bidder=0, answerer=0,
        bid_index=0, decision_role="BIDDER"
    )
    assert p.probability_continue(obs) == 1.0
    assert len(runner.calls) == 1
    assert runner.calls[0][0][0] == "BID"


def test_cardplay_can_use_persistent_runner_without_cli(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("cold CLI must not run when warm runner is supplied")

    monkeypatch.setattr(backend, "_run_cli", forbidden)
    runner = _FakeRunner(["D8 81.29", "After recursion:", "D8"])
    p = backend.FrozenB0CardplayPolicy(
        Path("/r"), Path("/p"), runner=runner
    )
    assert p.play_card(smoke_observation()) == "D8"
    assert len(runner.calls) == 1
    assert runner.calls[0][0][0] == "CARDPLAY"
