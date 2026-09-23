from pathlib import Path

import pytest

import skatai.runtime.skatzero_backend as backend
from skatai.evaluation.skatzero_bidding_baseline import B0DeclarationResult
from skatai.runtime.interface import (
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
    SkatAIInterfaceError,
)


HAND10 = ("CJ","DJ","DA","DK","DQ","D7","C9","HA","HT","HK")
HAND12 = HAND10 + ("CT","ST")


def test_hand_declaration_maps_pickup_without_fabricating_bid_state(monkeypatch):
    def fake(*args, **kwargs):
        return B0DeclarationResult("s", 0.1, 1, "SKAT_OR_HAND_DECL")
    monkeypatch.setattr(backend, "frozen_b0_skat_or_hand", fake)

    policy = backend.FrozenB0DeclarationDiscardPolicy(Path("/tmp/frozen"))
    obs = DeclarationObservation.create(
        HAND10,
        seat=0,
        winning_bid=18,
        picked_up_skat=False,
        legal_contracts=["PICKUP","GH"],
        max_accepted_bids_by_seat=[18,0,0],
    )
    assert policy.choose_contract(obs) == "PICKUP"


def test_pickup_declaration_and_discard_share_upstream_semantics(monkeypatch):
    def fake(*args, **kwargs):
        return B0DeclarationResult("G.CT.ST", 0.1, 1, "DISCARD_AND_DECL")
    monkeypatch.setattr(backend, "frozen_b0_discard_and_decl", fake)

    policy = backend.FrozenB0DeclarationDiscardPolicy(Path("/tmp/frozen"))
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


def test_downstream_policy_refuses_missing_public_bid_state():
    p = backend.FrozenB0DeclarationDiscardPolicy(Path("/tmp/frozen"))
    obs = DeclarationObservation.create(
        HAND10,
        seat=0,
        winning_bid=18,
        picked_up_skat=False,
        legal_contracts=["PICKUP","GH"],
    )
    with pytest.raises(SkatAIInterfaceError, match="MAX_ACCEPTED_BIDS"):
        p.choose_contract(obs)


def test_cardplay_args_match_frozen_api_contract():
    obs = CardplayObservation.create(
        ["CJ","DJ","DA","DT","DQ","D8","C7","SA","S9"],
        seat=0,
        declarer=0,
        contract="D",
        winning_bid=30,
        current_trick=[(1,"HT"),(2,"HA")],
        played_cards=[(1,"HT"),(2,"HA")],
        legal_cards=["D8"],
        points_self=25,
        points_other=0,
        max_accepted_bids_by_seat=[0,0,30],
        skat_cards=["DK","D7"],
        blind_hand=False,
    )
    args = backend._cardplay_args(obs)
    assert args == [
        "CARDPLAY",
        "D",
        "CJ,DJ,DA,DT,DQ,D8,C7,SA,S9",
        "0","25","0","0","30","DK","D7","0","0","??","1HT,2HA",
    ]


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
