from dataclasses import asdict, replace

import pytest

from skatai.evaluation.endgame_labels import label_endgame_observation
from skatai.selfplay.cardplay import make_deal, run_cardplay


def _sample(contract="G"):
    deal = make_deal(42)
    views = []

    class Capture:
        def play_card(self, observation):
            views.append(observation)
            return observation.legal_cards[0]

    run_cardplay(
        deal, contract=contract, declarer=1,
        policies=[Capture(), Capture(), Capture()],
    )
    return deal.hands, views


@pytest.mark.parametrize("contract", ["C", "S", "H", "D", "G", "N"])
def test_label_replays_legal_history_without_hidden_learner_inputs(contract):
    hands, views = _sample(contract)
    view = next(v for v in views if len(v.played_cards) == 21)
    row = label_endgame_observation(view, post_declaration_hands=hands)
    assert row["observation"] == asdict(view)
    assert row["target_card"] in view.legal_cards
    assert set(row["target_action_values"]) == set(view.legal_cards)
    assert "input_sha256" not in row
    assert "post_declaration_hands" not in row
    assert row["training_approved"] is False


def test_label_rejects_early_state_wrong_deal_and_tampered_view():
    hands, views = _sample()
    with pytest.raises(ValueError, match="CARD_BOUND"):
        label_endgame_observation(views[0], post_declaration_hands=hands)
    late = next(v for v in views if len(v.played_cards) == 21)
    wrong = make_deal(43).hands
    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        label_endgame_observation(late, post_declaration_hands=wrong)
    with pytest.raises(ValueError, match="POINT_MISMATCH"):
        label_endgame_observation(
            replace(late, points_self=999), post_declaration_hands=hands,
        )
    with pytest.raises(ValueError, match="UNSUPPORTED_PRIVATE_FIELDS"):
        label_endgame_observation(
            replace(late, known_private_cards=("CA",)), post_declaration_hands=hands,
        )
    with pytest.raises(ValueError, match="OBSERVATION_MISMATCH"):
        label_endgame_observation(
            replace(next(v for v in views if len(v.played_cards) == 22), current_trick=()),
            post_declaration_hands=hands,
        )


def test_pickup_declarer_label_uses_only_legal_known_skat():
    deal = make_deal(17)
    hand12 = (*deal.hands[1], *deal.skat)
    final_skat = hand12[:2]
    final_hand = hand12[2:]
    post_hands = list(deal.hands)
    post_hands[1] = final_hand
    views = []

    class Capture:
        def play_card(self, observation):
            views.append(observation)
            return observation.legal_cards[0]

    run_cardplay(
        deal, contract="G", declarer=1,
        final_hand=final_hand, final_skat=final_skat,
        policies=[Capture(), Capture(), Capture()],
    )
    view = next(v for v in views if v.seat == 1 and len(v.played_cards) >= 21)
    row = label_endgame_observation(view, post_declaration_hands=post_hands)
    assert row["observation"]["skat_cards"] == final_skat
    assert row["target_card"] in view.legal_cards
