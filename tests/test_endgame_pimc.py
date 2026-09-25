from dataclasses import replace

import pytest

from skatai.evaluation.endgame_pimc import choose_endgame_card, sample_legal_worlds
from skatai.game.rules import category, game_type_from_contract
from skatai.selfplay.cardplay import DECK, make_deal, run_cardplay


def _late_view(contract: str, seed: int = 42):
    views = []

    class FirstLegal:
        def play_card(self, observation):
            views.append(observation)
            return observation.legal_cards[0]

    run_cardplay(
        make_deal(seed), contract=contract, declarer=1,
        policies=[FirstLegal(), FirstLegal(), FirstLegal()],
    )
    return next(v for v in views if len(v.played_cards) == 21)


@pytest.mark.parametrize("contract", ["C", "G", "N"])
def test_worlds_use_only_visible_cards_and_respect_public_voids(contract):
    view = _late_view(contract)
    worlds = sample_legal_worlds(view, max_worlds=32, seed=19)
    assert worlds == sample_legal_worlds(view, max_worlds=32, seed=19)
    assert 1 <= len(worlds) <= 32
    played = {card for _, card in view.played_cards}
    game_type = game_type_from_contract(contract)
    voids = [set() for _ in range(3)]
    for i, (seat, card) in enumerate(view.played_cards):
        if i % 3 == 0:
            lead = category(card, game_type)
        elif category(card, game_type) != lead:
            voids[seat].add(lead)
    for hands in worlds:
        assert set(hands[view.seat]) == set(view.hand)
        assert len(set(card for hand in hands for card in hand) | played) == 30
        assert len(set(DECK) - played - set(card for hand in hands for card in hand)) == 2
        for seat in range(3):
            assert not any(category(card, game_type) in voids[seat] for card in hands[seat])


@pytest.mark.parametrize("contract", ["C", "G", "N"])
def test_pimc_returns_legal_deterministic_research_action(contract):
    view = _late_view(contract)
    first = choose_endgame_card(view, max_worlds=16, seed=5)
    assert first == choose_endgame_card(view, max_worlds=16, seed=5)
    assert first["card"] in view.legal_cards
    assert set(first["action_mean_values"]) == set(view.legal_cards)
    assert first["research_only"] is True


def test_pimc_rejects_stale_or_privileged_observation():
    view = _late_view("G")
    with pytest.raises(ValueError, match="UNSUPPORTED_OBSERVATION"):
        sample_legal_worlds(replace(view, known_private_cards=("CA",)))
    with pytest.raises(ValueError, match="HISTORY_MISMATCH"):
        sample_legal_worlds(replace(view, current_trick=((0, "CA"),)))
    with pytest.raises(ValueError, match="WORLD_LIMIT"):
        sample_legal_worlds(view, max_worlds=0)
