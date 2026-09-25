from dataclasses import asdict
import json

import pytest

from skatai.game.bidding import BID_VALUES
from skatai.selfplay.cardplay import RandomLegalPolicy, make_deal
from skatai.selfplay.trajectory import capture_basic_game, declarer_learner_view


class Bid:
    def __init__(self, limit):
        self.limit = limit

    def probability_continue(self, view):
        return float(BID_VALUES[view.bid_index] <= self.limit)


class Declare:
    def __init__(self, pickup):
        self.pickup = pickup

    def choose_contract(self, view):
        if self.pickup and not view.picked_up_skat:
            return "PICKUP"
        return "G" if self.pickup else "GH"


class Discard:
    def choose_discard(self, view):
        return view.hand12[:2]


def captured(seed, pickup, limit=18):
    return capture_basic_game(
        seed, source_commit="a" * 40,
        policy_ids={phase: (phase + "-0", phase + "-1", phase + "-2")
                    for phase in ("BID", "DECLARATION", "DISCARD", "CARDPLAY")},
        bidding_policies=[Bid(limit)] * 3,
        declaration_policies=[Declare(pickup)] * 3,
        discard_policies=[Discard()] * 3,
        cardplay_policies=[RandomLegalPolicy(seed * 10 + seat) for seat in range(3)],
        legal_contracts=("G", "GH"),
    )


@pytest.mark.parametrize("seed", [0, 1, 42])
@pytest.mark.parametrize("pickup", [False, True])
def test_captured_trajectory_has_only_decision_time_private_views(seed, pickup):
    result = captured(seed, pickup)
    assert result == captured(seed, pickup)
    assert result.signed_basic_value != 0
    assert sum(x.phase == "CARDPLAY" for x in result.decisions) == 30
    assert [x.ordinal for x in result.decisions] == list(range(len(result.decisions)))
    deal = make_deal(seed)
    for decision in result.decisions:
        view = decision.observation
        assert "initial_hands" not in view and "final_skat" not in view
        if decision.phase == "BID":
            assert tuple(view["hand"]) == deal.hands[decision.seat]
            assert not set(view["hand"]) & set(deal.skat)
            assert decision.native_bid_action is not None
        else:
            assert decision.native_bid_action is None
        if decision.phase == "DECLARATION" and not view["picked_up_skat"]:
            assert tuple(view["cards"]) == deal.hands[decision.seat]
        if decision.phase == "CARDPLAY" and decision.seat != result.declarer:
            assert view["skat_cards"] == ()
            assert view["open_hand_cards"] == ()
    assert "hands" not in asdict(result) and "final_skat" not in asdict(result)
    assert json.loads(json.dumps(asdict(result)))["deal_sha256"] == deal.identity_sha256


def test_all_pass_trajectory_has_no_terminal_score():
    result = captured(2, False, limit=0)
    assert result.all_pass and result.signed_basic_value is None
    assert result.contract is None and result.declarer is None
    assert len(result.decisions) == 3


def test_missing_policy_identity_fails_before_game():
    with pytest.raises(ValueError, match="POLICY_IDENTITIES_REQUIRED_FOR_ALL_PHASES"):
        capture_basic_game(
            0, source_commit="a" * 40, policy_ids={},
            bidding_policies=[Bid(18)] * 3,
            declaration_policies=[Declare(False)] * 3,
            discard_policies=[Discard()] * 3,
            cardplay_policies=[RandomLegalPolicy(i) for i in range(3)],
            legal_contracts=("G",),
        )


@pytest.mark.parametrize("contracts", [(), ("GHS",), ("GHZ",), ("G", "GHS")])
def test_capture_rejects_unscorable_contracts_before_game(monkeypatch, contracts):
    def unexpected_game(*args, **kwargs):
        raise AssertionError("game must not start")

    monkeypatch.setattr("skatai.selfplay.trajectory.run_game", unexpected_game)
    with pytest.raises(ValueError, match="CAPTURE_REQUIRES_DEFAULT_SCORABLE_CONTRACTS"):
        capture_basic_game(
            0, source_commit="a" * 40,
            policy_ids={phase: (phase + "-0", phase + "-1", phase + "-2")
                        for phase in ("BID", "DECLARATION", "DISCARD", "CARDPLAY")},
            bidding_policies=[Bid(18)] * 3,
            declaration_policies=[Declare(False)] * 3,
            discard_policies=[Discard()] * 3,
            cardplay_policies=[RandomLegalPolicy(i) for i in range(3)],
            legal_contracts=contracts,
        )


def test_learner_export_is_single_seat_and_removes_replay_keys():
    raw = captured(1, True)
    public = declarer_learner_view(
        raw, policy_family_ids={phase: "code-family-" + phase
                                for phase in ("BID", "DECLARATION", "DISCARD", "CARDPLAY")},
    )
    data = asdict(public)
    assert data["schema"] == "skatai.v2.selfplay.learner-seat.v1"
    assert "deal_seed" not in data and "deal_sha256" not in data
    assert "decision_trace_sha256" not in data and "game_id" not in data
    assert all(d["seat"] == data["seat"] for d in data["decisions"])
    assert len(data["decisions"]) < len(raw.decisions)
    assert data["signed_basic_value"] == raw.signed_basic_value
