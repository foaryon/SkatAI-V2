from __future__ import annotations

from dataclasses import replace

import pytest

from skatai.game.rules import game_type_from_contract, legal_cards, replay_tricks
from skatai.selfplay.cardplay import RandomLegalPolicy, make_deal, run_cardplay


@pytest.mark.parametrize("contract", ["C", "S", "H", "D", "G", "N"])
@pytest.mark.parametrize("seed", [0, 1, 42, 20260925])
def test_deterministic_legal_hand_game_cardplay(contract, seed):
    def episode():
        return run_cardplay(
            make_deal(seed), contract=contract, declarer=1,
            policies=[RandomLegalPolicy(seed * 3 + 10 + seat) for seat in range(3)],
        )

    first = episode()
    assert first == episode()
    assert len(first.plays) == 30
    assert len(first.trick_winners) == 10
    assert first.declarer_final_points + first.defender_trick_points == 120
    replayed = replay_tricks(
        first.plays, game_type=game_type_from_contract(contract), declarer=1
    )
    assert len(replayed["completed_tricks"]) == 10
    assert replayed["declarer_trick_points"] == first.declarer_trick_points


def test_policy_observation_keeps_hidden_deal_private_and_illegal_move_fails():
    class InspectPolicy:
        def __init__(self):
            self.calls = 0

        def play_card(self, view):
            self.calls += 1
            assert view.skat_cards == ()
            assert view.open_hand_cards == ()
            assert view.known_private_cards == ()
            assert set(view.legal_cards).issubset(set(view.hand))
            return view.legal_cards[0]

    policies = [InspectPolicy() for _ in range(3)]
    run_cardplay(make_deal(1), contract="G", declarer=0, policies=policies)
    assert [policy.calls for policy in policies] == [10, 10, 10]

    class IllegalPolicy:
        def play_card(self, view):
            return "XX"

    with pytest.raises(ValueError, match="ILLEGAL_POLICY_CARD"):
        run_cardplay(
            make_deal(1), contract="G", declarer=0,
            policies=[IllegalPolicy(), InspectPolicy(), InspectPolicy()],
        )

    with pytest.raises(ValueError, match="DEAL_IDENTITY_MISMATCH"):
        run_cardplay(
            replace(make_deal(1), identity_sha256="wrong"),
            contract="G", declarer=0, policies=policies,
        )
